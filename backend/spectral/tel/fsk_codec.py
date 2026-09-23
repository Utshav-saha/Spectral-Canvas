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
# Re-sync markers
#
# The modem locks its grid once, from the preamble, and then counts symbols.
# That is fine on a file and fine on a short call. On a long one it is not:
# measured on a real 860 s Linphone call, the handset's jitter buffer slipped
# 700 samples - 2.2 whole symbols - in discrete jumps, and every symbol after
# the first jump was read from the wrong place.
#
# Nothing downstream can repair that, because no measurement taken from the
# waveform can see it. A grid shifted by a whole symbol is still perfectly
# aligned to *a* symbol boundary, so margin, tone concentration and every
# other alignment score peak identically at every multiple of SYMBOL_SAMPLES.
# Measured on that recording, at the segment needing -700 samples:
#
#     shift    -1000   -750   -700   -450   -100   +250
#     margin   14.94  15.00  15.00  15.31  14.84  14.68   <- blind
#     correct    5.5%  92.2%  96.7%  11.5%   6.8%   4.7%
#
# The tones themselves arrived fine - 92-98% correct once realigned. The only
# thing missing was something in the stream to realign *to*. So the stream now
# carries one, and a slip can only cost the segment it happens in.
#
# Unlike PREAMBLE, this sequence is NOT periodic: PREAMBLE alternates with a
# period of two symbols, so it scores the same shifted by two, which is
# exactly the ambiguity being fixed here. These eight tones are all distinct,
# so any nonzero shift lines up none of them.
RESYNC = np.array([0, 9, 2, 13, 6, 15, 4, 11])

# Payload symbols between markers, and the reason it is this small.
#
# A handset does not merely slip occasionally: measured across a real 160 s
# call, the phone's timing wandered over a range of 805 samples - 2.5 whole
# symbols - at a median 15.6 samples/s with jumps to 352, and it wandered both
# directions rather than drifting one way. Between anchors that accumulates:
#
#     symbols between anchors   2.56 s   5.12 s   10.2 s   20.5 s
#     median drift (samples)        50      130      165      135
#     95th percentile              272      367      425      353
#
# against a tolerance of about 80 samples before a symbol is read from the
# wrong place. Replaying that measured wander over the real GSM channel:
#
#     interval    512     256     128      64      48      32
#     airtime   164.2s  166.7s  171.8s  181.8s  188.5s  201.9s
#     exact      55.2%   79.3%   86.3%   98.6%   99.2%   99.5%
#
# 64 is the knee. Below it the airtime climbs and the picture barely improves.
# The markers cost 12.5% of the wire, which is the price of the channel being
# a phone rather than a file.
RESYNC_INTERVAL = 64

# How far a marker is hunted for around where it was expected. Every marker
# re-anchors absolutely, so this covers one interval's wander, not the call's:
# two symbols is well past the 272-sample 95th percentile above, and keeping it
# tight is what stops the search wandering onto a spurious peak.
RESYNC_SPAN = 2 * SYMBOL_SAMPLES


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


def modulate(bits, fec=True, header=None, resync=None):
    """header: optional 16-bit array describing the payload, sent uncoded of
    interleaving so the receiver can read it standalone.

    resync: payload symbols between re-sync markers, or 0 for none. The
    markers are what let a long call survive a jitter-buffer slip; see RESYNC.
    """
    # resolved here, not in the signature, so the constant stays tunable
    resync = RESYNC_INTERVAL if resync is None else resync
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

    # A marker leads EVERY segment, the first one included. The preamble
    # cannot do that job: it alternates between two tones, so its score is
    # broad and it localises poorly - measured on a real 36 s call, it landed
    # 110 samples early, which read 57.8% of the first segment correctly where
    # the true alignment read 83.2%. The marker's eight distinct tones give a
    # sharp peak instead, so segment one is anchored exactly as well as the
    # rest. The preamble's remaining job is to be findable at all, in a
    # recording that starts whenever Record was pressed.
    body = []
    if resync:
        for i in range(0, len(symbols), resync):
            body.extend(int(s) for s in RESYNC)
            body.extend(int(s) for s in symbols[i:i + resync])
    else:
        body = [int(s) for s in symbols]

    audio = np.concatenate(
        [_tone(s, t, window) for s in PREAMBLE] + head +
        [_tone(s, t, window) for s in body]
    )
    audio = audio / max(np.max(np.abs(audio)), 1e-12) * 0.7

    info = {
        "n_payload_bits": int(n_payload),
        # payload symbols only: the markers are wire overhead and never reach
        # bits_to_activation, so every existing reader of this field is right
        "n_symbols": int(len(symbols)),
        "fec": bool(fec),
        "hamming_pad": int(ham_pad),
        "interleave_pad": int(il_pad),
        "symbol_pad": int(pad),
        "sample_rate": SAMPLE_RATE,
        "symbol_ms": SYMBOL_MS,
        "has_header": header is not None,
        "resync_interval": int(resync or 0),
        "wire_symbols": int(len(body)),
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


def _marker_score(audio, offset):
    """How much like a RESYNC marker the symbols at `offset` look."""
    mags = _symbol_magnitudes(audio, offset, len(RESYNC))
    total = mags.sum(axis=1) + 1e-12
    return float(np.mean(mags[np.arange(len(RESYNC)), RESYNC] / total))


def find_marker(audio, expected, span=None, step=8):
    """Where the marker near `expected` really is; returns the offset AFTER it.

    Absolute, not relative: each marker is hunted for around where the symbol
    count says it should be, so an error corrected here does not carry into
    the next segment and cannot accumulate over a long call.
    """
    span = RESYNC_SPAN if span is None else span
    room = len(audio) - len(RESYNC) * SYMBOL_SAMPLES
    lo, hi = max(0, expected - span), min(room, expected + span)
    if hi <= lo:
        return expected + len(RESYNC) * SYMBOL_SAMPLES
    coarse = max(range(lo, hi + 1, step), key=lambda o: _marker_score(audio, o))
    fine = max(range(max(lo, coarse - step), min(hi, coarse + step) + 1),
               key=lambda o: _marker_score(audio, o))
    return fine + len(RESYNC) * SYMBOL_SAMPLES


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

    interval = int(info.get("resync_interval") or 0)
    if interval:
        # Read one segment, re-anchor on the marker that follows it, repeat.
        chunks, pos, left = [], data_start, n_symbols
        while left > 0:
            pos = find_marker(audio, pos)
            count = min(interval, left)
            chunks.append(np.argmax(_symbol_magnitudes(audio, pos, count), axis=1))
            pos += count * SYMBOL_SAMPLES
            left -= count
        symbols = np.concatenate(chunks)
    else:
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
