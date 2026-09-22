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


def read_any_audio(raw_bytes, filename=None):
    """Any recording -> mono float64 at 8 kHz.

    WAV still goes through read_any_wav, byte for byte as before. Anything else
    goes through ffmpeg, because Linphone's in-call recorder writes Matroska
    (.mka) and an iOS share can arrive as .m4a or .caf -- none of which scipy
    will open, and all of which are what a real call actually produces.

    Imported inside the function so the app still starts if voip/ is absent.
    """
    if not raw_bytes:
        raise ValueError("Choose an audio file to inspect.")

    try:
        return read_any_wav(raw_bytes)
    except ValueError:
        pass

    try:
        from voip.audio_io import load_audio_bytes
    except ImportError:
        raise ValueError(
            "That does not look like a WAV file, and the converter for other "
            "formats is unavailable on this server."
        )

    try:
        audio, meta = load_audio_bytes(raw_bytes, filename)
    except Exception as exc:
        raise ValueError(str(exc))

    return audio, {"sample_rate_in": int(meta.get("sample_rate_in", SAMPLE_RATE)),
                   "channels": int(meta.get("channels", 1)),
                   "resampled": bool(meta.get("resampled", False)),
                   "container": meta.get("container"),
                   "codec": meta.get("codec")}


def _find_transmission(audio):
    """Where the transmission starts, and whether the header describes audio
    that is actually here. Without the second half, noise can claim a 65 kB
    packet and the demodulator would build a minutes-long symbol grid out of
    nothing.

    The search is delegated to voip.sync rather than fsk.read_header. The
    modem's own preamble search only looks at the first three seconds, which is
    all a simulated call ever needs -- the simulator prepends at most 900 ms.
    A recording made on a phone has however long it took to press Record, walk
    back to the laptop and start the audio, so a real .mka would come back
    "not found" here for no better reason than it began too late.
    """
    from voip import sync as voip_sync

    located = voip_sync.find_preamble(audio)
    if not located.found:
        return {"found": False, "packet_bytes": None, "offset": located.offset,
                "score": located.score, "weak_symbols": None, "truncated": None}

    length_bits, _ = fsk.read_header(audio, offset=located.offset)
    n_bytes = int("".join(str(int(b)) for b in length_bits), 2)
    frames = len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS + 2 * n_bytes
    end = located.offset + frames * SYMBOL_SAMPLES
    plausible = image_webp.RS_PARITY < n_bytes and end <= len(audio) * 1.1 + SAMPLE_RATE

    return {
        "found": bool(plausible),
        "packet_bytes": int(n_bytes) if plausible else None,
        "offset": int(located.offset),
        "score": round(float(located.score), 4),
        "weak_symbols": _weak_symbols(audio, located.offset, n_bytes) if plausible else None,
        "truncated": bool(end > len(audio)) if plausible else None,
    }


def _weak_symbols(audio, offset, n_bytes):
    """How many payload symbols were close calls, for the inspect report."""
    from voip.dsp import available_symbols, confidence_stats, symbol_decisions, \
        symbol_magnitudes

    start = offset + (len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS) * SYMBOL_SAMPLES
    count = min(2 * int(n_bytes), available_symbols(audio, start))
    if count <= 0:
        return None
    _, margin = symbol_decisions(symbol_magnitudes(audio, start, count))
    return confidence_stats(margin)["weak_symbols"]


def _from_preamble(audio, offset):
    """The recording trimmed to start at the preamble.

    image_webp.receive_image -> demodulate -> fsk.read_header runs its own
    three-second search, one layer down. Handing it a slice that begins at the
    preamble means that search trivially succeeds at offset 0, so a recording
    with a long lead-in decodes without image_webp needing to change at all.
    """
    return audio[int(offset):] if offset else audio


def run_inspect(audio):
    located = _find_transmission(audio)
    found = located["found"]

    opens = False
    if found:
        image, _ = image_webp.receive_image(_from_preamble(audio, located["offset"]))
        opens = image is not None

    if not found:
        message = ("No call transmission was found in this audio. It needs the "
                   "16-tone preamble that the send side puts at the start.")
    elif located["truncated"]:
        message = ("This transmission was found, but the recording stops before it "
                   "ends. Rebuilding it will probably fail; record the whole call.")
    elif opens:
        message = "This transmission is open. Rebuild it whenever you are ready."
    else:
        message = ("This transmission did not open without a key. It is either locked, "
                   "or the call damaged it past repair. Enter the numbers and PIN to try.")

    return {
        "found": bool(found),
        "packet_bytes": located["packet_bytes"],
        "offset_seconds": round(located["offset"] / SAMPLE_RATE, 3) if found else None,
        "opens_without_key": opens,
        "message": message,
        "stats": wf.global_stats(audio, SAMPLE_RATE),
        # additive: the Call page ignores what it does not know about
        "preamble_score": located["score"],
        "weak_symbols": located["weak_symbols"],
        "truncated": located["truncated"],
    }


def run_receive(audio, locked=False, caller=None, receiver=None, pin=None,
                sent_array=None):
    creds = {}
    if locked:
        validate_credentials(caller, receiver, pin)
        creds = dict(caller=caller, receiver=receiver, pin=pin)

    located = _find_transmission(audio)
    if not located["found"]:
        raise ValueError("No call transmission was found in this audio, so there is "
                         "nothing to rebuild.")

    try:
        image, report = image_webp.receive_image(
            _from_preamble(audio, located["offset"]), **creds)
    except Exception as exc:
        raise ValueError(f"The audio could not be read as a transmission: {exc}")

    result = {
        "ok": image is not None,
        "locked": bool(locked),
        "packet_bytes": report["packet_bytes"],
        # report["offset"] is relative to the trimmed slice, so add back where
        # the slice began to keep this the position in the original recording
        "offset_seconds": round(
            (located["offset"] + report["offset"]) / SAMPLE_RATE, 3),
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
