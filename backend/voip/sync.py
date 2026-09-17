"""Find where the transmission starts in a recording of unknown length.

``fsk_codec.find_preamble`` searches ``search_seconds=3.0`` by default and
evaluates one candidate offset at a time. Both are fine for the simulated
channel, which prepends at most 900 ms of silence. Neither survives contact
with a real call: you press Record on the phone, walk back to the laptop, press
Enter, and the preamble lands 10 to 60 seconds in. Widening the window in the
obvious way is not an option either -- at ``step=4`` a 60-second search is
120,000 separate 8x320 FFT batches.

So the search is restructured rather than re-tuned. Every 40 ms frame on a
10 ms grid is transformed **once**, into a single magnitude matrix, and each
candidate offset is then a handful of lookups into it. Because the symbol
period (320 samples) is a whole multiple of the grid stride (80), symbol *j* of
a candidate starting at grid index *i* is simply grid frame ``i + 4j``.

That buys three things the original could not give:

  * the whole file is searchable, not the first three seconds;
  * it is faster even on short audio, because the transform is shared;
  * the score comes back with the offset, so "no preamble here" is finally
    something the caller can be told rather than a silently wrong answer.

The modem itself is untouched. ``read_header`` and ``demodulate`` both already
accept an explicit ``offset``; this module computes it and passes it in.
"""

from dataclasses import dataclass, field

import numpy as np

from voip import _tel
from voip.config import (
    DRIFT_PPM_GRID,
    SAMPLE_RATE,
    SYMBOL_SAMPLES,
    SYNC_REFINE_BELOW,
    SYNC_SCORE_THRESHOLD,
    SYNC_STRIDE,
)
from voip.dsp import mean_margin

# Frames per symbol on the coarse grid. 320 / 80 = 4, and the algorithm below
# depends on this being an integer.
_FRAMES_PER_SYMBOL = SYMBOL_SAMPLES // SYNC_STRIDE

# Cap on frames transformed at once. 4096 x 320 float64 is ~10 MB; without it a
# five-minute recording would materialise 77 MB of overlapping windows.
_CHUNK_FRAMES = 4096


@dataclass
class SyncResult:
    """Where the preamble is, and how sure we are."""

    offset: int
    score: float
    found: bool
    threshold: float = SYNC_SCORE_THRESHOLD
    search_seconds: float | None = None
    refined: bool = False
    coarse_stride: int = SYNC_STRIDE
    drift_ppm: float | None = None
    sample_rate: int = SAMPLE_RATE
    warnings: list = field(default_factory=list)

    @property
    def offset_seconds(self):
        return self.offset / float(self.sample_rate)

    def as_dict(self):
        return {
            "found": bool(self.found),
            "preamble_score": round(float(self.score), 4),
            "threshold": float(self.threshold),
            "offset_samples": int(self.offset),
            "offset_seconds": round(self.offset_seconds, 4),
            "refined": bool(self.refined),
            "coarse_stride": int(self.coarse_stride),
            "search_seconds": self.search_seconds,
            "drift_ppm": self.drift_ppm,
        }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def preamble_score(audio, offset):
    """Fraction of each preamble symbol's energy landing in its expected tone.

    Byte-for-byte the same measure ``fsk_codec.find_preamble`` computes
    internally; it just never returns it. Being a ratio, it is immune to the
    overall gain a call applies, which is the same property that makes argmax
    work.
    """
    fsk = _tel.fsk()
    preamble = fsk.PREAMBLE
    mags = fsk._symbol_magnitudes(audio, int(offset), len(preamble))
    total = mags.sum(axis=1) + 1e-12
    return float(np.mean(mags[np.arange(len(preamble)), preamble] / total))


def _grid_purity(audio, stride, limit_samples):
    """Per-frame tone purity on a fixed grid: (n_frames, 16), rows summing to 1.

    One windowed rFFT per grid position, computed in chunks. Every candidate
    offset is then just an index into this.
    """
    fsk = _tel.fsk()
    audio = np.asarray(audio, dtype=np.float64)

    usable = min(len(audio), int(limit_samples) + SYMBOL_SAMPLES) if limit_samples else len(audio)
    n_frames = (usable - SYMBOL_SAMPLES) // stride + 1
    if n_frames <= 0:
        return np.zeros((0, fsk.M), dtype=np.float64)

    window = np.hanning(SYMBOL_SAMPLES)
    purity = np.empty((n_frames, fsk.M), dtype=np.float64)

    for start in range(0, n_frames, _CHUNK_FRAMES):
        stop = min(start + _CHUNK_FRAMES, n_frames)
        offsets = np.arange(start, stop) * stride
        # (chunk, SYMBOL_SAMPLES) gather, then one batched transform
        frames = audio[offsets[:, None] + np.arange(SYMBOL_SAMPLES)[None, :]] * window
        mags = np.abs(np.fft.rfft(frames, axis=1))[:, fsk.TONE_BINS]
        purity[start:stop] = mags / (mags.sum(axis=1, keepdims=True) + 1e-12)

    return purity


def coarse_scan(audio, stride=SYNC_STRIDE, search_seconds=None):
    """(scores, stride) -- the preamble score at every offset on the grid.

    scores[i] is the score for a transmission starting at sample i*stride.
    """
    fsk = _tel.fsk()
    preamble = fsk.PREAMBLE
    limit = int(search_seconds * SAMPLE_RATE) if search_seconds else 0

    purity = _grid_purity(audio, stride, limit)
    span = _FRAMES_PER_SYMBOL * (len(preamble) - 1)
    n_candidates = len(purity) - span
    if n_candidates <= 0:
        return np.zeros(0, dtype=np.float64), stride

    scores = np.zeros(n_candidates, dtype=np.float64)
    for j, tone in enumerate(preamble):
        first = j * _FRAMES_PER_SYMBOL
        scores += purity[first:first + n_candidates, tone]
    scores /= len(preamble)
    return scores, stride


