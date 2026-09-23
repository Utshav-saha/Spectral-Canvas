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
           pin=None, manifest=None, refine=True,
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

    # Generation A does not use the FSK framing at all: no preamble tones, no
    # 16-bit header. Its pilot-tone alignment and its geometry both come from
    # the manifest, so it takes its own short path and rejoins at the report.
    gen = (manifest or {}).get("generation") if generation in (None, "auto") else generation
    if gen == "A":
        return _decode_gen_a(audio, result, manifest, locked, caller, receiver,
                             pin, warnings)

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
                                   expect=expect or "auto")
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
    outcome = _rebuild_genb(bits, frame, locked, caller, receiver, pin)

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
# Generation A
# --------------------------------------------------------------------------

def _decode_gen_a(audio, result, manifest, locked, caller, receiver, pin,
                  warnings):
    """Pilot-aligned multitone. The picture is expected to arrive damaged."""
    meta = (manifest or {}).get("gen_metadata")
    if not meta:
        warnings.append(
            "Generation A carries no header on the wire, so it can only be "
            "decoded against the manifest written by `prepare`. Point --run at "
            "the run that produced this audio."
        )
        return _fail(result, "bad-header", audio, warnings, locked)

    call_track = payload.gen_a()
    import tel_decoder

    offset = int(tel_decoder.pilot_align(audio, meta["tel"]))
    frames = meta["columns"] * meta["channels"] + meta["tel"]["preamble_frames"]
    end = offset + frames * meta["tel"]["frame_samples"]

    result["sync"] = {"found": True, "offset": offset,
                      "offset_seconds": round(offset / SAMPLE_RATE, 3),
                      "score": None, "method": "pilot-alignment"}
    result["frame"] = {"generation": "A", "rows": meta["rows"],
                       "cols": meta["columns"], "levels": meta["gray_levels"],
                       "channels": meta["channels"], "mode": meta["mode"],
                       "truncated": bool(end > len(audio))}

    if end > len(audio):
        warnings.append(
            f"The recording is {(end - len(audio)) / SAMPLE_RATE:.1f} s short of "
            f"the transmission; the tail of the picture will be missing."
        )

    image_array = call_track.decode(audio[offset:], meta, caller=caller,
                                    receiver=receiver, pin=pin,
                                    decrypt_enabled=bool(locked))
    image = Image.fromarray(
        image_array, mode="RGB" if image_array.ndim == 3 else "L")

    result["payload"] = {
        "kind": "image", "opened": True, "generation": "A",
        "rows": meta["rows"], "cols": meta["columns"],
        "levels": meta["gray_levels"], "locked": bool(locked),
    }
    result["symbols"] = {"weak_symbols": None,
                         "note": "Generation A has no symbol decisions to score; "
                                 "the error is in the amplitudes, not the tones."}
    result["quality"] = score_against_sent(image_array, manifest)
    result["verdict"] = "ok"
    result["hints"] = []
    result["warnings"] = warnings
    result["summary"] = {"generation": "A", "offset_s": result["sync"]["offset_seconds"],
                         "truncated": result["frame"]["truncated"]}

    return DecodeResult(report=result, audio=audio, image=image,
                        activation=image_array, warnings=warnings)


def score_against_sent(image_array, manifest):
    """Measure a recovered picture against the levels that went on the wire.

    Generation A is lossy by construction, so this is the measurement, not a
    pass or fail. The reference comes from the manifest rather than sent.png,
    so scoring works with no run folder on disk.
    """
    sent_levels = ((manifest or {}).get("payload") or {}).get("sent_levels")
    if sent_levels is None:
        return {"compare_to": None}

    levels = manifest["payload"]["levels"]
    from spectral.decoder.image_reconstructor import to_image_array
    sent = to_image_array(np.asarray(sent_levels, dtype=float) / (levels - 1),
                          levels).astype(float)
    got = np.asarray(image_array, dtype=float)

    if sent.shape != got.shape:
        return {"compare_to": "manifest", "image_compared": False,
                "reason": f"sent {sent.shape} vs recovered {got.shape}"}

    mse = float(np.mean((got - sent) ** 2))
    return {
        "compare_to": "manifest",
        "image_compared": True,
        "identical": bool(mse == 0),
        "mae": round(float(np.mean(np.abs(got - sent))), 3),
        "psnr": None if mse == 0 else round(10 * np.log10(255.0 ** 2 / mse), 2),
        "exact_fraction": round(float(np.mean(got == sent)), 4),
    }


# --------------------------------------------------------------------------
# Payload rebuilding
# --------------------------------------------------------------------------

def _rebuild_genb(bits, frame, locked=False, caller=None, receiver=None, pin=None):
    """Hamming already ran inside demodulate; this is just bits -> pixels."""
    activation = payload.genb_bits_to_activation(bits, frame.rows, frame.cols, frame.levels)
    if locked:
        activation = payload.gen_a()._permute(activation, caller, receiver, pin,
                                              forward=False)
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
            "payload_bits": int(len(bits)), "locked": bool(locked),
        },
    }


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

    # Prefer the level indices in the manifest: they are what actually went on
    # the wire, they need no file on disk, and they score both generations the
    # same way.
    activation = outcome.get("activation")
    if activation is not None and sent_meta.get("sent_levels") is not None:
        scored = score_against_sent(_levels_to_pixels(activation, sent_meta["levels"]),
                                    manifest)
        if scored.get("image_compared"):
            return scored

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


def _levels_to_pixels(activation, levels):
    from spectral.decoder.image_reconstructor import to_image_array
    return to_image_array(np.asarray(activation, dtype=float), levels)


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
