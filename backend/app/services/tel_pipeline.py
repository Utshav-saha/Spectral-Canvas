"""Glue between the HTTP layer and the call path in spectral/tel/.

Two generations go out over a voice line, both through `spectral/tel/
call_track.py`:

    A   parallel multitone with pilot tones. Amplitude carries the pixel, so a
        speech codec damages it - about 75% of pixels exact through simulated
        GSM. Fast on the wire (one frame per column), so it carries the bigger
        picture. The damage is the input the restoration model is trained on.
    B   16-FSK, one tone per symbol. Which tone carries the pixel, so a codec
        cannot touch it - 100% exact - at about a tenth of the resolution.

Generation C (WebP + Reed-Solomon) was cut: a byte-exact file transfer
has no graded loss to measure or learn from. It is in the git history.

Nothing here reimplements a modem. The tel modules use bare sibling imports
(`import fsk_codec as fsk`), so their directory goes on sys.path first.
"""

import io
import os
import shutil
import sys

import numpy as np
from PIL import Image
from scipy.io import wavfile

_TEL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "spectral", "tel")
if _TEL not in sys.path:
    sys.path.insert(0, _TEL)

import channel_sim               # noqa: E402

from spectral.tel import call_track                    # noqa: E402
from spectral.analysis import waveform as wf           # noqa: E402
from spectral.decoder.image_reconstructor import to_image_array  # noqa: E402
from app.services.pipeline import validate_credentials  # noqa: E402

SAMPLE_RATE = call_track.SAMPLE_RATE

# What the Call page offers. Gen A spends one frame per column whatever the
# content, so it buys resolution cheaply; Gen B is serial and does not.
# A call runs in real time, so Generation A refuses one nobody would sit
# through. Generation B has no limit: it is the exact one, and the only reason
# to choose it is when correctness matters more than the wait - capping it
# would be refusing the whole point of it. The page warns past this many
# seconds instead of refusing.
MAX_SECONDS = 300
LONG_SECONDS = 300

GENERATIONS = {
    "A": {
        "id": "A",
        "label": "Generation A",
        "tagline": "Amplitude carries the pixel",
        "summary": ("The same parallel multitone scheme the WAV track uses, "
                    "narrowed to 700-3000 Hz with two pilot tones. A voice "
                    "codec models each frame with eight poles and cannot hold "
                    "that many tone levels, so the picture arrives damaged - "
                    "about three quarters of pixels exact. It is fast on the "
                    "wire, so it carries the most detail."),
        "sizes": [16, 24, 32, 48, 64],
        "default_size": 48,
        "levels": [2, 4, 8, 16],
        "default_levels": 16,
        "lossy": True,
        # a call in real time; a lossy picture is not worth sitting through
        "max_seconds": MAX_SECONDS,
    },
    "B": {
        "id": "B",
        "label": "Generation B",
        "tagline": "Which tone carries the pixel",
        "summary": ("One tone at a time out of sixteen. The decoder takes an "
                    "argmax and never compares loudness, so a codec that "
                    "destroys amplitude cannot touch it - the picture arrives "
                    "exact. The cost is airtime: 32 x 32 at 4 levels is about "
                    "36 s, and 64 x 64 is four times that, so the bigger "
                    "grids are only worth it when exactness matters more than "
                    "the wait."),
        "sizes": [16, 24, 32, 48, 64],
        "default_size": 32,
        "levels": [2, 4, 16],
        "default_levels": 4,
        "lossy": False,
        # no cap: exactness is the reason to pick this one
        "max_seconds": None,
    },
}



def info():
    return {
        "gsm_available": gsm_available(),
        "sample_rate": SAMPLE_RATE,
        "generations": list(GENERATIONS.values()),
        "default_generation": "A",
        "max_seconds": MAX_SECONDS,
    }


def gsm_available():
    return bool(shutil.which("toast") or channel_sim._ffmpeg_has_libgsm())


