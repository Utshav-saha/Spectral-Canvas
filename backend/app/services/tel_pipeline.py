"""Glue between the HTTP layer and the telephony path in spectral/tel/.

The picture travels the way image_webp.py sends it over a voice call:
WebP -> Reed-Solomon -> keyed byte shuffle -> 16-FSK at 8 kHz. Nothing here
reimplements the modem; it only moves bytes in and out of those modules.

The tel modules use bare sibling imports (`import fsk_codec as fsk`), so their
directory goes on sys.path before they are imported.
"""

import io
import os
import shutil
import sys
from math import gcd

import numpy as np
from PIL import Image
from scipy.io import wavfile
from scipy.signal import resample_poly

_TEL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "spectral", "tel")
if _TEL not in sys.path:
    sys.path.insert(0, _TEL)

import fsk_codec as fsk          # noqa: E402
import image_webp                # noqa: E402
import channel_sim               # noqa: E402

from spectral.analysis import waveform as wf          # noqa: E402
from app.services.pipeline import validate_credentials  # noqa: E402

SAMPLE_RATE = fsk.SAMPLE_RATE
SYMBOL_SAMPLES = fsk.SYMBOL_SAMPLES
SIZES = [64, 96, 128, 160]
QUALITIES = [30, 50, 70]


def info():
    return {
        "gsm_available": gsm_available(),
        "sample_rate": SAMPLE_RATE,
        "symbol_ms": fsk.SYMBOL_MS,
        "tones": fsk.M,
        "f_low": float(fsk.TONES[0]),
        "f_high": float(fsk.TONES[-1]),
        "rs_parity": image_webp.RS_PARITY,
        "sizes": SIZES,
        "qualities": QUALITIES,
        "default_size": image_webp.DEFAULT_SIZE,
        "default_quality": image_webp.DEFAULT_QUALITY,
    }


def gsm_available():
    return bool(shutil.which("toast") or channel_sim._ffmpeg_has_libgsm())


def _check_settings(size, quality):
    if size not in SIZES:
        raise ValueError(f"Size must be one of {', '.join(map(str, SIZES))} pixels.")
    if quality not in QUALITIES:
        raise ValueError(f"Quality must be one of {', '.join(map(str, QUALITIES))}.")


def open_image(upload_bytes):
    if not upload_bytes:
        raise ValueError("Choose an image file to send.")
    try:
        image = Image.open(io.BytesIO(upload_bytes))
        image.load()
    except Exception:
        raise ValueError("That file is not a picture we can read. Try a PNG or JPG.")
    return image


def plan(image_bytes, size, quality):
    _check_settings(size, quality)
    p = image_webp.plan(image_bytes, size, quality)
    if p["packet_bytes"] > image_webp.MAX_PACKET_BYTES:
        p["too_large"] = True
    p["seconds"] = round(p["seconds"], 2)
    return p


def _png(image):
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def to_wav_bytes(audio, sample_rate=SAMPLE_RATE):
    buf = io.BytesIO()
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    wavfile.write(buf, sample_rate, pcm)
    return buf.getvalue()


def run_send(image_bytes, size, quality, locked=False,
             caller=None, receiver=None, pin=None):
    _check_settings(size, quality)
    if locked:
        validate_credentials(caller, receiver, pin)
        creds = dict(caller=caller, receiver=receiver, pin=pin)
    else:
        creds = {}

    audio, on_wire, report = image_webp.send_image(image_bytes, size, quality, **creds)
    psnr = report["compression_psnr"]
    report["compression_psnr"] = None if psnr == float("inf") else round(psnr, 2)
    report["seconds"] = round(report["seconds"], 2)
    report["quality"] = quality

    return {
        "audio": audio,
        "wav_bytes": to_wav_bytes(audio),
        "sent_png": _png(on_wire),
        "sent_array": np.asarray(on_wire),
        "report": report,
        "stats": wf.global_stats(audio, SAMPLE_RATE),
    }


def run_call(audio, loss, noise_db, seed):
    if not 0.0 <= loss <= 0.3:
        raise ValueError("Packet loss must be between 0% and 30%.")
    if not -80.0 <= noise_db <= -10.0:
        raise ValueError("Noise floor must be between -80 and -10 dB.")
    if not gsm_available():
        raise ValueError("No GSM 06.10 codec is installed on the server, so the call "
                         "cannot be simulated. Install it with: brew install libgsm")
    try:
        received = channel_sim.channel(audio, sample_rate=SAMPLE_RATE, loss_rate=loss,
                                       noise_db=noise_db, seed=int(seed))
    except RuntimeError as exc:
        raise ValueError(str(exc))
    return {
        "audio": received,
        "wav_bytes": to_wav_bytes(received),
        "stats": wf.global_stats(received, SAMPLE_RATE),
    }


