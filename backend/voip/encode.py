"""Turn a picture or a message into the WAV that gets played into the call.

The output is deliberately boring: 8 kHz, mono, 16-bit PCM, with a couple of
seconds of digital silence at each end. pjsua refuses to open anything else,
Linphone's file player is happiest with plain PCM, and the silence covers the
gap between pressing Record on the phone and Enter on the laptop.

Everything needed to score the result later goes into ``manifest.json`` beside
the WAV. The decoder does not need it -- both generations are self-describing
on the wire -- but without it there is nothing to compare the recovered picture
against.
"""

import hashlib
import io
import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from voip import _tel, framing, payload, report
from voip.audio_io import pad_audio, to_wav_bytes, write_int16_wav
from voip.config import (
    DEFAULT_GRID,
    DEFAULT_LEAD_IN_S,
    DEFAULT_LEAD_OUT_S,
    DEFAULT_LEVELS,
    LONG_TRANSMISSION_WARN_S,
    MANIFEST_SCHEMA,
    SAMPLE_RATE,
    VoipError,
)


@dataclass
class PrepareResult:
    audio: np.ndarray
    manifest: dict
    sent_image: Image.Image | None = None
    source_image: Image.Image | None = None
    text: str | None = None
    warnings: list = field(default_factory=list)

    @property
    def duration_seconds(self):
        return len(self.audio) / float(SAMPLE_RATE)


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def _open_image(source):
    if isinstance(source, Image.Image):
        return source
    if isinstance(source, (bytes, bytearray)):
        image = Image.open(io.BytesIO(bytes(source)))
    else:
        path = os.path.abspath(os.path.expanduser(str(source)))
        if not os.path.isfile(path):
            raise VoipError(f"No such picture: {path}")
        image = Image.open(path)
    image.load()
    return image


def _quantize_for_genb(image, grid, levels):
    """Reuse the app's own preprocessing so Gen-B matches the rest of the project.

    process_image gives back an inverted activation matrix -- black is 1.0, the
    loud end -- resized with the aspect ratio kept and padded onto white, which
    costs no energy to transmit.
    """
    from spectral.input.image_preprocessor import process_image
    return process_image(image, target_width=int(grid), target_height=int(grid),
                         gray_levels=int(levels), mode="L")


def _sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode()).hexdigest()


# --------------------------------------------------------------------------
# Planning (no audio produced)
# --------------------------------------------------------------------------

def plan(source=None, text=None, generation="A", grid=DEFAULT_GRID,
         levels=DEFAULT_LEVELS, colour=False, lead_in=DEFAULT_LEAD_IN_S,
         lead_out=DEFAULT_LEAD_OUT_S, **_ignored):
    """Geometry and seconds, without building the waveform. Drives --dry-run."""
    if generation not in ("A", "B"):
        raise VoipError(f"Generation must be A or B, not {generation!r}.")
    if generation == "B" and colour:
        raise VoipError("Generation B carries grayscale only. Use --gen A for colour.")

    if generation == "B":
        fsk = _tel.fsk()
        overhead = (len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS) * fsk.SYMBOL_MS / 1000.0
        # Validate before quoting a time: without this, --dry-run cheerfully
        # plans a call for a grid the 16-bit header cannot describe.
        framing.build_genb_header(grid, grid, levels)
        info = framing.genb_info(grid, grid, levels)
        seconds = info["n_symbols"] * fsk.SYMBOL_MS / 1000.0 + overhead
        out = {"generation": "B", "kind": "image", "rows": grid, "cols": grid,
               "levels": levels, "mode": "L", "channels": 1,
               "payload_bits": info["n_payload_bits"], "symbols": info["n_symbols"]}
    else:
        mode = "RGB" if colour else "L"
        seconds = payload.gen_a().budget_seconds(grid, grid, levels, mode, "A")
        out = {"generation": "A", "kind": "image", "rows": grid, "cols": grid,
               "levels": levels, "mode": mode, "channels": 3 if colour else 1,
               "symbols": grid * (3 if colour else 1)}

    out["airtime_seconds"] = round(seconds, 2)
    out["total_seconds"] = round(seconds + lead_in + lead_out, 2)
    return out


