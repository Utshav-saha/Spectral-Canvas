"""Glue between the HTTP layer and the pure spectral/ library."""

import numpy as np
from PIL import Image
import io

from spectral.encoder.audio_encoder import encode
from spectral.input.image_preprocessor import (
    decode_data_url, activation_to_png_bytes
)
from spectral.text import text_codec
from spectral.common.wav_container import write_wav_bytes, read_wav_bytes
from spectral.decoder.image_reconstructor import (
    reconstruct, to_png_bytes, mean_absolute_error, mse, psnr
)
from spectral.analysis import waveform as wf
from app.config import PHONE_DIGITS, PIN_MIN, PIN_MAX, MAX_UPLOAD_BYTES

# Text is sent as 16-tone MFSK (see spectral/text/text_codec.py), not drawn as a
# picture. One byte = two 4-bit symbols = two tones.
TEXT_SAMPLE_RATE = 44100
TEXT_SYMBOL_SECONDS = 0.05
TEXT_F_MIN, TEXT_F_MAX = 2000, 5000
TEXT_BITS = 4


def validate_credentials(caller, receiver, pin):
    for label, value in (("Caller number", caller), ("Receiver number", receiver)):
        if not value or not value.isdigit() or len(value) != PHONE_DIGITS:
            raise ValueError(f"{label} must be exactly {PHONE_DIGITS} digits.")
    if not pin or not pin.isdigit() or not (PIN_MIN <= len(pin) <= PIN_MAX):
        raise ValueError(f"PIN must be {PIN_MIN}-{PIN_MAX} digits.")


def build_source_image(params, upload_bytes):
    """source_type -> a PIL image ready for the encoder."""
    if params.source_type == "doodle":
        if not params.data_url:
            raise ValueError("The canvas is empty. Draw something first.")
        return Image.open(io.BytesIO(decode_data_url(params.data_url)))

    if not upload_bytes:
        raise ValueError("Choose an image file to send.")
    return Image.open(io.BytesIO(upload_bytes))


def run_encode_text(params):
    if not params.text or not params.text.strip():
        raise ValueError("Enter some text, or switch to file upload.")
    if params.security_enabled:
        raise ValueError("Locking is not available for text yet. Turn the lock off to send it.")

    message_bytes = params.text.encode("utf-8")
    # every byte costs two symbols of 16-bit audio; the file must still fit the
    # Receive page's upload limit
    bytes_per_char = 2 * int(TEXT_SAMPLE_RATE * TEXT_SYMBOL_SECONDS) * 2
    max_bytes = (MAX_UPLOAD_BYTES - 4096) // bytes_per_char
    if len(message_bytes) > max_bytes:
        raise ValueError(f"That message is too long to send as tones. Keep it under "
                         f"{max_bytes} characters of plain text.")

    data = text_codec.text_to_data(params.text)
    freqs = text_codec.bits_to_frequencies(
        data, fs=TEXT_SAMPLE_RATE, Ts=TEXT_SYMBOL_SECONDS,
        f_min=TEXT_F_MIN, f_max=TEXT_F_MAX, bits=TEXT_BITS,
    )
    audio = text_codec.transmit(freqs, fs=TEXT_SAMPLE_RATE, Ts=TEXT_SYMBOL_SECONDS)

    metadata = {
        "kind": "text",
        "scheme": "mfsk",
        "sample_rate": TEXT_SAMPLE_RATE,
        "frame_duration": TEXT_SYMBOL_SECONDS,
        "frame_samples": int(TEXT_SAMPLE_RATE * TEXT_SYMBOL_SECONDS),
        "f_min": TEXT_F_MIN, "f_max": TEXT_F_MAX,
        "bits_per_symbol": TEXT_BITS,
        "tones": 2 ** TEXT_BITS,
        "symbols": len(freqs),
        "bytes": len(message_bytes),
        "characters": len(params.text),
        # rows/columns keep the response shape the frontend already reads
        "rows": 2 ** TEXT_BITS,
        "columns": len(freqs),
        "mode": "text",
        "security_enabled": False,
        "duration_seconds": round(len(audio) / TEXT_SAMPLE_RATE, 3),
    }

    return {
        "audio": audio,
        "metadata": metadata,
        "activation": None,
        "text": params.text,
        "wav_bytes": write_wav_bytes(TEXT_SAMPLE_RATE, audio, metadata),
        "preview_png": None,
        "stats": wf.global_stats(audio, TEXT_SAMPLE_RATE),
    }