def _check(generation, size, levels, colour):
    if generation not in GENERATIONS:
        raise ValueError("Generation must be A or B.")
    spec = GENERATIONS[generation]
    if size not in spec["sizes"]:
        raise ValueError(f"Generation {generation} carries "
                         f"{', '.join(str(s) for s in spec['sizes'])} pixels square.")
    if levels not in spec["levels"]:
        raise ValueError(f"Generation {generation} carries "
                         f"{', '.join(str(n) for n in spec['levels'])} gray levels.")
    mode = "RGB" if colour else "L"
    seconds = call_track.budget_seconds(size, size, levels, mode, generation)

    limit = spec.get("max_seconds")
    if limit is not None and seconds > limit:
        raise ValueError(
            f"That would take {seconds / 60:.1f} minutes of call time, and the "
            f"limit for Generation {generation} is {limit // 60} minutes. Use a "
            f"smaller grid, fewer gray levels, or send it in grayscale. "
            f"Generation B has no limit, if you want to wait it out."
        )
    return mode, seconds


def open_image(upload_bytes):
    if not upload_bytes:
        raise ValueError("Choose an image file to send.")
    try:
        image = Image.open(io.BytesIO(upload_bytes))
        image.load()
    except Exception:
        raise ValueError("That file is not a picture we can read. Try a PNG or JPG.")
    return image


def plan(generation, size, levels, colour):
    """Quote the airtime without doing the encode, so the page can show it
    while the settings are still being chosen."""
    mode, seconds = _check(generation, size, levels, colour)
    spec = GENERATIONS[generation]
    channels = 3 if colour else 1
    return {
        "generation": generation,
        "rows": size, "columns": size,
        "gray_levels": levels,
        "mode": mode,
        "channels": channels,
        "seconds": round(seconds, 2),
        "lossy": spec["lossy"],
        "pixels": size * size * channels,
        "max_seconds": spec.get("max_seconds"),
        # no cap on B, but past five minutes the page should say so plainly
        "long": bool(seconds > LONG_SECONDS),
    }


def _png(image_array):
    image = Image.fromarray(image_array,
                            mode="RGB" if image_array.ndim == 3 else "L")
    scale = max(1, 320 // max(1, image.width))
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale),
                             Image.Resampling.NEAREST)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def to_wav_bytes(audio, sample_rate=SAMPLE_RATE):
    buf = io.BytesIO()
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    wavfile.write(buf, sample_rate, pcm)
    return buf.getvalue()


def run_send(image_bytes, generation, size, levels, colour, locked=False,
             caller=None, receiver=None, pin=None, autocontrast=True):
    mode, _ = _check(generation, size, levels, colour)
    if locked:
        validate_credentials(caller, receiver, pin)

    audio, metadata, activation = call_track.encode(
        open_image(image_bytes), target_width=size, target_height=size,
        gray_levels=levels, mode=mode, generation=generation,
        security_enabled=locked, caller=caller, receiver=receiver, pin=pin,
        autocontrast=autocontrast,
    )

    sent_array = to_image_array(activation, levels)
    return {
        "audio": audio,
        "metadata": metadata,
        "wav_bytes": to_wav_bytes(audio),
        "sent_png": _png(sent_array),
        "sent_array": sent_array,
        "report": {
            "generation": generation,
            "rows": metadata["rows"], "columns": metadata["columns"],
            "gray_levels": levels, "mode": mode,
            "channels": metadata["channels"],
            "seconds": metadata["duration_seconds"],
            "scheme": metadata["scheme"],
            "band": metadata["band"],
            "lossy": GENERATIONS[generation]["lossy"],
            "autocontrast": bool(autocontrast),
            "locked": bool(locked),
        },
        "stats": wf.global_stats(audio, SAMPLE_RATE),
    }


def run_call(audio, loss, noise_db, seed):
    if not 0.0 <= loss <= 0.3:
        raise ValueError("Packet loss must be between 0% and 30%.")
    if not -80.0 <= noise_db <= -10.0:
        raise ValueError("Noise floor must be between -80 and -10 dB.")
    if not gsm_available():
        raise ValueError("No GSM 06.10 codec is installed on the server, so the "
                         "call cannot be simulated. Install libgsm, or an ffmpeg "
                         "built with it.")
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