# --------------------------------------------------------------------------
# Building the transmission
# --------------------------------------------------------------------------

def prepare(source=None, text=None, generation="A", grid=DEFAULT_GRID,
            levels=DEFAULT_LEVELS, colour=False, as_image=False, locked=False,
            caller=None, receiver=None, pin=None, lead_in=DEFAULT_LEAD_IN_S,
            lead_out=DEFAULT_LEAD_OUT_S, source_name=None, **_ignored):
    """Source -> PrepareResult holding the padded audio and a full manifest."""
    if source is None and text is None:
        raise VoipError("Nothing to send: give a picture or some text.")
    if generation not in ("A", "B"):
        raise VoipError(f"Generation must be A or B, not {generation!r}.")
    if generation == "B" and colour:
        raise VoipError("Generation B carries grayscale only. Use --gen A for colour.")

    fsk = _tel.fsk()
    call_track = payload.gen_a()
    warnings = []

    if locked:
        # Reuses the app's own rule: two 11-digit numbers and a 4-8 digit PIN.
        from app.services.pipeline import validate_credentials
        validate_credentials(caller, receiver, pin)

    # Generation C carried raw text bytes. With it gone, text over a call is
    # rendered to a picture and sent like any other.
    if text is not None:
        if not as_image:
            warnings.append("Text over a call is rendered as a picture.")
        from spectral.input.image_preprocessor import render_text_image
        source = render_text_image(text, target_width=max(64, grid),
                                   target_height=max(64, grid))
        source_name = source_name or "rendered-text"

    source_image = _open_image(source)
    gen_metadata = None

    if generation == "B":
        activation = _quantize_for_genb(source_image, grid, levels)
        to_send = activation
        if locked:
            to_send = call_track._permute(activation, caller, receiver, pin,
                                          forward=True)
        bits, payload_meta = payload.build_genb_bits(to_send, levels)
        header = framing.build_genb_header(payload_meta["rows"],
                                           payload_meta["cols"], levels)
        # No re-sync markers on this path. They are the app's Track 2 format
        # (see fsk_codec.RESYNC), and this package carries its own framing
        # instead: a 16-bit descriptor that makes a recording self-describing
        # from the audio alone, read back by voip.framing rather than by
        # fsk.demodulate's own segment walk. The two layers each own the wire
        # they read, and mixing them would break voip.framing's symbol
        # arithmetic - `resync=0` keeps this one exactly as its tests pin it.
        audio, info = fsk.modulate(bits, fec=True, header=header, resync=0)
        header_value = int("".join(map(str, header)), 2)
        payload_meta.update(mode="L", channels=1, locked=bool(locked))
    else:
        audio, gen_metadata, activation = call_track.encode(
            source_image, target_width=grid, target_height=grid,
            gray_levels=levels, mode="RGB" if colour else "L", generation="A",
            security_enabled=bool(locked), caller=caller, receiver=receiver,
            pin=pin)
        payload_meta = {
            "kind": "image", "rows": gen_metadata["rows"],
            "cols": gen_metadata["columns"], "levels": levels,
            "mode": gen_metadata["mode"], "channels": gen_metadata["channels"],
            "locked": bool(locked),
        }
        # Generation A has no FSK framing; these keep the manifest one shape.
        info = {"n_symbols": gen_metadata["columns"] * gen_metadata["channels"],
                "n_payload_bits": None}
        header_value = None

    sent_image = _activation_preview(activation, levels)

    # The level indices that went on the wire, so a decode can be scored
    # against them without the run folder. Small: 24x24 is 576 ints, and a
    # 32x32 colour frame is 3072. Generation A has no header on the wire, so
    # its manifest has to be self-sufficient anyway.
    payload_meta["sent_levels"] = np.rint(
        np.clip(activation, 0.0, 1.0) * (levels - 1)).astype(int).tolist()

    airtime = len(audio) / float(SAMPLE_RATE)
    if airtime > LONG_TRANSMISSION_WARN_S:
        warnings.append(
            f"This is {airtime / 60:.1f} minutes of call. A smaller --grid, "
            f"fewer --levels or dropping --colour all shorten it."
        )

    padded = pad_audio(audio, lead_in, lead_out, SAMPLE_RATE)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "created_utc": report.utc_now(),
        "generation": generation,
        # Generation A is not self-describing on the wire - its pilot preamble
        # carries no header - so the decoder needs this dict back verbatim.
        "gen_metadata": gen_metadata,
        "source": {
            "kind": payload_meta["kind"],
            "name": source_name or _source_name(source),
            "text": text,
        },
        "payload": payload_meta,
        "wire": {
            "sample_rate": SAMPLE_RATE,
            "generation": generation,
            "payload_symbols": int(info["n_symbols"]),
            "header_value": None if header_value is None else int(header_value),
            "header_hex": None if header_value is None else f"{int(header_value):04x}",
            "fec": "hamming74+interleave16" if generation == "B" else "none",
            "airtime_seconds": round(airtime, 3),
            "lead_in_seconds": float(lead_in),
            "lead_out_seconds": float(lead_out),
            "total_seconds": round(len(padded) / float(SAMPLE_RATE), 3),
        },
        "fsk_info": dict(info) if generation == "B" else None,
    }
    if generation == "B":
        manifest["wire"].update(
            symbol_ms=fsk.SYMBOL_MS, tones=int(fsk.M),
            f_low=float(fsk.TONES[0]), f_high=float(fsk.TONES[-1]),
            preamble_symbols=len(fsk.PREAMBLE),
            header_symbols=int(fsk.HEADER_SYMBOLS))
    else:
        manifest["wire"].update(
            f_low=gen_metadata["band"][0], f_high=gen_metadata["band"][1],
            frame_ms=gen_metadata["frame_duration"] * 1000,
            preamble_frames=gen_metadata["tel"]["preamble_frames"])

    return PrepareResult(audio=padded, manifest=manifest, sent_image=sent_image,
                         source_image=source_image, text=text, warnings=warnings)


