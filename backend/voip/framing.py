"""The 16 header bits, and refusing to invent payload that is not there.

A transmission is ``[8 preamble][7 header][payload]``. The header carries
exactly 16 bits, which is all the out-of-band information a phone call allows:
there is no file container to hang metadata off.

Two payload generations share those 16 bits.

**Generation C** (``image_webp``) spends all 16 on the packet byte count.
Everything else the receiver needs is either fixed in code or derivable from
that count. This package does not change that format by one bit -- audio
prepared here is still decodable by ``image_webp.receive_image`` and by the
existing web page.

**Generation B** (``image_fsk``, raw quantized pixels) had no header at all.
``encode_image`` calls ``fsk.modulate`` without one, and the image's shape and
gray-level count live only in the sender's ``info`` dict. That is fine when the
sender hands the dict to the receiver in the same Python process, and useless
over a call, where the receiver has nothing but audio. So Generation B gets a
descriptor packed into the same 16 bits:

    bit 15..12   0xF   marker
    bit 11.. 7   rows - 1      (1..32)
    bit  6.. 2   cols - 1      (1..32)
    bit  1.. 0   levels code   0=2, 1=4, 2=16, 3=256

The marker cannot be mistaken for a Generation C byte count, because 0xF000 is
61440 and a packet that size is 82 minutes of air time. Everything else about
the frame -- the Hamming padding, the interleaver padding, the symbol count --
follows arithmetically from those twelve bits, so Generation B becomes fully
self-describing without touching the modem.

The other job here is a bounds check. ``fsk._symbol_magnitudes`` zero-pads when
the audio runs out, so a recording stopped a few seconds early decodes into
fabricated bytes and reports nothing wrong. Measured on this modem: a header
claiming 600 bytes against audio holding 592 symbols returned a full 600 bytes,
of which 297 matched. ``demodulate_frame`` clamps to what exists and says how
much was missing.
"""

from dataclasses import dataclass, field

import numpy as np

from voip import _tel
from voip.config import (
    CODE_LEVELS,
    GEN_B_MARKER,
    GEN_B_MAX_SIDE,
    HEADER_BITS,
    LEVEL_CODES,
    SYMBOL_SAMPLES,
    FrameError,
)
from voip.dsp import available_symbols, symbol_decisions, symbol_magnitudes

GEN_B_FLOOR = GEN_B_MARKER << 12          # 61440


@dataclass
class Frame:
    """What the header said, and where the payload therefore starts."""

    generation: str                  # "B" or "C"
    header_value: int
    n_symbols: int                   # payload symbols the header implies
    data_start: int                  # first payload sample
    info: dict                       # ready to hand to fsk.demodulate
    rows: int | None = None
    cols: int | None = None
    levels: int | None = None
    packet_bytes: int | None = None
    warnings: list = field(default_factory=list)

    @property
    def header_hex(self):
        return f"{self.header_value:04x}"

    def as_dict(self):
        out = {
            "generation": self.generation,
            "header_value": int(self.header_value),
            "header_hex": self.header_hex,
            "expected_symbols": int(self.n_symbols),
        }
        if self.generation == "B":
            out.update(rows=self.rows, cols=self.cols, levels=self.levels,
                       payload_bits=int(self.info["n_payload_bits"]))
        else:
            out.update(packet_bytes=int(self.packet_bytes))
        return out


# --------------------------------------------------------------------------
# Header bits
# --------------------------------------------------------------------------

def _to_bits(value, width=HEADER_BITS):
    return np.array([int(b) for b in format(int(value), f"0{width}b")], dtype=np.uint8)


def _from_bits(bits):
    return int("".join(str(int(b)) for b in np.asarray(bits).ravel()[:HEADER_BITS]), 2)


def build_genc_header(packet_bytes):
    """Generation C: the packet byte count, exactly as image_webp writes it."""
    packet_bytes = int(packet_bytes)
    if not 0 < packet_bytes < GEN_B_FLOOR:
        raise FrameError(
            f"A Generation C packet of {packet_bytes} bytes will not fit a 16-bit "
            f"header (limit {GEN_B_FLOOR - 1}). Lower --size or --quality."
        )
    return _to_bits(packet_bytes)


