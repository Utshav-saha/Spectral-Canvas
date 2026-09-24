"""What a call transmission is, written into the transmission itself.

A recording of a real call is bare audio: no WAV chunk, no session, nothing
but the tones. Until this existed the receiver had to be told the grid,
generation and level count by picking the send the recording came from, which
only works on the machine that did the sending - and picking the wrong one
reads the wrong length, which the page reports as "recording stops before the
transmission ends" even when the recording is complete.

So every Call-page transmission now opens with a descriptor, straight after the
FSK preamble, in the modem's header slot:

    bit 31..28   0x5          marker
    bit 27       generation   0 = A, 1 = B
    bit 26..20   rows - 1     (1..128)
    bit 19..13   cols - 1     (1..128)
    bit 12       colour       1 = RGB, three channel passes
    bit 11..10   levels       2 ** (code + 1): 2, 4, 8, 16
    bit  9       locked       permutation lock in use
    bit  8       fec          Hamming(7,4) on the payload (Generation B)
    bit  7.. 0   CRC-8        over bits 31..8, polynomial 0x07

Hamming repairs one bad symbol per 16-bit block; the CRC is what catches two,
so a damaged descriptor is refused rather than believed. The 32 bits go out
twice (4 blocks, 28 symbols, 1.12 s) and the first copy that checks out wins,
because a real call loses the odd symbol and this is the one part of the
transmission with no second chance.

The re-sync interval is not carried: it is fsk_codec.RESYNC_INTERVAL on both
ends. voip/framing.py's 16-bit descriptor (marker 0xF) is a different frame
read by a different reader; the two never meet.
"""

import numpy as np

MARKER = 0x5
BITS = 32
COPIES = 2
LEVELS = (2, 4, 8, 16)
MAX_SIDE = 128


def _crc8(value, width=24):
    crc = 0
    for i in range(width - 1, -1, -1):
        bit = (value >> i) & 1
        top = (crc >> 7) & 1
        crc = (crc << 1) & 0xFF
        if bit ^ top:
            crc ^= 0x07
    return crc


def pack(generation, rows, cols, colour, levels, locked, fec=True):
    """-> the descriptor as a bit array, COPIES times over, MSB first."""
    rows, cols, levels = int(rows), int(cols), int(levels)
    if generation not in ("A", "B"):
        raise ValueError("Generation must be A or B.")
    if not (1 <= rows <= MAX_SIDE and 1 <= cols <= MAX_SIDE):
        raise ValueError(f"A call carries at most {MAX_SIDE} pixels a side.")
    if levels not in LEVELS:
        raise ValueError(f"Gray levels must be one of {', '.join(map(str, LEVELS))}.")

    body = ((MARKER << 20)
            | ((1 if generation == "B" else 0) << 19)
            | ((rows - 1) << 12)
            | ((cols - 1) << 5)
            | ((1 if colour else 0) << 4)
            | (LEVELS.index(levels) << 2)
            | ((1 if locked else 0) << 1)
            | (1 if fec else 0))
    value = (body << 8) | _crc8(body)
    one = np.array([(value >> i) & 1 for i in range(BITS - 1, -1, -1)], dtype=np.uint8)
    return np.tile(one, COPIES)


def unpack(bits):
    """Bits read off the wire -> dict, or None if no copy checks out."""
    bits = np.asarray(bits, dtype=np.uint8).ravel()
    for c in range(len(bits) // BITS):
        value = 0
        for b in bits[c * BITS:(c + 1) * BITS]:
            value = (value << 1) | int(b)
        body, crc = value >> 8, value & 0xFF
        if body >> 20 != MARKER or _crc8(body) != crc:
            continue
        levels_code = (body >> 2) & 0x3
        return {
            "generation": "B" if (body >> 19) & 1 else "A",
            "rows": ((body >> 12) & 0x7F) + 1,
            "cols": ((body >> 5) & 0x7F) + 1,
            "colour": bool((body >> 4) & 1),
            "gray_levels": LEVELS[levels_code],
            "locked": bool((body >> 1) & 1),
            "fec": bool(body & 1),
            "copy": c,
        }
    return None