def _source_name(source):
    if isinstance(source, (str, os.PathLike)):
        return os.path.basename(str(source))
    return None


def _activation_preview(activation, levels):
    """Gen-B has no 'as it goes on the wire' image, so render the quantized one."""
    from spectral.input.image_preprocessor import activation_to_png_bytes
    return Image.open(io.BytesIO(
        activation_to_png_bytes(activation, gray_levels=levels, scale=8)
    )).convert("RGB")


# --------------------------------------------------------------------------
# Writing a run folder
# --------------------------------------------------------------------------

def write_run(result, run_dir):
    """Drop tx.wav, the previews and manifest.json into a run folder."""
    os.makedirs(run_dir, exist_ok=True)
    artifacts = {}

    write_int16_wav(os.path.join(run_dir, "tx.wav"), result.audio, SAMPLE_RATE)
    artifacts["tx_wav"] = "tx.wav"

    if result.sent_image is not None:
        result.sent_image.save(os.path.join(run_dir, "sent.png"))
        artifacts["sent_png"] = "sent.png"
    if result.source_image is not None:
        preview = result.source_image.convert("RGB")
        preview.thumbnail((512, 512))
        preview.save(os.path.join(run_dir, "source.png"))
        artifacts["source_png"] = "source.png"
    if result.text:
        with open(os.path.join(run_dir, "sent.txt"), "w", encoding="utf-8") as handle:
            handle.write(result.text)
        artifacts["sent_txt"] = "sent.txt"

    result.manifest["run_id"] = os.path.basename(run_dir)
    result.manifest["artifacts"] = artifacts
    report.write_manifest(run_dir, result.manifest)
    return run_dir


def wav_bytes(result):
    return to_wav_bytes(result.audio, SAMPLE_RATE)
