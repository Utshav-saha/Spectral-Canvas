"""Scoring a decode, and saying in English what went wrong.

A real call fails in a handful of distinguishable ways, and telling them apart
is most of the diagnostic value. The verdict ladder is ordered by how early the
failure happened, because the first thing to break makes everything after it
meaningless:

    no-sync      no preamble in the recording at all
    bad-header   preamble found, but the 16 bits after it are not a frame
    truncated    recording stops before the payload does
    rs-failed    all the symbols arrived, Reed-Solomon still could not repair
    wrong-pin    same as above, but a lock was in use, so suspect the PIN first
    ok           picture or text recovered

``hints`` turns whichever rung was reached into the next thing to try, so the
report is useful without the troubleshooting table in hand.
"""

import numpy as np

from voip.config import WEAK_MARGIN_THRESHOLD


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def image_metrics(received, sent):
    """PSNR/MAE/MSE between two PIL images, when they are the same shape."""
    if received is None or sent is None:
        return {}
    got = np.asarray(received.convert("RGB"), dtype=np.float64)
    want = np.asarray(sent.convert("RGB"), dtype=np.float64)
    if got.shape != want.shape:
        return {"shape_match": False,
                "width": int(received.width), "height": int(received.height)}

    mse = float(np.mean((got - want) ** 2))
    return {
        "shape_match": True,
        "width": int(received.width), "height": int(received.height),
        "identical": bool(mse == 0),
        "mae": round(float(np.mean(np.abs(got - want))), 4),
        "mse": round(mse, 4),
        "psnr_db": None if mse == 0 else round(10 * np.log10(255.0 ** 2 / mse), 2),
    }


def activation_metrics(received, sent, levels):
    """Generation B: how many pixels came back at exactly the right level."""
    if received is None or sent is None:
        return {}
    got = np.asarray(received, dtype=np.float64)
    want = np.asarray(sent, dtype=np.float64)
    if got.shape != want.shape:
        return {"shape_match": False}

    steps = int(levels) - 1
    got_levels = np.round(got * steps).astype(int)
    want_levels = np.round(want * steps).astype(int)
    mse = float(np.mean((got - want) ** 2))
    return {
        "shape_match": True,
        "exact_pixel_fraction": round(float(np.mean(got_levels == want_levels)), 6),
        "mae": round(float(np.mean(np.abs(got - want))), 6),
        "mse": round(mse, 6),
        "psnr_db": None if mse == 0 else round(10 * np.log10(1.0 / mse), 2),
    }


def bit_error_rate(received_bits, sent_bits):
    if received_bits is None or sent_bits is None:
        return None
    n = min(len(received_bits), len(sent_bits))
    if n == 0:
        return None
    a = np.asarray(received_bits[:n], dtype=np.uint8)
    b = np.asarray(sent_bits[:n], dtype=np.uint8)
    return round(float(np.mean(a != b)), 6)


