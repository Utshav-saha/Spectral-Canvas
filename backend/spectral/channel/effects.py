"""LTI channel effects - the Signals and Systems core of the project.

Every function here is y[n] = x[n] * h[n] + w[n] for some h and w, and each has
a VISIBLE consequence in the recovered image. That mapping is the point:
  low-pass  -> top image rows fade  (high frequencies carry the top rows)
  band-stop -> a horizontal band vanishes
  echo      -> columns smear rightwards
  clipping  -> harmonics create false rows
"""

import numpy as np
from scipy import signal


def add_noise(audio, snr_db=20.0, seed=None):
    rng = np.random.default_rng(seed)
    signal_power = np.mean(audio ** 2)
    if signal_power <= 0:
        return audio.copy()
    noise_power = signal_power / (10 ** (snr_db / 10))
    return audio + rng.normal(0, np.sqrt(noise_power), len(audio))


def add_echo(audio, delay_seconds=0.08, decay=0.4, sample_rate=44100):
    """h[n] = delta[n] + decay*delta[n-D]. Literal convolution with an impulse
    response - the most direct LTI demonstration in the whole project."""
    delay = int(delay_seconds * sample_rate)
    if delay <= 0:
        return audio.copy()
    impulse = np.zeros(delay + 1)
    impulse[0] = 1.0
    impulse[delay] = decay
    return signal.convolve(audio, impulse, mode="full")[:len(audio)]


def butter_filter(audio, cutoff, sample_rate, btype="low", order=6):
    nyq = sample_rate / 2
    if btype in ("low", "high"):
        wn = np.clip(cutoff / nyq, 1e-6, 0.999)
    else:
        lo, hi = cutoff
        wn = [np.clip(lo / nyq, 1e-6, 0.999), np.clip(hi / nyq, 1e-6, 0.999)]
    sos = signal.butter(order, wn, btype=btype, output="sos")
    return signal.sosfiltfilt(sos, audio)


def low_pass(audio, cutoff, sample_rate, order=6):
    return butter_filter(audio, cutoff, sample_rate, "low", order)


def high_pass(audio, cutoff, sample_rate, order=6):
    return butter_filter(audio, cutoff, sample_rate, "high", order)


def band_stop(audio, low, high, sample_rate, order=6):
    return butter_filter(audio, (low, high), sample_rate, "bandstop", order)


def clip(audio, threshold=0.3):
    return np.clip(audio, -threshold, threshold)


def resample_roundtrip(audio, sample_rate, target_rate, anti_alias=True):
    """Downsample then back up. With anti_alias=False, high rows fold down into
    lower ones - the sampling theorem made visible."""
    if target_rate >= sample_rate:
        return audio.copy()
    factor = int(round(sample_rate / target_rate))
    if factor < 2:
        return audio.copy()
    down = signal.decimate(audio, factor, ftype="fir") if anti_alias else audio[::factor]
    up = signal.resample(down, len(audio))
    return up


def apply_chain(audio, sample_rate, effects):
    """effects: list of {'type': ..., ...params}. Applied in order."""
    out = np.asarray(audio, dtype=np.float64).copy()
    for fx in effects or []:
        kind = fx.get("type")
        if kind == "noise":
            out = add_noise(out, fx.get("snr_db", 20.0))
        elif kind == "echo":
            out = add_echo(out, fx.get("delay", 0.08), fx.get("decay", 0.4), sample_rate)
        elif kind == "lowpass":
            out = low_pass(out, fx.get("cutoff", 4000), sample_rate)
        elif kind == "highpass":
            out = high_pass(out, fx.get("cutoff", 2000), sample_rate)
        elif kind == "bandstop":
            out = band_stop(out, fx.get("low", 3000), fx.get("high", 5000), sample_rate)
        elif kind == "clip":
            out = clip(out, fx.get("threshold", 0.3))
        elif kind == "resample":
            out = resample_roundtrip(out, sample_rate, fx.get("target_rate", 16000),
                                     fx.get("anti_alias", True))
    peak = np.max(np.abs(out))
    if peak > 1.0:
        out = out / peak * 0.99
    return out
