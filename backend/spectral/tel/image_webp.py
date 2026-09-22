"""
RGB images over a voice call, at a resolution worth looking at.

Raw pixels cost too much air time: 96x96 RGB at 16 levels is 110 kbit, over
half an hour at the modem's ~57 bit/s. So the picture is compressed first and
the modem carries the file, not the pixels:

    image  -> WebP                       (96x96 RGB is ~7 kbit instead of 110)
           -> Reed-Solomon, 32 parity    (fixes up to 16 bad bytes per 255)
           -> keyed byte shuffle         (the scramble, and an interleaver)
           -> 16-FSK, length header      (fsk_codec, unchanged)
        ... the call ...
           -> the same steps backwards

Why each stage is shaped the way it is:

  Reed-Solomon instead of Hamming(7,4)
      A compressed file has no slack: one wrong bit can stop the WebP opening
      at all. RS works on whole bytes, corrects far more errors per codeword,
      and spends 32/255 of the stream on parity instead of Hamming's 3/7.

  The scramble moved from pixels to bytes
      security.scramble permutes pixel rows and columns. Doing that before
      WebP destroys the spatial correlation WebP exploits (a 96x96 picture
      grows ~5x). So the same caller|receiver|pin key now seeds a permutation
      of the coded bytes instead. A wrong PIN un-shuffles into bytes that
      Reed-Solomon cannot decode, so nothing comes back but static.

  The shuffle is always applied
      Open transmissions use a fixed public key. A lost 20 ms packet corrupts
      a run of adjacent bytes; shuffling spreads that run across many RS
      codewords so no single one gets more damage than it can repair.

  Self-describing
      Only the byte count travels in the header. Everything else the receiver
      needs is fixed here or derivable from that count, so decoding needs the
      audio (and the PIN if locked) and nothing from the sender.
"""

import hashlib
import io
import os
import sys

import numpy as np
from PIL import Image, ImageOps

try:
    import reedsolo
except ImportError as exc:                       # pragma: no cover
    raise ImportError("image_webp needs reedsolo: pip install reedsolo") from exc

import fsk_codec as fsk

_SPECTRAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SPECTRAL not in sys.path:
    sys.path.insert(0, _SPECTRAL)
from common.security import derive_key          # noqa: E402

DEFAULT_SIZE = 96          # longest side, in pixels
DEFAULT_QUALITY = 50       # WebP quality, 0-100
RS_PARITY = 32             # parity bytes per 255-byte RS block
OPEN_KEY = b"spectral-canvas|open"
MAX_PACKET_BYTES = 2 ** fsk.HEADER_BITS - 1


class TransmissionError(Exception):
    pass


# --------------------------------------------------------------------------
# Compression
# --------------------------------------------------------------------------

def compress(image, size=DEFAULT_SIZE, quality=DEFAULT_QUALITY):
    """image (path, bytes or PIL.Image) -> (resized PIL image, WebP bytes).

    Keeps the aspect ratio: the longest side becomes `size`.
    """
    if isinstance(image, (bytes, bytearray)):
        image = Image.open(io.BytesIO(image))
    elif not isinstance(image, Image.Image):
        image = Image.open(image)

    small = ImageOps.contain(image.convert("RGB"), (size, size), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "WEBP", quality=quality, method=6)
    return small, buf.getvalue()


# --------------------------------------------------------------------------
# Error correction + scramble
# --------------------------------------------------------------------------

def transmission_key(caller=None, receiver=None, pin=None):
    if caller is None and receiver is None and pin is None:
        return OPEN_KEY
    return derive_key(caller, receiver, pin)


def byte_permutation(n, key):
    """Same hash-chain idea as security.scramble, seeding one permutation of n bytes."""
    seed = hashlib.sha256(hashlib.sha256(key).digest() + b"bytes").digest()
    return np.random.default_rng(int.from_bytes(seed, "big")).permutation(n)


def protect(payload, key, parity=RS_PARITY):
    coded = np.frombuffer(bytes(reedsolo.RSCodec(parity).encode(payload)), np.uint8)
    return coded[byte_permutation(len(coded), key)].tobytes()


def unshuffle(received, key):
    rx = np.frombuffer(received, np.uint8)
    coded = np.empty_like(rx)
    coded[byte_permutation(len(rx), key)] = rx
    return coded.tobytes()