def refine_offset(audio, offset, span=SYMBOL_SAMPLES // 2, n_probe=64):
    """Nudge the offset to maximise the mean *data* margin, not the preamble score.

    Decision-directed, and deliberately so. The preamble score saturates once
    the grid is roughly aligned, but a residual error of a few dozen samples
    still costs real margin on the payload. Probing actual symbols recovers it.
    Only worth the cost when sync looked marginal.
    """
    fsk = _tel.fsk()
    data_start = int(offset) + len(fsk.PREAMBLE) * SYMBOL_SAMPLES

    best_offset, best_margin = int(offset), mean_margin(audio, data_start, n_probe)
    for candidate in range(int(offset) - span, int(offset) + span + 1, 8):
        if candidate < 0:
            continue
        probe = candidate + len(fsk.PREAMBLE) * SYMBOL_SAMPLES
        score = mean_margin(audio, probe, n_probe)
        if score > best_margin:
            best_offset, best_margin = candidate, score
    return best_offset, best_margin


def find_preamble(audio, search_seconds=None, stride=SYNC_STRIDE,
                  threshold=SYNC_SCORE_THRESHOLD, fine=True, refine=True):
    """Locate the transmission. `search_seconds=None` scans the whole file.

    Coarse pass on the `stride` grid, then a per-sample pass either side of the
    winner, then optional decision-directed refinement when the score is
    marginal. Returns a SyncResult whose `found` flag is the answer to "is
    there a transmission in this audio at all".
    """
    audio = np.asarray(audio, dtype=np.float64)
    warnings = []
    fsk = _tel.fsk()
    minimum = (len(fsk.PREAMBLE) + 1) * SYMBOL_SAMPLES

    if len(audio) < minimum:
        return SyncResult(
            offset=0, score=-1.0, found=False, threshold=threshold,
            search_seconds=search_seconds,
            warnings=[f"Recording is only {len(audio) / SAMPLE_RATE:.2f} s; "
                      f"a transmission needs at least {minimum / SAMPLE_RATE:.2f} s."],
        )

    scores, stride = coarse_scan(audio, stride, search_seconds)
    if len(scores) == 0:
        return SyncResult(offset=0, score=-1.0, found=False, threshold=threshold,
                          search_seconds=search_seconds,
                          warnings=["Not enough audio to hold a preamble."])

    best_index = int(np.argmax(scores))
    offset = best_index * stride
    score = float(scores[best_index])

    if fine:
        low = max(0, offset - stride)
        high = min(len(audio) - minimum, offset + stride)
        for candidate in range(low, high + 1):
            candidate_score = preamble_score(audio, candidate)
            if candidate_score > score:
                offset, score = candidate, candidate_score

    result = SyncResult(
        offset=int(offset), score=score, found=score >= threshold,
        threshold=threshold, search_seconds=search_seconds,
        coarse_stride=stride, warnings=warnings,
    )

    if refine and result.found and score < SYNC_REFINE_BELOW:
        refined_offset, _ = refine_offset(audio, offset)
        if refined_offset != offset:
            result.offset = int(refined_offset)
            result.score = preamble_score(audio, refined_offset)
            result.refined = True

    if not result.found:
        result.warnings.append(
            f"Best preamble score was {score:.2f}, below the {threshold:.2f} "
            f"needed to call it a transmission."
        )
    return result


# --------------------------------------------------------------------------
# Clock drift
# --------------------------------------------------------------------------

def estimate_drift(audio, offset, n_symbols, ppm_grid=DRIFT_PPM_GRID):
    """Search for a sample-rate mismatch between the laptop and the phone.

    The two ends are independent oscillators. Over a 76-second Generation C
    transmission, only ~260 ppm of disagreement is enough to walk half a symbol
    out of alignment by the end, and Bluetooth or AirPlay resamplers can exceed
    that. This resamples by a few candidate ratios and keeps whichever gives the
    best mean margin over the payload.

    Off by default: it costs a resample per grid point, and a direct digital
    path usually has no drift worth correcting.

    Returns (ppm, audio, offset) for the winner -- unchanged if zero wins.
    """
    from scipy.signal import resample_poly

    fsk = _tel.fsk()
    data_start = int(offset) + len(fsk.PREAMBLE) * SYMBOL_SAMPLES
    probe = min(int(n_symbols), 256)

    best = (0.0, np.asarray(audio, dtype=np.float64), int(offset))
    best_margin = mean_margin(audio, data_start, probe)

    for ppm in ppm_grid:
        if ppm == 0.0:
            continue
        # up/down chosen so up/down == 1 + ppm/1e6, to a part in 1e7
        up = int(round(1_000_000 + ppm))
        down = 1_000_000
        divisor = np.gcd(up, down)
        stretched = resample_poly(audio, up // divisor, down // divisor)

        moved = int(round(offset * up / down))
        candidate = mean_margin(stretched, moved + len(fsk.PREAMBLE) * SYMBOL_SAMPLES, probe)
        if candidate > best_margin:
            best_margin, best = candidate, (float(ppm), stretched, moved)

    return best
