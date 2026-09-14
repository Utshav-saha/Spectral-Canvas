"""
16-FSK modem for a telephony voice channel.

One tone per 40 ms symbol, chosen from 16 frequencies spanning 700-3200 Hz.
4 bits per symbol, 100 bit/s raw, ~57 bit/s after Hamming(7,4).

Why this and not your spectrogram:
    A speech codec fits an 8-pole LPC envelope to each 20 ms frame. It can
    reproduce WHERE a spectral peak is with great accuracy, and its HEIGHT
    with almost none. So put the information in which tone is present, never
    in how loud it is. The decoder only ever does argmax over 16 bins - it
    never compares a magnitude against a threshold.

Layout of a transmission:
    [ preamble: 8 symbols, alternating lowest and highest tone ]
    [ payload:  Hamming(7,4) coded, block-interleaved, 4 bits per symbol ]
"""

import numpy as np
from scipy.signal.windows import tukey

SAMPLE_RATE = 8000
SYMBOL_MS = 40
SYMBOL_SAMPLES = SAMPLE_RATE * SYMBOL_MS // 1000        # 320 = 2 GSM frames
BIN_WIDTH = SAMPLE_RATE / SYMBOL_SAMPLES                # 25.0 Hz
M = 16
BITS_PER_SYMBOL = 4
F_LOW, F_HIGH = 700.0, 3200.0
TUKEY_ALPHA = 0.25
INTERLEAVE_DEPTH = 16

TONES = np.round(np.linspace(F_LOW, F_HIGH, M) / BIN_WIDTH) * BIN_WIDTH
TONE_BINS = np.round(TONES / BIN_WIDTH).astype(int)
PREAMBLE = np.array([0, M - 1, 0, M - 1, 0, M - 1, 0, M - 1])

# Fixed-size header, sent Hamming-coded but NOT interleaved, so the receiver
# can read it without knowing the payload length first.
HEADER_BITS = 16
HEADER_SYMBOLS = 7          # 16 data bits -> 28 coded bits -> 7 symbols


# --------------------------------------------------------------------------
# Hamming(7,4) - corrects one bit error per 7-bit codeword
# --------------------------------------------------------------------------