def audio_health(audio, sample_rate):
    """Level and clipping. A phone recorded too hot is a top-three failure mode."""
    from spectral.analysis.waveform import global_stats

    audio = np.asarray(audio, dtype=np.float64)
    stats = global_stats(audio, sample_rate)
    if not stats:
        return {}

    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    clipped = int(np.sum(np.abs(audio) >= 0.999))
    quiet = np.sort(np.abs(audio))[: max(1, len(audio) // 20)]
    floor = float(np.sqrt(np.mean(quiet ** 2)))

    return {
        "duration_seconds": stats["duration"],
        "peak_dbfs": round(20 * np.log10(peak), 1) if peak > 0 else -120.0,
        "rms_dbfs": round(20 * np.log10(rms), 1) if rms > 0 else -120.0,
        "crest_factor": stats["crest_factor"],
        "clipped_samples": clipped,
        "clipped_fraction": round(clipped / len(audio), 6),
        "noise_floor_dbfs": round(20 * np.log10(floor), 1) if floor > 0 else -120.0,
    }


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------

def verdict(sync_info, frame_info, bounds, payload_info, symbols_info,
            audio_info, locked=False):
    """(verdict, hints) -- which rung of the ladder the decode reached."""
    hints = []

    if not (sync_info or {}).get("found"):
        score = (sync_info or {}).get("preamble_score")
        hints.append(
            "No preamble was found anywhere in this recording. Check that it "
            "really contains the call audio, that Record was pressed before "
            "the transmission started, and that the phone's microphone was "
            "muted so the tones are not buried under room noise."
        )
        if score is not None and score > 0.25:
            hints.append(
                f"The best score was {score:.2f}, not far below the threshold. "
                f"A heavily damaged or re-compressed recording can look like this."
            )
        return "no-sync", hints

    if frame_info is None:
        hints.append(
            "The preamble was found but the header after it did not decode to a "
            "usable length. The first second of the transmission is damaged; try "
            "a longer --lead-in so the preamble is not clipped by the recorder."
        )
        return "bad-header", hints

    _level_hints(symbols_info, audio_info, hints)

    if (bounds or {}).get("truncated"):
        missing = bounds["truncated_symbols"]
        hints.insert(0, (
            f"The recording stops {missing} symbols "
            f"({bounds['truncated_seconds']:.1f} s) before the transmission ends. "
            f"Stop recording after the audio finishes, not during it."
        ))
        return "truncated", hints

    if not (payload_info or {}).get("opened"):
        if locked:
            hints.insert(0, (
                "Every symbol arrived, but Reed-Solomon could not rebuild the "
                "payload. With a lock in use the first suspect is the PIN or the "
                "two phone numbers, which have to match the sending side exactly."
            ))
            return "wrong-pin", hints
        hints.insert(0, (
            "Every symbol arrived, but Reed-Solomon could not repair the damage. "
            "It is all-or-nothing past 16 bad bytes in any 255-byte block, so a "
            "shorter transmission (--size 96, or --gen B) is the way back."
        ))
        return "rs-failed", hints

    return "ok", hints


def _level_hints(symbols_info, audio_info, hints):
    weak = (symbols_info or {}).get("weak_fraction")
    if weak is not None and weak > 0.10:
        hints.append(
            f"{weak * 100:.0f}% of symbols were marginal. That usually means the "
            f"phone's echo canceller or noise suppression was on -- both treat a "
            f"steady tone as something to remove -- or a speech codec such as GSM "
            f"or Opus was negotiated instead of PCMU."
        )

    peak = (audio_info or {}).get("peak_dbfs")
    if peak is not None and peak > -1.0 and (audio_info or {}).get("clipped_samples", 0) > 64:
        hints.append(
            "The recording is clipping. Turn the call volume down; a clipped tone "
            "smears energy into neighbouring bins."
        )
    if peak is not None and peak < -40.0:
        hints.append(
            f"The recording is very quiet (peak {peak:.0f} dBFS). Decoding ignores "
            f"gain, but this quiet usually means the far end was barely audible."
        )


def summary_block(sync_info, frame_info, symbols_info):
    """The four field names the troubleshooting table refers to, kept flat.

    Everything else in the report is nested; these four are the ones read at a
    glance, so they stay exactly where and exactly as they are named.
    """
    payload_bits = None
    if frame_info:
        payload_bits = frame_info.get("payload_bits")
        if payload_bits is None and frame_info.get("packet_bytes") is not None:
            payload_bits = 8 * int(frame_info["packet_bytes"])
    return {
        "preamble_score": (sync_info or {}).get("preamble_score"),
        "offset_s": (sync_info or {}).get("offset_seconds"),
        "payload_bits": payload_bits,
        "weak_symbols": (symbols_info or {}).get("weak_symbols"),
    }


def diff_image(received, sent, amplify=4):
    """Where the two pictures disagree, brightened so it is visible."""
    from PIL import Image

    if received is None or sent is None:
        return None
    got = np.asarray(received.convert("RGB"), dtype=np.int16)
    want = np.asarray(sent.convert("RGB"), dtype=np.int16)
    if got.shape != want.shape:
        return None
    delta = np.clip(np.abs(got - want) * amplify, 0, 255).astype(np.uint8)
    return Image.fromarray(delta, "RGB")


WEAK_THRESHOLD = WEAK_MARGIN_THRESHOLD