def build_genb_header(rows, cols, levels):
    """Generation B: the self-describing picture descriptor."""
    rows, cols, levels = int(rows), int(cols), int(levels)
    if not 1 <= rows <= GEN_B_MAX_SIDE or not 1 <= cols <= GEN_B_MAX_SIDE:
        raise FrameError(
            f"Generation B carries up to {GEN_B_MAX_SIDE}x{GEN_B_MAX_SIDE} pixels; "
            f"asked for {rows}x{cols}. Use --gen C for anything larger."
        )
    if levels not in LEVEL_CODES:
        raise FrameError(
            f"Gray levels must be one of {sorted(LEVEL_CODES)}; got {levels}."
        )
    value = (GEN_B_MARKER << 12) | ((rows - 1) << 7) | ((cols - 1) << 2) | LEVEL_CODES[levels]
    return _to_bits(value)


def parse_header(bits):
    """16 bits -> what they describe. Never raises; the caller validates."""
    value = _from_bits(bits)
    if value >= GEN_B_FLOOR:
        return {
            "generation": "B",
            "header_value": value,
            "rows": ((value >> 7) & 0x1F) + 1,
            "cols": ((value >> 2) & 0x1F) + 1,
            "levels": CODE_LEVELS[value & 0x03],
        }
    return {"generation": "C", "header_value": value, "packet_bytes": value}


# --------------------------------------------------------------------------
# Deriving the rest of the frame from the header
# --------------------------------------------------------------------------

def bits_per_pixel(levels):
    return int(np.log2(int(levels)))


def genb_info(rows, cols, levels):
    """Reconstruct fsk.modulate's info dict from the descriptor alone.

    Mirrors modulate()'s own order of operations: Hamming(7,4) first, then the
    depth-16 block interleaver, then 4 bits to a symbol. Each pad is forced, so
    none of them has to travel on the wire.
    """
    payload = int(rows) * int(cols) * bits_per_pixel(levels)
    hamming_pad = (-payload) % 4
    coded = (payload + hamming_pad) // 4 * 7
    interleave_pad = (-coded) % _tel.fsk().INTERLEAVE_DEPTH
    total = coded + interleave_pad
    return {
        "n_payload_bits": payload,
        "n_symbols": total // 4,
        "fec": True,
        "hamming_pad": hamming_pad,
        "interleave_pad": interleave_pad,
        "symbol_pad": (-total) % 4,      # always 0: total is a multiple of 16
        "has_header": True,
    }


def genc_info(packet_bytes):
    """Generation C carries Reed-Solomon bytes raw: 8 bits = exactly 2 symbols."""
    packet_bytes = int(packet_bytes)
    return {
        "n_payload_bits": 8 * packet_bytes,
        "n_symbols": 2 * packet_bytes,
        "fec": False,
        "symbol_pad": 0,
        "has_header": True,
    }


# --------------------------------------------------------------------------
# Reading a frame out of audio
# --------------------------------------------------------------------------

def read_header_bits(audio, offset):
    """The 7 header symbols, de-interleaved and Hamming-corrected."""
    fsk = _tel.fsk()
    start = int(offset) + len(fsk.PREAMBLE) * SYMBOL_SAMPLES
    if available_symbols(audio, start) < fsk.HEADER_SYMBOLS:
        raise FrameError(
            "The recording ends inside the header. It was probably stopped "
            "too early, or the preamble was found near the very end."
        )
    mags = symbol_magnitudes(audio, start, fsk.HEADER_SYMBOLS)
    symbols, margin = symbol_decisions(mags)
    bits = ((symbols[:, None] >> np.array([3, 2, 1, 0])) & 1).astype(np.uint8).ravel()
    # modulate() spreads the header's 4 codewords across its 7 symbols so one
    # bad symbol costs each codeword a single bit; undo that before decoding
    bits = bits[:28].reshape(7, 4).T.ravel()
    return fsk.hamming_decode(bits)[:HEADER_BITS], margin


