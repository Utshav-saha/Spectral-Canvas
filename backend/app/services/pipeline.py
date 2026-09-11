"""Glue between the HTTP layer and the pure spectral/ library."""

import numpy as np
from PIL import Image
import io

from spectral.encoder.audio_encoder import encode
from spectral.input.image_preprocessor import (
    render_text_image, decode_data_url, activation_to_png_bytes
)
from spectral.common.wav_container import write_wav_bytes, read_wav_bytes
from spectral.decoder.image_reconstructor import (
    reconstruct, to_png_bytes, mean_absolute_error, mse, psnr
)
from spectral.analysis import waveform as wf
from app.config import PHONE_DIGITS, PIN_MIN, PIN_MAX


def validate_credentials(caller, receiver, pin):
    for label, value in (("Caller number", caller), ("Receiver number", receiver)):
        if not value or not value.isdigit() or len(value) != PHONE_DIGITS:
            raise ValueError(f"{label} must be exactly {PHONE_DIGITS} digits.")
    if not pin or not pin.isdigit() or not (PIN_MIN <= len(pin) <= PIN_MAX):
        raise ValueError(f"PIN must be {PIN_MIN}-{PIN_MAX} digits.")


def build_source_image(params, upload_bytes):
    """source_type -> a PIL image ready for the encoder."""
    if params.source_type == "text":
        if not params.text or not params.text.strip():
            raise ValueError("Enter some text, or switch to file upload.")
        return render_text_image(params.text, params.target_width, params.target_height)

    if params.source_type == "doodle":
        if not params.data_url:
            raise ValueError("The canvas is empty. Draw something first.")
        return Image.open(io.BytesIO(decode_data_url(params.data_url)))

    if not upload_bytes:
        raise ValueError("Choose an image file to send.")
    return Image.open(io.BytesIO(upload_bytes))


def run_encode(params, upload_bytes=None):
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
               source_activation=None):
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