def locate(audio, metadata):
    """Where the transmission starts in the received audio.

    The simulated call puts 0.12-0.9 s of silence in front, as a real
    recording would, so the decoder has to find the start before it reads.
    Neither generation is self-describing on the wire; the geometry comes
    from the send's metadata.
    """
    if metadata.get("generation") == "A":
        import tel_decoder
        start = int(tel_decoder.pilot_align(audio, metadata["tel"]))
        frames = metadata["columns"] * metadata["channels"] + metadata["tel"]["preamble_frames"]
        end = start + frames * metadata["tel"]["frame_samples"]
        return {"offset": start, "truncated": bool(end > len(audio))}

    import fsk_codec as fsk
    start = int(fsk.find_preamble(audio))
    frames = len(fsk.PREAMBLE) + metadata["fsk"]["n_symbols"]
    end = start + frames * fsk.SYMBOL_SAMPLES
    return {"offset": start, "truncated": bool(end > len(audio))}


def run_receive(audio, metadata, locked=False, caller=None, receiver=None,
                pin=None, sent_array=None):
    if metadata is None:
        raise ValueError("That transmission has expired. Send it again.")
    if locked:
        validate_credentials(caller, receiver, pin)

    located = locate(audio, metadata)
    trimmed = audio[located["offset"]:] if located["offset"] else audio

    try:
        image_array = call_track.decode(
            trimmed, metadata, caller=caller, receiver=receiver, pin=pin,
            decrypt_enabled=bool(locked))
    except Exception as exc:
        raise ValueError(f"The audio could not be read as a transmission: {exc}")

    result = {
        "ok": True,
        "generation": metadata.get("generation"),
        "locked": bool(locked),
        "rows": metadata["rows"], "columns": metadata["columns"],
        "mode": metadata.get("mode", "L"),
        "offset_seconds": round(located["offset"] / SAMPLE_RATE, 3),
        "truncated": located["truncated"],
        "png": _png(image_array),
        # kept so the model can be run on it later, at its real size: _png
        # blows it up to ~320 px for display, which is not what to feed a model
        "array": image_array,
    }

    # Against what was actually put on the wire, so the number is the channel's
    # doing and not the quantiser's.
    if sent_array is not None and np.shape(sent_array) == image_array.shape:
        got = image_array.astype(float)
        ref = np.asarray(sent_array, dtype=float)
        mse = float(np.mean((got - ref) ** 2))
        exact = float(np.mean(got == ref))
        result["match"] = {
            "identical": bool(mse == 0),
            "mae": round(float(np.mean(np.abs(got - ref))), 3),
            "psnr": None if mse == 0 else round(10 * np.log10(255.0 ** 2 / mse), 2),
            "exact_fraction": round(exact, 4),
        }
    return result


def model_status():
    """Whether the learned upscaler can run here, and why not if it cannot."""
    from spectral.restore import upscaler
    return upscaler.status()


def run_enhance(image_array):
    """Track 2's missing detail, guessed back by the model.

    Generation B arrives bit-exact, so nothing here is repairing transmission
    damage. What it undoes is the shrinking and the 4-level quantising done
    *before* the call, which are the two things a voice line has no airtime
    for. See spectral/restore/upscaler.py.
    """
    from spectral.restore import upscaler

    if not upscaler.available():
        raise ValueError(upscaler.status()["message"])

    array = np.asarray(image_array)
    if array.size == 0:
        raise ValueError("There is no rebuilt picture to enhance yet.")

    enhanced = upscaler.enhance(array)
    return {
        "png": _png(enhanced),
        "size": int(enhanced.shape[0]),
        "from_size": int(array.shape[0]),
        "compare": upscaler.compare(array, enhanced),
    }


def waveform_payload(audio, buckets):
    return {
        "envelope": wf.peak_envelope(audio, buckets),
        "buckets": wf.bucket_stats(audio, SAMPLE_RATE, buckets),
        "stats": wf.global_stats(audio, SAMPLE_RATE),
    }
