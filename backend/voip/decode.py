"""Recording in, picture and report out.

    load -> find the preamble anywhere in the file -> read the header
         -> demodulate exactly as many symbols as exist
         -> undo the error correction -> rebuild -> score it

Every step records what it saw, because on a real call the interesting question
is rarely "did it work" but "how close to not working was it".
"""

import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from voip import _tel, framing, payload, quality, report, sync
from voip.audio_io import load_audio
from voip.config import (
    DEFAULT_PARITY,
    REPORT_SCHEMA,
    SAMPLE_RATE,
    SYNC_SCORE_THRESHOLD,
    WEAK_MARGIN_THRESHOLD,
    FrameError,
    VoipError,
)
from voip.dsp import confidence_stats, weak_symbol_indices


@dataclass
class DecodeResult:
    report: dict
    audio: np.ndarray
    image: Image.Image | None = None
    text: str | None = None
    static: Image.Image | None = None
    activation: np.ndarray | None = None
    warnings: list = field(default_factory=list)

    @property
    def verdict(self):
        return self.report.get("verdict")

    @property
    def ok(self):
        return self.report.get("verdict") == "ok"


def decode(source, generation="auto", locked=False, caller=None, receiver=None,
           pin=None, parity=DEFAULT_PARITY, manifest=None, refine=True,
           drift_scan=False, search_seconds=None,
           threshold=SYNC_SCORE_THRESHOLD, weak_threshold=WEAK_MARGIN_THRESHOLD):
    """Decode a recording (path, bytes or array) into a picture or text."""
    audio, input_meta = _load(source)
    warnings = []

    result = {
        "schema": REPORT_SCHEMA,
        "decoded_utc": report.utc_now(),
        "input": input_meta,
        "audio": quality.audio_health(audio, SAMPLE_RATE),
    }

    # ---- 1. where does the transmission start -----------------------------
    found = sync.find_preamble(audio, search_seconds=search_seconds,
                               threshold=threshold, refine=refine)
    warnings.extend(found.warnings)

    if drift_scan and found.found:
        ppm, stretched, moved = sync.estimate_drift(audio, found.offset, 512)
        if ppm:
            warnings.append(f"Corrected an estimated {ppm:+.0f} ppm of clock drift.")
            audio, found.offset, found.drift_ppm = stretched, moved, ppm

    result["sync"] = found.as_dict()

    if not found.found:
        return _fail(result, "no-sync", audio, warnings, locked,
                     sync_info=result["sync"])

    # ---- 2. what does the header say --------------------------------------
    expect = None if generation in (None, "auto") else generation
    try:
        frame = framing.read_frame(audio, found.offset,
                                   expect=expect or "auto", rs_parity=parity)
    except FrameError as exc:
        warnings.append(str(exc))
        return _fail(result, "bad-header", audio, warnings, locked,
                     sync_info=result["sync"])

    warnings.extend(frame.warnings)
    result["frame"] = frame.as_dict()

    # ---- 3. demodulate, without inventing symbols that are not there ------
    bits, symbols, margin, bounds = framing.demodulate_frame(audio, frame, clamp=True)
    result["frame"].update(bounds)

    symbols_info = confidence_stats(margin, weak_threshold)
    symbols_info["weak_indices"] = weak_symbol_indices(margin, weak_threshold)
    result["symbols"] = symbols_info

    # ---- 4. undo the payload coding ---------------------------------------
    if frame.generation == "B":
        outcome = _rebuild_genb(bits, frame)
    else:
        outcome = _rebuild_genc(bits, frame, parity, locked, caller, receiver, pin)

    result["payload"] = outcome["payload"]
    warnings.extend(outcome.get("warnings", []))

    if bounds["truncated"]:
        warnings.append(
            f"Decoded only {bounds['available_symbols']} of "
            f"{bounds['expected_symbols']} symbols."
        )

    # ---- 5. score it against what was sent --------------------------------
    result["quality"] = _score(outcome, manifest, frame)

    verdict, hints = quality.verdict(
        result["sync"], result.get("frame"), bounds, result["payload"],
        symbols_info, result["audio"], locked=locked)
    result["verdict"] = verdict
    result["hints"] = hints
    result["warnings"] = warnings
    result["summary"] = quality.summary_block(result["sync"], result["frame"], symbols_info)

    return DecodeResult(report=result, audio=audio, image=outcome.get("image"),
                        text=outcome.get("text"), static=outcome.get("static"),
                        activation=outcome.get("activation"), warnings=warnings)


# --------------------------------------------------------------------------
# Payload rebuilding
# --------------------------------------------------------------------------

def _rebuild_genb(bits, frame):
    """Hamming already ran inside demodulate; this is just bits -> pixels."""
    activation = payload.genb_bits_to_activation(bits, frame.rows, frame.cols, frame.levels)
    from spectral.input.image_preprocessor import activation_to_png_bytes
    import io
    image = Image.open(io.BytesIO(
        activation_to_png_bytes(activation, gray_levels=frame.levels, scale=8)
    )).convert("RGB")
    return {
        "activation": activation,
        "image": image,
        "payload": {
            "kind": "image", "opened": True, "generation": "B",
            "rows": frame.rows, "cols": frame.cols, "levels": frame.levels,
            "payload_bits": int(len(bits)), "locked": False,
        },
    }


