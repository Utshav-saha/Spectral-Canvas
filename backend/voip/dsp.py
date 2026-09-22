"""Symbol magnitudes and the confidence the modem throws away.

``fsk_codec`` decides each symbol with ``np.argmax(mags, axis=1)``. That is the
right decision rule -- it is what makes the scheme immune to the gain changes a
phone call inflicts -- but it discards *how* clearly the winner won.

Over a simulated channel that does not matter, because you either get the
picture or you do not. Over a real call it is the difference between "this
worked" and "this worked with two decibels to spare", which is the only way to
tell a good call from a lucky one. So every decision here also reports a
margin: the ratio of the best tone's magnitude to the runner-up's.

    margin ~ 1.0   the two best tones were indistinguishable; a coin flip
    margin ~ 2.0   marginal
    margin > 10    clean

Nothing here reimplements the FFT. ``symbol_magnitudes`` delegates to the
modem so the two can never drift apart.
"""

import numpy as np

from voip import _tel
from voip.config import SYMBOL_SAMPLES, WEAK_MARGIN_THRESHOLD


def symbol_magnitudes(audio, offset, count):
    """(count, 16) FFT magnitudes at the tone bins, one row per symbol.

    Delegates to fsk_codec so the analysis window and bin table stay identical
    to what the rest of the project uses. Note the modem zero-pads a short
    tail rather than raising -- see available_symbols before trusting `count`.
    """
    return _tel.fsk()._symbol_magnitudes(audio, int(offset), int(count))


def available_symbols(audio, data_start):
    """How many whole symbols actually exist from data_start onwards."""
    return max(0, (len(audio) - int(data_start)) // SYMBOL_SAMPLES)


def symbol_decisions(mags):
    """(symbols, margin) -- argmax plus the top1/top2 magnitude ratio."""
    mags = np.asarray(mags, dtype=np.float64)
    if mags.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64)

    symbols = np.argmax(mags, axis=1)
    top2 = np.partition(mags, -2, axis=1)[:, -2:]
    best, runner_up = top2[:, 1], top2[:, 0]
    margin = best / np.maximum(runner_up, 1e-12)
    return symbols.astype(np.int64), margin.astype(np.float64)


def symbols_to_bits(symbols):
    """4 bits per symbol, MSB first -- the modem's own mapping."""
    s = np.asarray(symbols, dtype=np.int64)
    return ((s[:, None] >> np.array([3, 2, 1, 0])) & 1).astype(np.uint8).ravel()


def confidence_stats(margin, threshold=WEAK_MARGIN_THRESHOLD):
    """Summarise per-symbol margins for the decode report."""
    margin = np.asarray(margin, dtype=np.float64)
    if margin.size == 0:
        return {
            "count": 0, "weak_symbols": 0, "weak_fraction": 0.0,
            "weak_threshold": float(threshold),
            "margin_min": None, "margin_p05": None,
            "margin_median": None, "margin_mean": None,
        }

    weak = int(np.sum(margin < threshold))
    return {
        "count": int(margin.size),
        "weak_symbols": weak,
        "weak_fraction": float(weak / margin.size),
        "weak_threshold": float(threshold),
        "margin_min": float(np.min(margin)),
        "margin_p05": float(np.percentile(margin, 5)),
        "margin_median": float(np.median(margin)),
        "margin_mean": float(np.mean(margin)),
    }


def weak_symbol_indices(margin, threshold=WEAK_MARGIN_THRESHOLD, limit=200):
    """Where the marginal symbols were, so bursts are visible as runs."""
    idx = np.nonzero(np.asarray(margin) < threshold)[0]
    return [int(i) for i in idx[:limit]]


def mean_margin(audio, offset, count):
    """Average decision margin over `count` symbols starting at `offset`.

    The objective refine_offset maximises: unlike the preamble score it looks
    at real data, so it keeps improving after the preamble score has saturated.
    """
    count = min(int(count), available_symbols(audio, offset))
    if count <= 0:
        return 0.0
    _, margin = symbol_decisions(symbol_magnitudes(audio, offset, count))
    return float(np.mean(margin)) if margin.size else 0.0