def read_frame(audio, offset, expect="auto", rs_parity=None):
    """Decode the header and work out the whole frame shape.

    `expect` is "auto", "B" or "C". Forcing it matters for one legacy case:
    spectral/tel/demo.py writes a Generation B tx.wav whose header holds a raw
    payload *bit* count, which has no marker and so reads as Generation C.
    """
    fsk = _tel.fsk()
    bits, header_margin = read_header_bits(audio, offset)
    parsed = parse_header(bits)
    warnings = []

    generation = parsed["generation"]
    if expect in ("B", "C") and expect != generation:
        if expect == "C":
            parsed = {"generation": "C", "header_value": parsed["header_value"],
                      "packet_bytes": parsed["header_value"]}
            generation = "C"
            warnings.append("Header looked like Generation B but --gen C was forced.")
        else:
            raise FrameError(
                f"--gen B was forced but the header reads {parsed['header_value']}, "
                f"which is not a Generation B descriptor."
            )

    data_start = int(offset) + (len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS) * SYMBOL_SAMPLES

    if generation == "B":
        info = genb_info(parsed["rows"], parsed["cols"], parsed["levels"])
        frame = Frame(
            generation="B", header_value=parsed["header_value"],
            n_symbols=info["n_symbols"], data_start=data_start, info=info,
            rows=parsed["rows"], cols=parsed["cols"], levels=parsed["levels"],
            warnings=warnings,
        )
    else:
        packet_bytes = parsed["packet_bytes"]
        if packet_bytes <= 0:
            raise FrameError(
                "The header decoded to a packet of zero bytes. The first second "
                "of the transmission is too damaged to read."
            )
        # Generalises tel_pipeline._find_transmission: a packet at or below the
        # parity size cannot contain a payload, so this is noise that happened
        # to score well rather than a real header.
        floor = rs_parity if rs_parity is not None else _tel.image_webp().RS_PARITY
        if packet_bytes <= floor:
            raise FrameError(
                f"The header claims a {packet_bytes}-byte packet, which is smaller "
                f"than the {floor} bytes of error-correction every packet carries. "
                f"This is not a transmission."
            )
        info = genc_info(packet_bytes)
        frame = Frame(
            generation="C", header_value=parsed["header_value"],
            n_symbols=info["n_symbols"], data_start=data_start, info=info,
            packet_bytes=packet_bytes, warnings=warnings,
        )

    if float(np.mean(header_margin)) < 2.0:
        frame.warnings.append(
            "The header symbols were marginal, so the payload length may be wrong."
        )
    return frame


def frame_bounds(audio, frame):
    """How much of the payload the recording actually contains."""
    have = available_symbols(audio, frame.data_start)
    want = int(frame.n_symbols)
    missing = max(0, want - have)
    return {
        "expected_symbols": want,
        "available_symbols": int(min(have, want)),
        "truncated_symbols": missing,
        "truncated": missing > 0,
        "truncated_seconds": round(missing * SYMBOL_SAMPLES / _tel.fsk().SAMPLE_RATE, 3),
    }


def demodulate_frame(audio, frame, clamp=True):
    """(bits, bounds) -- never reads past the end of the recording.

    Without the clamp the modem silently analyses zero-padded silence and hands
    back confident garbage. With it, a short recording produces fewer bits and
    an honest `truncated` count.
    """
    fsk = _tel.fsk()
    bounds = frame_bounds(audio, frame)

    info = dict(frame.info)
    if bounds["truncated"]:
        if not clamp:
            raise FrameError(
                f"The recording is {bounds['truncated_symbols']} symbols "
                f"({bounds['truncated_seconds']:.1f} s) short of the "
                f"{bounds['expected_symbols']} the header asks for."
            )
        info["n_symbols"] = bounds["available_symbols"]

    mags = symbol_magnitudes(audio, frame.data_start, info["n_symbols"])
    symbols, margin = symbol_decisions(mags)
    bits = fsk.demodulate(audio, info, offset=frame_offset(frame))
    return bits, symbols, margin, bounds


def frame_offset(frame):
    """The preamble offset fsk.demodulate expects, recovered from data_start."""
    fsk = _tel.fsk()
    return frame.data_start - (len(fsk.PREAMBLE) + fsk.HEADER_SYMBOLS) * SYMBOL_SAMPLES