def _rebuild_genc(bits, frame, parity, locked, caller, receiver, pin):
    """Un-shuffle, Reed-Solomon, then see whether it is a picture or text."""
    packet = np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()[:frame.packet_bytes]
    key = payload.transmission_key(caller, receiver, pin) if locked \
        else payload.transmission_key()

    info = {"kind": None, "opened": False, "generation": "C",
            "packet_bytes": int(frame.packet_bytes), "locked": bool(locked)}
    info.update(payload.rs_blocks(frame.packet_bytes, parity))

    try:
        raw, repaired = payload.unprotect(packet, key, parity)
    except Exception as exc:
        info["reason"] = str(exc)
        return {"payload": info, "static": payload.static_image(packet, key)}

    info["repaired_bytes"] = int(repaired)
    info["rs_headroom"] = max(0, info["rs_limit_total"] - int(repaired))

    try:
        opened = payload.open_payload(raw)
    except VoipError as exc:
        info["reason"] = str(exc)
        return {"payload": info, "static": payload.static_image(packet, key)}

    info["opened"] = True
    info["kind"] = opened["kind"]
    out = {"payload": info}
    if opened["kind"] == "image":
        info["webp_bytes"] = opened["webp_bytes"]
        info["width"], info["height"] = opened["width"], opened["height"]
        out["image"] = opened["image"]
    else:
        info["characters"] = opened["characters"]
        info["text_bytes"] = opened["bytes"]
        out["text"] = opened["text"]
    return out


# --------------------------------------------------------------------------
# Scoring against the manifest
# --------------------------------------------------------------------------

def _score(outcome, manifest, frame):
    """Compare what came back with what the manifest says went out."""
    if not manifest:
        return {"compare_to": None}

    run_dir = manifest.get("_run_dir")
    sent_meta = manifest.get("payload", {})

    if outcome.get("text") is not None:
        sent_text = (manifest.get("source") or {}).get("text")
        if sent_text is None:
            return {"compare_to": "manifest", "text_compared": False}
        return {
            "compare_to": "manifest",
            "text_compared": True,
            "text_identical": bool(outcome["text"] == sent_text),
            "characters": len(outcome["text"]),
        }

    image = outcome.get("image")
    if image is None:
        return {"compare_to": "manifest", "image_compared": False}

    sent_png = (manifest.get("artifacts") or {}).get("sent_png")
    if run_dir and sent_png:
        path = os.path.join(run_dir, sent_png)
        if os.path.isfile(path):
            scored = quality.image_metrics(image, Image.open(path))
            scored["compare_to"] = "sent.png"
            return scored

    return {
        "compare_to": "manifest",
        "image_compared": False,
        "expected_width": sent_meta.get("width"),
        "expected_height": sent_meta.get("height"),
    }


# --------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------

def _load(source):
    if isinstance(source, np.ndarray):
        return np.asarray(source, dtype=np.float64), {
            "loader": "array", "name": "(in memory)", "path": None,
            "sample_rate_in": SAMPLE_RATE, "channels": 1, "resampled": False,
            "duration_seconds": len(source) / float(SAMPLE_RATE),
        }
    if isinstance(source, (bytes, bytearray)):
        from voip.audio_io import load_audio_bytes
        return load_audio_bytes(bytes(source))
    return load_audio(source)


def _fail(result, verdict, audio, warnings, locked, sync_info=None):
    result["verdict"] = verdict
    result.setdefault("frame", None)
    result.setdefault("symbols", {})
    result.setdefault("payload", {"opened": False})
    result.setdefault("quality", {"compare_to": None})
    _, hints = quality.verdict(sync_info, result.get("frame"), {},
                              result["payload"], result["symbols"],
                              result["audio"], locked=locked)
    result["hints"] = hints
    result["warnings"] = warnings
    result["summary"] = quality.summary_block(sync_info, result.get("frame"),
                                              result.get("symbols"))
    return DecodeResult(report=result, audio=audio, warnings=warnings)


# --------------------------------------------------------------------------
# Run folders
# --------------------------------------------------------------------------

def write_run(result, run_dir, recording_path=None, sent_image=None):
    """Save the decoded artefacts and report.json beside the transmission."""
    os.makedirs(run_dir, exist_ok=True)
    artifacts = {}

    from voip.audio_io import write_int16_wav
    write_int16_wav(os.path.join(run_dir, "rx_8k.wav"), result.audio, SAMPLE_RATE)
    artifacts["rx_wav"] = "rx_8k.wav"

    if result.image is not None:
        result.image.save(os.path.join(run_dir, "received.png"))
        artifacts["received_png"] = "received.png"
    if result.static is not None:
        result.static.save(os.path.join(run_dir, "static.png"))
        artifacts["static_png"] = "static.png"
    if result.text is not None:
        with open(os.path.join(run_dir, "received.txt"), "w", encoding="utf-8") as handle:
            handle.write(result.text)
        artifacts["received_txt"] = "received.txt"

    if result.image is not None and sent_image is not None:
        diff = quality.diff_image(result.image, sent_image)
        if diff is not None:
            diff.save(os.path.join(run_dir, "diff.png"))
            artifacts["diff_png"] = "diff.png"

    if recording_path:
        artifacts["recording"] = os.path.basename(recording_path)

    result.report["run_id"] = os.path.basename(run_dir)
    result.report["artifacts"] = artifacts
    report.write_report(run_dir, result.report)
    return run_dir