def run_decode_text(audio, metadata, source_text=None):
    fs = metadata["sample_rate"]
    Ts = metadata["frame_duration"]
    f_min, f_max = metadata["f_min"], metadata["f_max"]
    bits = metadata.get("bits_per_symbol", TEXT_BITS)

    # trailing padding would give an odd symbol count, and symbols come in pairs
    symbols = metadata.get("symbols")
    samples = int(fs * Ts)
    if symbols:
        audio = audio[:symbols * samples]

    freqs = text_codec.receive(audio, fs=fs, Ts=Ts)
    freqs = freqs[:len(freqs) - len(freqs) % 2]
    data = text_codec.frequencies_to_bits(freqs, fs=fs, Ts=Ts,
                                          f_min=f_min, f_max=f_max, bits=bits)
    try:
        text = text_codec.data_to_text(data)
    except UnicodeDecodeError:
        # a damaged file can split a multi-byte character; show what survived
        text = bytes((int(u) << 4) | int(l) for u, l in data).decode("utf-8", errors="replace")

    metrics = None
    if source_text is not None:
        matched = sum(a == b for a, b in zip(text, source_text))
        metrics = {"characters": len(source_text),
                   "matched": matched,
                   "exact": text == source_text}

    return {"text": text, "decrypted": False, "metrics": metrics}


def run_encode(params, upload_bytes=None):
    if params.source_type == "text":
        return run_encode_text(params)

    if params.security_enabled:
        validate_credentials(params.caller, params.receiver, params.pin)
    if params.f_max >= params.sample_rate / 2:
        raise ValueError("Top frequency must stay below half the sample rate.")
    if params.f_min >= params.f_max:
        raise ValueError("Top frequency must be higher than the bottom frequency.")

    image = build_source_image(params, upload_bytes)

    audio, metadata, activation = encode(
        image,
        target_width=params.target_width, target_height=params.target_height,
        sample_rate=params.sample_rate, f_min=params.f_min, f_max=params.f_max,
        frame_duration=params.frame_duration, gray_levels=params.gray_levels,
        security_enabled=params.security_enabled, caller=params.caller,
        receiver=params.receiver, pin=params.pin, alpha=params.alpha,
        mode=params.mode,
    )

    wav_bytes = write_wav_bytes(metadata["sample_rate"], audio, metadata)
    preview_png = activation_to_png_bytes(activation, params.gray_levels)

    return {
        "audio": audio,
        "metadata": metadata,
        "activation": activation,
        "text": None,
        "wav_bytes": wav_bytes,
        "preview_png": preview_png,
        "stats": wf.global_stats(audio, metadata["sample_rate"]),
    }


def run_inspect(raw_bytes):
    sample_rate, audio, metadata = read_wav_bytes(raw_bytes)
    return {
        "sample_rate": sample_rate,
        "audio": audio,
        "metadata": metadata,
        "stats": wf.global_stats(audio, sample_rate),
    }


def run_decode(audio, metadata, caller=None, receiver=None, pin=None,
               source_activation=None, source_text=None):
    if metadata.get("kind") == "text":
        return run_decode_text(audio, metadata, source_text)

    encrypted = bool(metadata.get("security_enabled", False))
    if encrypted:
        validate_credentials(caller, receiver, pin)

    image_array = reconstruct(audio, metadata, caller=caller, receiver=receiver,
                              pin=pin, decrypt_enabled=encrypted)

    metrics = None
    if source_activation is not None:
        from spectral.decoder.image_reconstructor import to_image_array
        source = to_image_array(source_activation, metadata.get("gray_levels", 16))
        if source.shape == image_array.shape:
            metrics = {
                "mae": round(mean_absolute_error(image_array, source), 4),
                "mse": round(mse(image_array, source), 4),
                "psnr": round(psnr(image_array, source), 2),
            }

    return {"image_array": image_array, "png": to_png_bytes(image_array),
            "decrypted": encrypted, "metrics": metrics}


def waveform_payload(audio, sample_rate, buckets=1200):
    return {
        "envelope": wf.peak_envelope(audio, buckets),
        "buckets": wf.bucket_stats(audio, sample_rate, buckets),
        "stats": wf.global_stats(audio, sample_rate),
    }