def read_any_wav(raw_bytes):
    """Any PCM or float WAV -> mono float64 at 8 kHz, plus what it was before."""
    try:
        rate, data = wavfile.read(io.BytesIO(raw_bytes))
    except Exception:
        raise ValueError("That does not look like a WAV file.")

    channels = 1 if data.ndim == 1 else data.shape[1]
    if data.dtype == np.int16:
        audio = data / 32768.0
    elif data.dtype == np.int32:
        audio = data / 2147483648.0
    elif data.dtype == np.uint8:
        audio = (data.astype(np.float64) - 128.0) / 128.0
    else:
        audio = data.astype(np.float64)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float64)
    if len(audio) == 0:
        raise ValueError("That WAV file has no audio in it.")

    if rate != SAMPLE_RATE:
        g = gcd(int(rate), SAMPLE_RATE)
        audio = resample_poly(audio, SAMPLE_RATE // g, int(rate) // g)

    return audio, {"sample_rate_in": int(rate), "channels": channels,
                   "resampled": rate != SAMPLE_RATE}


def _find_transmission(audio):
    """Read the length header and check it describes audio that is actually
    here. Without this, noise can claim a 65 kB packet and the demodulator
    would build a minutes-long symbol grid out of nothing."""
    length_bits, offset = fsk.read_header(audio)
    n_bytes = int("".join(str(int(b)) for b in length_bits), 2)
    frames = len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS + 2 * n_bytes
    end = offset + frames * SYMBOL_SAMPLES
    found = image_webp.RS_PARITY < n_bytes and end <= len(audio) * 1.1 + SAMPLE_RATE
    return found, n_bytes, offset


def run_inspect(audio):
    found, n_bytes, offset = _find_transmission(audio)
    opens = False
    if found:
        image, _ = image_webp.receive_image(audio)
        opens = image is not None

    if not found:
        message = ("No call transmission was found in this audio. It needs the "
                   "16-tone preamble that the send side puts at the start.")
    elif opens:
        message = "This transmission is open. Rebuild it whenever you are ready."
    else:
        message = ("This transmission did not open without a key. It is either locked, "
                   "or the call damaged it past repair. Enter the numbers and PIN to try.")

    return {
        "found": bool(found),
        "packet_bytes": int(n_bytes) if found else None,
        "offset_seconds": round(offset / SAMPLE_RATE, 3) if found else None,
        "opens_without_key": opens,
        "message": message,
        "stats": wf.global_stats(audio, SAMPLE_RATE),
    }


def run_receive(audio, locked=False, caller=None, receiver=None, pin=None,
                sent_array=None):
    creds = {}
    if locked:
        validate_credentials(caller, receiver, pin)
        creds = dict(caller=caller, receiver=receiver, pin=pin)

    found, _, _ = _find_transmission(audio)
    if not found:
        raise ValueError("No call transmission was found in this audio, so there is "
                         "nothing to rebuild.")

    try:
        image, report = image_webp.receive_image(audio, **creds)
    except Exception as exc:
        raise ValueError(f"The audio could not be read as a transmission: {exc}")

    result = {
        "ok": image is not None,
        "locked": bool(locked),
        "packet_bytes": report["packet_bytes"],
        "offset_seconds": round(report["offset"] / SAMPLE_RATE, 3),
    }

    if image is None:
        result.update(png=_png(report["static"]), reason=report["reason"],
                      width=report["static"].width, height=report["static"].height)
        return result

    result.update(png=_png(image), width=image.width, height=image.height,
                  repaired_bytes=report["repaired_bytes"],
                  webp_bytes=report["webp_bytes"])

    if sent_array is not None:
        got = np.asarray(image)
        if got.shape == sent_array.shape:
            mse = float(np.mean((got.astype(float) - sent_array.astype(float)) ** 2))
            result["match"] = {
                "identical": bool(mse == 0),
                "psnr": None if mse == 0 else round(10 * np.log10(255.0 ** 2 / mse), 2),
            }
    return result


def waveform_payload(audio, buckets):
    return {
        "envelope": wf.peak_envelope(audio, buckets),
        "buckets": wf.bucket_stats(audio, SAMPLE_RATE, buckets),
        "stats": wf.global_stats(audio, SAMPLE_RATE),
    }
