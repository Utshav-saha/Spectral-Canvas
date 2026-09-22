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
    DEFAULT_PARITY,
    DEFAULT_QUALITY,
    DEFAULT_SIZE,
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

def plan(source=None, text=None, generation="C", size=DEFAULT_SIZE,
         quality=DEFAULT_QUALITY, grid=DEFAULT_GRID, levels=DEFAULT_LEVELS,
         parity=DEFAULT_PARITY, lead_in=DEFAULT_LEAD_IN_S,
         lead_out=DEFAULT_LEAD_OUT_S):
    """Bytes and seconds, without building the waveform. Drives --dry-run."""
    fsk = _tel.fsk()
    overhead = (len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS) * fsk.SYMBOL_MS / 1000.0

    if generation == "B":
        if text is not None:
            raise VoipError("Text goes over Generation C. Drop --gen B, or use --as-image.")
        # Validate before quoting a time: without this, --dry-run cheerfully
        # plans a two-minute call for a grid the 16-bit header cannot describe.
        framing.build_genb_header(grid, grid, levels)
        info = framing.genb_info(grid, grid, levels)
        seconds = info["n_symbols"] * fsk.SYMBOL_MS / 1000.0 + overhead
        out = {"generation": "B", "rows": grid, "cols": grid, "levels": levels,
               "payload_bits": info["n_payload_bits"], "symbols": info["n_symbols"]}
    else:
        webp = _tel.image_webp()
        if text is not None:
            raw = payload.TEXT_MAGIC + text.encode("utf-8")
            packet_bytes = len(webp.reedsolo.RSCodec(parity).encode(raw))
            out = {"generation": "C", "kind": "text", "characters": len(text),
                   "packet_bytes": packet_bytes}
        else:
            planned = webp.plan(_open_image(source), size, quality, parity)
            packet_bytes = planned["packet_bytes"]
            out = {"generation": "C", "kind": "image", "packet_bytes": packet_bytes,
                   "webp_bytes": planned["webp_bytes"],
                   "width": planned["width"], "height": planned["height"]}
        seconds = 2 * packet_bytes * fsk.SYMBOL_MS / 1000.0 + overhead
        out["symbols"] = 2 * packet_bytes

    out["airtime_seconds"] = round(seconds, 2)
    out["total_seconds"] = round(seconds + lead_in + lead_out, 2)
    return out


# --------------------------------------------------------------------------
# Building the transmission
# --------------------------------------------------------------------------

def prepare(source=None, text=None, generation="C", size=DEFAULT_SIZE,
            quality=DEFAULT_QUALITY, grid=DEFAULT_GRID, levels=DEFAULT_LEVELS,
            as_image=False, locked=False, caller=None, receiver=None, pin=None,
            parity=DEFAULT_PARITY, lead_in=DEFAULT_LEAD_IN_S,
            lead_out=DEFAULT_LEAD_OUT_S, source_name=None):
    """Source -> PrepareResult holding the padded audio and a full manifest."""
    if source is None and text is None:
        raise VoipError("Nothing to send: give a picture or some text.")
    if generation not in ("B", "C"):
        raise VoipError(f"Generation must be B or C, not {generation!r}.")

    fsk = _tel.fsk()
    warnings = []

    if locked:
        # Reuses the app's own rule: two 11-digit numbers and a 4-8 digit PIN.
        from app.services.pipeline import validate_credentials
        validate_credentials(caller, receiver, pin)
        creds = (caller, receiver, pin)
    else:
        creds = (None, None, None)

    # Text rendered as a picture is a demo flourish, not the default path.
    if text is not None and as_image:
        from spectral.input.image_preprocessor import render_text_image
        source = render_text_image(text, target_width=max(64, grid),
                                   target_height=max(64, grid))
        source_name = source_name or "rendered-text"
        text = None

    source_image = None
    sent_image = None

    if generation == "B":
        if text is not None:
            raise VoipError("Text goes over Generation C. Drop --gen B, or add --as-image.")
        source_image = _open_image(source)
        activation = _quantize_for_genb(source_image, grid, levels)
        bits, payload_meta = payload.build_genb_bits(activation, levels)
        header = framing.build_genb_header(payload_meta["rows"], payload_meta["cols"], levels)
        audio, info = fsk.modulate(bits, fec=True, header=header)
        payload_meta["locked"] = False
        if locked:
            warnings.append(
                "Generation B has no PIN lock; the scramble lives in the "
                "Generation C byte shuffle. Sent open."
            )
        sent_image = _activation_preview(activation, levels)
        header_value = int("".join(map(str, header)), 2)

    else:
        key = payload.transmission_key(*creds)
        if text is not None:
            packet, payload_meta = payload.build_text_packet(text, key, parity)
        else:
            source_image = _open_image(source)
            packet, sent_image, payload_meta = payload.build_image_packet(
                source_image, size, quality, key, parity)
        payload_meta["locked"] = bool(locked)
        payload_meta["sha256_packet"] = _sha256(packet)
        # image_webp.modulate builds the header itself, so the bits on the wire
        # stay identical to what the web page produces
        audio, info = _tel.image_webp().modulate(packet)
        header_value = payload_meta["packet_bytes"]

    airtime = len(audio) / float(SAMPLE_RATE)
    if airtime > LONG_TRANSMISSION_WARN_S:
        warnings.append(
            f"This is {airtime / 60:.1f} minutes of call. "
            f"--size 96 is about half that, and --gen B about a tenth."
        )

    padded = pad_audio(audio, lead_in, lead_out, SAMPLE_RATE)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "created_utc": report.utc_now(),
        "generation": generation,
        "source": {
            "kind": payload_meta["kind"],
            "name": source_name or _source_name(source),
            "text": text,
        },
        "payload": payload_meta,
        "wire": {
            "sample_rate": SAMPLE_RATE,
            "symbol_ms": fsk.SYMBOL_MS,
            "tones": int(fsk.M),
            "f_low": float(fsk.TONES[0]),
            "f_high": float(fsk.TONES[-1]),
            "preamble_symbols": len(fsk.PREAMBLE),
            "header_symbols": int(fsk.HEADER_SYMBOLS),
            "payload_symbols": int(info["n_symbols"]),
            "header_value": int(header_value),
            "header_hex": f"{int(header_value):04x}",
            "fec": "hamming74+interleave16" if generation == "B" else "reed-solomon",
            "airtime_seconds": round(airtime, 3),
            "lead_in_seconds": float(lead_in),
            "lead_out_seconds": float(lead_out),
            "total_seconds": round(len(padded) / float(SAMPLE_RATE), 3),
        },
        "fsk_info": dict(info),
    }

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
