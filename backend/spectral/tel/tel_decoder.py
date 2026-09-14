"""
Telephony-band decoder.

Replaces synchronizer.synchronize + stft_decoder.decode +
image_reconstructor.recover_activation.

Two jobs:

  SYNC   Your find_start_by_energy finds the first frame louder than 10% of
         peak. Over a codec the onset is soft (encoder ramp-up, comfort
         noise) and you can easily land half a frame off, which smears every
         column into its neighbour. Here we use the pilots instead: slide the
         analysis grid and keep the offset where total pilot energy peaks.
         A correctly aligned window sees a clean bin-centred pilot; a
         misaligned one straddles two frames' tapers and measures less.

  LEVEL  Your recover_activation divides by normalization_gain * sum(hann).
         That assumes the channel preserved absolute amplitude. It did not.
         Here every row magnitude is divided by the interpolated pilot
         magnitude from the SAME frame, so gain, AGC drift and the channel's
         spectral tilt all cancel.
"""

import numpy as np

import tel_config as cfg


def load_audio(path):
    from scipy.io.wavfile import read
    sample_rate, audio = read(path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if audio.dtype.kind == "i":
        audio = audio / np.iinfo(audio.dtype).max
    return sample_rate, audio.astype(np.float64)


def _bins(freqs, bin_width):
    return np.round(np.asarray(freqs) / bin_width).astype(int)


def _spectra(audio, start, n_frames, frame_samples):
    """Cut n_frames starting at `start`, Hann-analyse, return |rfft| matrix."""
    need = n_frames * frame_samples
    segment = audio[start:start + need]
    if len(segment) < need:
        segment = np.concatenate([segment, np.zeros(need - len(segment))])
    frames = segment.reshape(n_frames, frame_samples)
    analysis = np.hanning(frame_samples)
    return np.abs(np.fft.rfft(frames * analysis, axis=1))


def coarse_start(audio, frame_samples, energy_ratio=0.15):
    n = len(audio) // frame_samples
    if n == 0:
        return 0
    blocks = audio[:n * frame_samples].reshape(n, frame_samples)
    energy = np.sqrt(np.mean(blocks ** 2, axis=1))
    if energy.max() == 0:
        return 0
    loud = np.where(energy >= energy_ratio * energy.max())[0]
    return int(loud[0] * frame_samples) if len(loud) else 0


def pilot_align(audio, metadata, search_frames=8):
    """Find the sample offset of the first preamble frame."""
    frame_samples = metadata["frame_samples"]
    bin_width = metadata["bin_width"]
    lo_bin, hi_bin = _bins([metadata["pilot_low"], metadata["pilot_high"]],
                           bin_width)

    rough = coarse_start(audio, frame_samples)
    lo = max(0, rough - frame_samples)
    hi = min(len(audio) - search_frames * frame_samples, rough + frame_samples)
    if hi <= lo:
        return max(0, rough)

    def score(offset):
        spectra = _spectra(audio, offset, search_frames, frame_samples)
        return float(spectra[:, lo_bin].sum() + spectra[:, hi_bin].sum())

    coarse_grid = range(lo, hi, 8)
    best = max(coarse_grid, key=score)
    fine_grid = range(max(0, best - 8), best + 9)
    return max(fine_grid, key=score)


def _column_offsets(audio, start, n_frames, metadata, jitter=24):
    """Per-column fine alignment, +-jitter samples.

    Guards against the jitter buffer inserting or dropping a 20 ms frame
    partway through a long transmission, which slides everything after it.
    """
    frame_samples = metadata["frame_samples"]
    bin_width = metadata["bin_width"]
    lo_bin, hi_bin = _bins([metadata["pilot_low"], metadata["pilot_high"]],
                           bin_width)
    analysis = np.hanning(frame_samples)

    offsets = np.zeros(n_frames, dtype=int)
    drift = 0
    for i in range(n_frames):
        base = start + i * frame_samples + drift
        best, best_score = 0, -1.0
        for d in range(-jitter, jitter + 1, 4):
            seg = audio[base + d: base + d + frame_samples]
            if len(seg) < frame_samples:
                continue
            spectrum = np.abs(np.fft.rfft(seg * analysis))
            s = spectrum[lo_bin] + spectrum[hi_bin]
            if s > best_score:
                best_score, best = s, d
        offsets[i] = base + best
        drift += best          # carry the correction forward
    return offsets


def decode(audio, metadata, per_column_sync=True):
    """audio -> activation matrix (rows, cols) or (rows, cols, 3)."""
    frame_samples = metadata["frame_samples"]
    bin_width = metadata["bin_width"]
    rows = metadata["rows"]
    columns = metadata["columns"]
    n_channels = metadata["channels"]
    preamble = metadata["preamble_frames"]
    pilot_amp = metadata["pilot_amplitude"]

    row_bins = _bins(metadata["row_frequencies"], bin_width)
    lo_bin, hi_bin = _bins([metadata["pilot_low"], metadata["pilot_high"]],
                           bin_width)
    f_lo = metadata["pilot_low"]
    f_hi = metadata["pilot_high"]
    row_f = np.asarray(metadata["row_frequencies"])

    start = pilot_align(audio, metadata)
    data_start = start + preamble * frame_samples
    n_data = columns * n_channels

    analysis = np.hanning(frame_samples)

    if per_column_sync:
        positions = _column_offsets(audio, data_start, n_data, metadata)
    else:
        positions = data_start + np.arange(n_data) * frame_samples

    spectra = np.zeros((n_data, frame_samples // 2 + 1))
    for i, p in enumerate(positions):
        seg = audio[p:p + frame_samples]
        if len(seg) < frame_samples:
            seg = np.concatenate([seg, np.zeros(frame_samples - len(seg))])
        spectra[i] = np.abs(np.fft.rfft(seg * analysis))

    # ---- pilot normalisation -------------------------------------------
    # K = channel gain implied by each pilot, per column
    k_lo = np.maximum(spectra[:, lo_bin] / pilot_amp, 1e-12)
    k_hi = np.maximum(spectra[:, hi_bin] / pilot_amp, 1e-12)

    # log-linear interpolation of gain across frequency, per column
    w = (row_f - f_lo) / (f_hi - f_lo)                    # (rows,)
    log_k = (np.log(k_lo)[:, None] * (1 - w)[None, :]
             + np.log(k_hi)[:, None] * w[None, :])        # (n_data, rows)
    k = np.exp(log_k)

    magnitudes = spectra[:, row_bins]                     # (n_data, rows)
    activation = np.clip(magnitudes / k, 0.0, 1.0).T      # (rows, n_data)

    if n_channels == 1:
        return activation
    return np.stack(
        [activation[:, i * columns:(i + 1) * columns] for i in range(n_channels)],
        axis=-1,
    )


def quantize(activation, levels=cfg.GRAY_LEVELS):
    if not levels:
        return activation
    return np.round(activation * (levels - 1)) / (levels - 1)


def to_pixels(activation, levels=cfg.GRAY_LEVELS):
    activation = quantize(activation, levels)
    return np.clip(np.rint((1.0 - activation) * 255.0), 0, 255).astype(np.uint8)