_G = np.array([[1, 1, 0, 1], [1, 0, 1, 1], [1, 0, 0, 0],
               [0, 1, 1, 1], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
_H = np.array([[1, 0, 1, 0, 1, 0, 1], [0, 1, 1, 0, 0, 1, 1],
               [0, 0, 0, 1, 1, 1, 1]])


def hamming_encode(bits):
    bits = np.asarray(bits, dtype=np.uint8)
    pad = (-len(bits)) % 4
    bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
    words = bits.reshape(-1, 4)
    return (words @ _G.T % 2).astype(np.uint8).ravel(), pad


def hamming_decode(bits):
    words = np.asarray(bits, dtype=np.uint8).reshape(-1, 7)
    syndrome = (words @ _H.T % 2)
    position = syndrome @ np.array([1, 2, 4])
    fixed = words.copy()
    for i, p in enumerate(position):
        if p:
            fixed[i, p - 1] ^= 1
    return fixed[:, [2, 4, 5, 6]].ravel()


# --------------------------------------------------------------------------
# Interleaving - spreads a burst loss across many codewords
# --------------------------------------------------------------------------

def interleave(bits, depth=INTERLEAVE_DEPTH):
    pad = (-len(bits)) % depth
    bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
    return bits.reshape(-1, depth).T.ravel(), pad


def deinterleave(bits, depth=INTERLEAVE_DEPTH):
    return np.asarray(bits).reshape(depth, -1).T.ravel()


# --------------------------------------------------------------------------
# Modulation
# --------------------------------------------------------------------------

def _tone(index, t, window):
    return np.sin(2 * np.pi * TONES[index] * t) * window


def _to_symbols(bits):
    pad = (-len(bits)) % BITS_PER_SYMBOL
    bits = np.concatenate([np.asarray(bits, np.uint8),
                           np.zeros(pad, dtype=np.uint8)])
    return bits.reshape(-1, BITS_PER_SYMBOL) @ np.array([8, 4, 2, 1]), pad


def modulate(bits, fec=True, header=None):
    """header: optional 16-bit array describing the payload, sent uncoded of
    interleaving so the receiver can read it standalone."""
    bits = np.asarray(bits, dtype=np.uint8)
    n_payload = len(bits)

    if fec:
        coded, ham_pad = hamming_encode(bits)
        coded, il_pad = interleave(coded)
    else:
        coded, ham_pad, il_pad = bits, 0, 0

    symbols, pad = _to_symbols(coded)

    t = np.arange(SYMBOL_SAMPLES) / SAMPLE_RATE
    window = tukey(SYMBOL_SAMPLES, alpha=TUKEY_ALPHA)

    head = []
    if header is not None:
        hcoded, _ = hamming_encode(np.asarray(header, np.uint8)[:HEADER_BITS])
        # interleave across the 4 codewords so one bad symbol costs each
        # codeword a single bit, which Hamming can then repair
        hcoded = hcoded.reshape(4, 7).T.ravel()
        hsym, _ = _to_symbols(hcoded)
        head = [_tone(s, t, window) for s in hsym[:HEADER_SYMBOLS]]

    audio = np.concatenate(
        [_tone(s, t, window) for s in PREAMBLE] + head +
        [_tone(s, t, window) for s in symbols]
    )
    audio = audio / max(np.max(np.abs(audio)), 1e-12) * 0.7

    info = {
        "n_payload_bits": int(n_payload),
        "n_symbols": int(len(symbols)),
        "fec": bool(fec),
        "hamming_pad": int(ham_pad),
        "interleave_pad": int(il_pad),
        "symbol_pad": int(pad),
        "sample_rate": SAMPLE_RATE,
        "symbol_ms": SYMBOL_MS,
        "has_header": header is not None,
        "duration_seconds": len(audio) / SAMPLE_RATE,
    }
    return audio, info


# --------------------------------------------------------------------------
# Demodulation
# --------------------------------------------------------------------------

def _symbol_magnitudes(audio, offset, count):
    need = count * SYMBOL_SAMPLES
    seg = audio[offset:offset + need]
    if len(seg) < need:
        seg = np.concatenate([seg, np.zeros(need - len(seg))])
    frames = seg.reshape(count, SYMBOL_SAMPLES) * np.hanning(SYMBOL_SAMPLES)
    return np.abs(np.fft.rfft(frames, axis=1))[:, TONE_BINS]


def find_preamble(audio, search_seconds=3.0, step=4):
    """Slide the symbol grid and look for the alternating low/high pattern."""
    limit = min(len(audio) - (len(PREAMBLE) + 2) * SYMBOL_SAMPLES,
                int(search_seconds * SAMPLE_RATE))
    if limit <= 0:
        return 0

    def score(offset):
        mags = _symbol_magnitudes(audio, offset, len(PREAMBLE))
        total = mags.sum(axis=1) + 1e-12
        # reward energy concentrated in the expected tone of each symbol
        return float(np.mean(mags[np.arange(len(PREAMBLE)), PREAMBLE] / total))

    coarse = max(range(0, limit, step), key=score)
    fine = max(range(max(0, coarse - step), coarse + step + 1), key=score)
    return fine


def read_header(audio, offset=None):
    """Decode the 16-bit header standalone, before the payload length is known."""
    if offset is None:
        offset = find_preamble(audio)
    start = offset + len(PREAMBLE) * SYMBOL_SAMPLES
    mags = _symbol_magnitudes(audio, start, HEADER_SYMBOLS)
    symbols = np.argmax(mags, axis=1)
    bits = ((symbols[:, None] >> np.array([3, 2, 1, 0])) & 1).astype(np.uint8).ravel()
    bits = bits[:28].reshape(7, 4).T.ravel()
    return hamming_decode(bits)[:HEADER_BITS], offset


def demodulate(audio, info, offset=None):
    if offset is None:
        offset = find_preamble(audio)
    data_start = offset + len(PREAMBLE) * SYMBOL_SAMPLES
    if info.get("has_header"):
        data_start += HEADER_SYMBOLS * SYMBOL_SAMPLES

    n_symbols = info.get("n_symbols")
    if n_symbols is None:
        # self-describing mode: read every symbol that fits
        n_symbols = max(0, (len(audio) - data_start) // SYMBOL_SAMPLES)

    mags = _symbol_magnitudes(audio, data_start, n_symbols)
    symbols = np.argmax(mags, axis=1)

    bits = ((symbols[:, None] >> np.array([3, 2, 1, 0])) & 1).astype(np.uint8).ravel()

    if info.get("symbol_pad"):
        bits = bits[:len(bits) - info["symbol_pad"]]

    if info.get("fec", True):
        depth = INTERLEAVE_DEPTH
        bits = bits[:len(bits) - (len(bits) % depth)]
        bits = deinterleave(bits, depth)
        if info.get("interleave_pad"):
            bits = bits[:len(bits) - info["interleave_pad"]]
        bits = bits[:len(bits) - (len(bits) % 7)]
        bits = hamming_decode(bits)

    n = info.get("n_payload_bits")
    return bits if n is None else bits[:n]
