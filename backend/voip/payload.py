"""What the modem carries: a picture, or a piece of text.

Three payload shapes, two of them already built by the teammate and imported
here unchanged.

**Generation C** -- ``image_webp``: WebP, then Reed-Solomon, then a byte
shuffle keyed on the caller, receiver and PIN. Real RGB pictures at 96-160 px,
at the cost of roughly a minute of air time. This module adds no new bytes to
that format, so audio prepared here still opens in ``image_webp.receive_image``
and on the existing web page.

**Generation B** -- ``image_fsk``: raw quantized pixels with Hamming(7,4).
Caps out around 32x32 grayscale, but a 16x16 at 4 levels is 9.6 seconds, which
is what makes it the right thing to put through the first few real calls. It
also degrades gracefully: a bad symbol costs you pixels, where Reed-Solomon
past its limit costs you the entire picture.

**Text** goes through the Generation C pipeline as ``b"SCTX" + utf8``.

Why not ``spectral/text/text_codec.py``: it is 44.1 kHz MFSK between 2 and
5 kHz with no error correction. A voice channel keeps roughly 300-3400 Hz, so
most of its tones do not arrive at all -- the same reason the original 64-row
image encoder had to be abandoned for this path. Reusing Generation C instead
gives byte-exact text or an honest failure, keeps the PIN lock, and adds no new
code path. Images stay raw WebP with no prefix, so only text carries the magic
and the format stays backward compatible.
"""

import io

import numpy as np
from PIL import Image

from voip import _tel
from voip.config import (
    DEFAULT_PARITY,
    TEXT_MAGIC,
    VoipError,
    WEBP_RIFF,
    WEBP_TAG,
)


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------

def transmission_key(caller=None, receiver=None, pin=None):
    """Locked -> key from caller|receiver|PIN. Open -> the fixed public key.

    Open transmissions are still shuffled. The shuffle doubles as a byte-level
    interleaver: a lost 20 ms packet wrecks a run of adjacent bytes, and
    spreading that run across many Reed-Solomon blocks keeps any one block
    inside its 16-byte repair budget.
    """
    return _tel.image_webp().transmission_key(caller, receiver, pin)


# --------------------------------------------------------------------------
# Sniffing what came back
# --------------------------------------------------------------------------

def sniff(payload):
    """"image" | "text" | "unknown" from the first few bytes."""
    data = bytes(payload)
    if data[:4] == TEXT_MAGIC:
        return "text"
    if len(data) >= 12 and data[:4] == WEBP_RIFF and data[8:12] == WEBP_TAG:
        return "image"
    return "unknown"


def open_payload(payload):
    """Reed-Solomon output -> the thing it encodes."""
    kind = sniff(payload)
    if kind == "text":
        raw = bytes(payload)[len(TEXT_MAGIC):]
        try:
            return {"kind": "text", "text": raw.decode("utf-8"),
                    "characters": len(raw.decode("utf-8")), "bytes": len(raw)}
        except UnicodeDecodeError as exc:
            raise VoipError(
                f"The text came through but is not valid UTF-8: {exc}"
            ) from exc
    if kind == "image":
        image = Image.open(io.BytesIO(bytes(payload)))
        image.load()
        return {"kind": "image", "image": image.convert("RGB"),
                "webp_bytes": len(payload),
                "width": image.width, "height": image.height}
    raise VoipError(
        "The error correction succeeded but the result is neither a WebP "
        "picture nor Spectral Canvas text. The numbers or PIN are probably "
        "for a different transmission."
    )


# --------------------------------------------------------------------------
# Building a packet (Generation C wire format)
# --------------------------------------------------------------------------

def build_image_packet(image, size, quality, key, parity=DEFAULT_PARITY):
    """Picture -> (packet bytes, the image as it goes on the wire, meta)."""
    webp = _tel.image_webp()
    small, encoded = webp.compress(image, size, quality)
    packet = webp.protect(encoded, key, parity)
    on_wire = Image.open(io.BytesIO(encoded)).convert("RGB")
    meta = {
        "kind": "image",
        "width": small.width, "height": small.height,
        "quality": int(quality), "requested_size": int(size),
        "webp_bytes": len(encoded), "packet_bytes": len(packet),
        "rs_parity": int(parity),
        "compression_psnr": _psnr(on_wire, small),
    }
    return packet, on_wire, meta


def build_text_packet(text, key, parity=DEFAULT_PARITY):
    """Text -> (packet bytes, meta). Same wire format as a picture."""
    if not text or not text.strip():
        raise VoipError("There is no text to send.")
    raw = text.encode("utf-8")
    packet = _tel.image_webp().protect(TEXT_MAGIC + raw, key, parity)
    return packet, {
        "kind": "text",
        "characters": len(text), "text_bytes": len(raw),
        "packet_bytes": len(packet), "rs_parity": int(parity),
    }


def unprotect(packet, key, parity=DEFAULT_PARITY):
    """(payload bytes, bytes Reed-Solomon repaired). Raises past its limit."""
    return _tel.image_webp().unprotect(bytes(packet), key, parity)


def static_image(packet, key):
    """What a failed decode shows: the un-shuffled bytes painted as noise.

    A wrong PIN is not an error, it is static -- the behaviour the whole
    project is built around. This is the same picture the web page draws.
    """
    webp = _tel.image_webp()
    return webp.static_image(webp.unshuffle(bytes(packet), key))


def rs_blocks(packet_bytes, parity=DEFAULT_PARITY):
    """How many 255-byte Reed-Solomon blocks a packet spans, and the repair budget.

    Useful in a report: RS is all-or-nothing, so knowing you repaired 11 bytes
    out of a 16-per-block budget is the difference between "it worked" and "it
    nearly did not".
    """
    parity = int(parity)
    chunk = 255 - parity
    data_bytes = max(0, int(packet_bytes) - parity)
    blocks = max(1, -(-data_bytes // chunk)) if data_bytes else 1
    return {"blocks": blocks, "rs_parity": parity,
            "rs_limit_per_block": parity // 2, "rs_limit_total": blocks * (parity // 2)}


# --------------------------------------------------------------------------
# Generation B
# --------------------------------------------------------------------------

def build_genb_bits(activation, levels):
    """Quantized activation matrix -> flat bits, MSB first per pixel."""
    activation = np.asarray(activation, dtype=np.float64)
    if activation.ndim != 2:
        raise VoipError(
            "Generation B carries grayscale only. Use --gen C for a colour picture."
        )
    bits = _tel.image_fsk().activation_to_bits(activation, levels)
    rows, cols = activation.shape
    return bits, {
        "kind": "image", "rows": int(rows), "cols": int(cols),
        "levels": int(levels), "payload_bits": int(len(bits)),
    }


def genb_bits_to_activation(bits, rows, cols, levels):
    return _tel.image_fsk().bits_to_activation(bits, (int(rows), int(cols)), int(levels))


def _psnr(a, b):
    mse = float(np.mean((np.asarray(a, float) - np.asarray(b, float)) ** 2))
    return None if mse == 0 else float(10 * np.log10(255.0 ** 2 / mse))