def unprotect(received, key, parity=RS_PARITY):
    """-> (payload bytes, number of bytes Reed-Solomon repaired)."""
    try:
        decoded, _, errata = reedsolo.RSCodec(parity).decode(unshuffle(received, key))
    except reedsolo.ReedSolomonError as exc:
        raise TransmissionError(
            "Too many damaged bytes to repair, or the numbers/PIN are wrong."
        ) from exc
    return bytes(decoded), len(errata)


# --------------------------------------------------------------------------
# Modem framing
# --------------------------------------------------------------------------

def modulate(packet):
    if len(packet) > MAX_PACKET_BYTES:
        raise ValueError(f"Packet is {len(packet)} bytes; the header allows "
                         f"{MAX_PACKET_BYTES}. Lower the size or quality.")
    bits = np.unpackbits(np.frombuffer(packet, np.uint8))
    header = np.array([int(b) for b in format(len(packet), f"0{fsk.HEADER_BITS}b")],
                      np.uint8)
    # Reed-Solomon replaces the modem's own Hamming + interleaving
    return fsk.modulate(bits, fec=False, header=header)


def demodulate(audio):
    """audio -> (packet bytes, sample offset of the preamble)."""
    length_bits, offset = fsk.read_header(audio)
    n_bytes = int("".join(str(int(b)) for b in length_bits), 2)
    info = {
        "fec": False,
        "has_header": True,
        "n_symbols": 2 * n_bytes,          # 8 bits = exactly 2 symbols, no pad
        "symbol_pad": 0,
        "n_payload_bits": 8 * n_bytes,
    }
    bits = fsk.demodulate(audio, info, offset=offset)
    return np.packbits(bits).tobytes()[:n_bytes], offset


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------

def plan(image, size=DEFAULT_SIZE, quality=DEFAULT_QUALITY, parity=RS_PARITY):
    """How many bytes and seconds a send will take, without making audio."""
    small, webp = compress(image, size, quality)
    packet_bytes = len(reedsolo.RSCodec(parity).encode(webp))
    symbols = len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS + 2 * packet_bytes
    return {
        "width": small.width, "height": small.height,
        "webp_bytes": len(webp), "packet_bytes": packet_bytes,
        "seconds": symbols * fsk.SYMBOL_MS / 1000.0,
    }


def send_image(image, size=DEFAULT_SIZE, quality=DEFAULT_QUALITY,
               caller=None, receiver=None, pin=None, parity=RS_PARITY):
    """-> (audio at fsk.SAMPLE_RATE, the picture as it goes on the wire, report).

    The returned picture is the decoded WebP, i.e. exactly what a perfect call
    would rebuild. report["compression_psnr"] is what WebP alone cost.
    """
    small, webp = compress(image, size, quality)
    packet = protect(webp, transmission_key(caller, receiver, pin), parity)
    audio, info = modulate(packet)
    on_wire = Image.open(io.BytesIO(webp)).convert("RGB")
    report = {
        "width": small.width, "height": small.height,
        "webp_bytes": len(webp), "packet_bytes": len(packet),
        "seconds": info["duration_seconds"],
        "locked": pin is not None,
        "compression_psnr": _psnr(on_wire, small),
    }
    return audio, on_wire, report


def _psnr(a, b):
    mse = np.mean((np.asarray(a, float) - np.asarray(b, float)) ** 2)
    return float("inf") if mse == 0 else float(10 * np.log10(255.0 ** 2 / mse))


def static_image(packet, side=None):
    """What a failed decode shows: the received bytes painted as RGB noise."""
    data = np.frombuffer(packet, np.uint8)
    if side is None:
        side = max(8, int(np.sqrt(len(data) / 3)))
    if len(data) == 0:
        data = np.zeros(1, np.uint8)
    pixels = np.resize(data, side * side * 3).reshape(side, side, 3)
    return Image.fromarray(pixels, "RGB")


def receive_image(audio, caller=None, receiver=None, pin=None, parity=RS_PARITY):
    """-> (PIL image or None, report). On failure report["static"] holds noise."""
    key = transmission_key(caller, receiver, pin)
    packet, offset = demodulate(audio)
    report = {"packet_bytes": len(packet), "offset": int(offset)}

    try:
        webp, repaired = unprotect(packet, key, parity)
        image = Image.open(io.BytesIO(webp))
        image.load()
    except (TransmissionError, OSError) as exc:
        report.update(ok=False, reason=str(exc) or type(exc).__name__,
                      static=static_image(unshuffle(packet, key)))
        return None, report

    report.update(ok=True, repaired_bytes=repaired, webp_bytes=len(webp),
                  width=image.width, height=image.height)
    return image.convert("RGB"), report
