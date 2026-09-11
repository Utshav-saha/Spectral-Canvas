"""Turn a long audio array into something a browser can draw at 60fps.

A 4-second clip is ~176k samples. Shipping that as JSON is megabytes and the
canvas can only show ~1200 columns anyway. So we send a min/max envelope: for
each of N buckets, the lowest and highest sample it contains. That preserves
the visual shape of the waveform exactly, at 1/150th the payload.
"""

import numpy as np


def peak_envelope(audio, buckets=1200):
    audio = np.asarray(audio, dtype=np.float64)
    n = len(audio)
    if n == 0:
        return {"min": [], "max": [], "rms": []}

    buckets = int(min(buckets, n))
    edges = np.linspace(0, n, buckets + 1).astype(int)

    mins, maxs, rms = [], [], []
    for i in range(buckets):
        chunk = audio[edges[i]:edges[i + 1]]
        if len(chunk) == 0:
            mins.append(0.0); maxs.append(0.0); rms.append(0.0)
            continue
        mins.append(float(chunk.min()))
        maxs.append(float(chunk.max()))
        rms.append(float(np.sqrt(np.mean(chunk ** 2))))

    return {"min": mins, "max": maxs, "rms": rms}


def dominant_frequency(chunk, sample_rate):
    if len(chunk) < 16:
        return 0.0
    windowed = chunk * np.hanning(len(chunk))
    spectrum = np.abs(np.fft.rfft(windowed))
    if spectrum.max() <= 0:
        return 0.0
    peak_bin = int(np.argmax(spectrum))
    return float(peak_bin * sample_rate / len(chunk))


def bucket_stats(audio, sample_rate, buckets=1200):
    """Per-bucket stats for the hover tooltip: time, peak, rms, dominant tone."""
    audio = np.asarray(audio, dtype=np.float64)
    n = len(audio)
    if n == 0:
        return []

    buckets = int(min(buckets, n))
    edges = np.linspace(0, n, buckets + 1).astype(int)

    out = []
    for i in range(buckets):
        chunk = audio[edges[i]:edges[i + 1]]
        if len(chunk) == 0:
            continue
        out.append({
            "t": round(edges[i] / sample_rate, 4),
            "peak": round(float(np.max(np.abs(chunk))), 4),
            "rms": round(float(np.sqrt(np.mean(chunk ** 2))), 4),
            "freq": round(dominant_frequency(chunk, sample_rate), 1),
        })
    return out


def global_stats(audio, sample_rate):
    audio = np.asarray(audio, dtype=np.float64)
    if len(audio) == 0:
        return {}
    rms = float(np.sqrt(np.mean(audio ** 2)))
    peak = float(np.max(np.abs(audio)))
    crest = (peak / rms) if rms > 0 else 0.0
    return {
        "duration": round(len(audio) / sample_rate, 3),
        "samples": int(len(audio)),
        "sample_rate": int(sample_rate),
        "peak": round(peak, 4),
        "rms": round(rms, 4),
        "crest_factor": round(crest, 2),
        "dbfs": round(20 * np.log10(peak) if peak > 0 else -120.0, 1),
    }


def spectrogram(audio, sample_rate, frame_samples, max_frames=240, max_bins=160,
                f_min=0, f_max=None):
    """Coarse dB spectrogram for the UI heatmap. Deliberately downsampled."""
    audio = np.asarray(audio, dtype=np.float64)
    frames = len(audio) // frame_samples
    if frames == 0:
        return {"data": [], "frames": 0, "bins": 0}

    blocks = audio[:frames * frame_samples].reshape(frames, frame_samples)
    if frames > max_frames:
        idx = np.linspace(0, frames - 1, max_frames).astype(int)
        blocks = blocks[idx]

    windowed = blocks * np.hanning(frame_samples)
    spectrum = np.abs(np.fft.rfft(windowed, axis=1))

    freqs = np.fft.rfftfreq(frame_samples, 1 / sample_rate)
    f_max = f_max or sample_rate / 2
    keep = np.where((freqs >= f_min) & (freqs <= f_max))[0]
    spectrum = spectrum[:, keep]

    if spectrum.shape[1] > max_bins:
        idx = np.linspace(0, spectrum.shape[1] - 1, max_bins).astype(int)
        spectrum = spectrum[:, idx]

    db = 20 * np.log10(spectrum + 1e-9)
    db = np.clip((db - db.max() + 70) / 70, 0, 1)

    return {
        "data": [[round(float(v), 3) for v in row] for row in db[:, ::-1].T],
        "frames": int(db.shape[0]),
        "bins": int(db.shape[1]),
        "f_min": float(f_min),
        "f_max": float(f_max),
    }
