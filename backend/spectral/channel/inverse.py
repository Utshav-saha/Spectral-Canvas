"""Undoing the channel effects that can be undone.

This is the other half of `effects.py`. If the channel is LTI - linear and
time invariant - then it did

    Y(f) = H(f) X(f)

and getting X back is division: X(f) = Y(f) / H(f). That is the whole idea.
Everything below is that one line, plus the care needed where H(f) is small.

**Why dividing needs care.** Where the channel passed almost nothing, H(f) is
almost 0, and 1/H(f) is enormous. The signal there is gone, but the noise is
not, so dividing blows the noise up into a loud mess. The fix is to stop
dividing once H gets small:

    inverse = H / (H^2 + eps)        instead of      1 / H

With eps = 0 this is exactly 1/H. With eps > 0 it fades smoothly to 0 where H
is tiny, so killed frequencies stay quiet instead of turning into noise. eps
is the one knob here: bigger = safer and blurrier, smaller = sharper and
noisier. This is Wiener-style regularisation, and it is why a band-stop cannot
really be undone: inside the stop band H is 0, so no eps setting brings the
rows back. It only decides how loud the failure is.

**What can and cannot be undone**, which is the point of the whole exercise:

    low-pass     yes, up to where it stopped passing anything
    high-pass    yes, same limit at the other end
    echo         yes, and exactly: it is a feedback filter run backwards
    gain         yes, and trivially: measure the level and scale it back
    band-stop    no. Not attenuated, annihilated. Nothing to divide by
    resampling   no. Everything above the new Nyquist is gone; if the
                 anti-alias filter was off, two rows were summed into one
    clipping     no. Not LTI at all - no h[n], no H(f), so no division
    noise        no. It was added, not convolved. Averaging helps, undoing
                 does not apply

The first four have an inverse. The last four are what the restoration model
is for, and this file says so rather than pretending otherwise.
"""

import numpy as np
from scipy import signal

# Default regularisation, picked by measuring on the real pipeline: at 1e-6 a
# clean low-pass inverts better (0.0135 vs 0.0730 mean activation error), but
# once there is noise on the line 1e-5 wins, because less of the noise gets
# amplified. 1e-5 is the safer default; the bench lets you move it.
EPSILON = 1e-5

# What the encoder normalises its output to: 0.8 open, 0.5 locked (the mask
# needs the headroom). A gain-corrupted recording is scaled back to this.
OPEN_PEAK = 0.8
LOCKED_PEAK = 0.5

# --------------------------------------------------------------------------
# What is invertible, in one place
# --------------------------------------------------------------------------

INVERTIBLE = {
    "lowpass": True,
    "highpass": True,
    "echo": True,
    "gain": True,
    "bandstop": False,
    "resample": False,
    "clip": False,
    "noise": False,
}

WHY = {
    "lowpass": "LTI. Divide by H(f), up to where H(f) stops being measurable.",
    "highpass": "LTI. The same division, at the other end of the band.",
    "echo": "LTI. h[n] = d[n] + a*d[n-D] is a feedback filter run backwards, "
            "and it is exact while a < 1.",
    "gain": "One number. Measure the level and scale it back.",
    "bandstop": "H(f) = 0 inside the stop band. Those rows were annihilated, "
                "not attenuated, and 0/0 is not a recovery.",
    "resample": "Everything above the new Nyquist is gone. With the anti-alias "
                "filter off it is worse: two rows were added together, and one "
                "number cannot be split back into two.",
    "clip": "Not LTI. A memoryless nonlinearity has no h[n] and no H(f), so "
            "there is nothing to divide by. It also invents new frequencies.",
    "noise": "Added, not convolved. There is no H(f) that produced it.",
}

def report():
    """A plain list for the UI: what has an inverse and why."""
    return [{"id": key, "invertible": INVERTIBLE[key], "why": WHY[key]}
            for key in INVERTIBLE]

# --------------------------------------------------------------------------
# The pieces
# --------------------------------------------------------------------------

def undo_gain(audio, target_peak=OPEN_PEAK):
    """Unknown overall gain.

    A channel that multiplies everything by some unknown number is the easiest
    case there is: scale so the peak is what it should be. Amplitude carries
    the pixel on Track 1, so getting this wrong washes the whole picture out.

    `target_peak` must be what the encoder aimed for - 0.8 open, 0.5 locked -
    because the decoder divides by the gain recorded in the metadata. Scaling
    to anything else (0.99, say) trades one wrong level for another: measured,
    that alone costs about 0.08 of mean activation error.
    """
    audio = np.asarray(audio, dtype=np.float64)
    peak = float(np.max(np.abs(audio)))
    if peak <= 0:
        return audio.copy()
    return audio * (target_peak / peak)

def undo_echo(audio, delay_seconds=0.08, decay=0.4, sample_rate=44100):
    """Exact, because an echo is a filter and filters run backwards.

    The echo did      y[n] = x[n] + a*x[n-D]
    so the inverse is x[n] = y[n] - a*x[n-D]

    which is a feedback (IIR) filter: lfilter takes the echo's coefficients as
    the DENOMINATOR, and that is the whole inversion. It is stable while
    a < 1, because each round of feedback is smaller than the last.
    """
    delay = int(delay_seconds * sample_rate)
    if delay <= 0 or abs(decay) >= 1.0:
        return np.asarray(audio, dtype=np.float64).copy()

    a = np.zeros(delay + 1)
    a[0] = 1.0
    a[delay] = decay
    return signal.lfilter([1.0], a, np.asarray(audio, dtype=np.float64))

def _response(sos, n_freqs, sample_rate):
    """|H(f)| of a filter, on the same frequency grid as rfft uses.

    Squared, because effects.py filters with sosfiltfilt, which runs the
    filter forwards and then backwards. That is two passes, so the signal met
    |H(f)| twice, and it also cancels all phase shift - which is handy, since
    it means the inverse only has to fix the magnitude.
    """
    freqs = np.fft.rfftfreq(n_freqs, d=1.0 / sample_rate)
    _, h = signal.sosfreqz(sos, worN=freqs, fs=sample_rate)
    return np.abs(h) ** 2

def undo_filter(audio, cutoff, sample_rate, btype="low", order=6, epsilon=EPSILON):
    """Divide the spectrum by the filter's response, carefully.

    Steps, and there are only four:
      1. FFT the audio                                  Y(f)
      2. work out what the filter did at each frequency H(f)
      3. divide, with the eps guard                     X(f) = Y H / (H^2+eps)
      4. inverse FFT back to samples
    """
    audio = np.asarray(audio, dtype=np.float64)
    if len(audio) == 0:
        return audio.copy()

    nyq = sample_rate / 2
    if btype in ("low", "high"):
        wn = np.clip(cutoff / nyq, 1e-6, 0.999)
    else:
        lo, hi = cutoff
        wn = [np.clip(lo / nyq, 1e-6, 0.999), np.clip(hi / nyq, 1e-6, 0.999)]
    sos = signal.butter(order, wn, btype=btype, output="sos")

    spectrum = np.fft.rfft(audio)
    response = _response(sos, len(audio), sample_rate)
    inverse = response / (response ** 2 + epsilon)
    return np.fft.irfft(spectrum * inverse, n=len(audio))

def undo_low_pass(audio, cutoff, sample_rate, order=6, epsilon=EPSILON):
    return undo_filter(audio, cutoff, sample_rate, "low", order, epsilon)

def undo_high_pass(audio, cutoff, sample_rate, order=6, epsilon=EPSILON):
    return undo_filter(audio, cutoff, sample_rate, "high", order, epsilon)

def undo_band_stop(audio, low, high, sample_rate, order=6, epsilon=EPSILON):
    """Included so the failure can be seen rather than argued about.

    Inside the stop band the filter passed nothing, so there is nothing to
    divide back up. Run it and look at the row error: the rows inside the band
    do not come back. That is the honest result, and it is why the model
    exists.
    """
    return undo_filter(audio, (low, high), sample_rate, "bandstop", order, epsilon)

def undo_resample(audio, sample_rate, target_rate, anti_alias=True):
    """There is no undo here, and this function says so by doing nothing.

    Sampling at a lower rate throws away everything above half that rate. If
    the anti-alias filter was on, that content is simply gone. If it was off,
    it is worse than gone: it folded down and was added to a lower frequency,
    so one bin now holds the sum of two rows. No filter separates a sum back
    into its parts.
    """
    return np.asarray(audio, dtype=np.float64).copy()

# --------------------------------------------------------------------------
# A whole chain
# --------------------------------------------------------------------------

def undo_chain(audio, sample_rate, effects, epsilon=EPSILON, fix_gain=False,
               target_peak=OPEN_PEAK):
    """Undo what can be undone, in reverse order.

    Reverse, because effects were applied one after another: the last thing
    done is the first thing undone, the same way you take off a coat before a
    jumper.

    Three outcomes, kept apart because they mean different things:
      undone     it had an inverse and the inverse ran
      attempted  the inverse ran but cannot fully work (band-stop: the
                 shoulders come back, the killed rows do not)
      skipped    no inverse exists, so nothing was done

    `fix_gain` is off by default. Only turn it on when the level really is
    unknown: the decoder already divides by the gain stored in the metadata,
    so rescaling audio that was never gain-corrupted makes things worse.
    """
    out = np.asarray(audio, dtype=np.float64).copy()
    done, attempted, skipped = [], [], []

    for fx in reversed(effects or []):
        kind = fx.get("type")

        if kind == "echo":
            out = undo_echo(out, fx.get("delay", 0.08), fx.get("decay", 0.4),
                            sample_rate)
            done.append(kind)
        elif kind == "lowpass":
            out = undo_low_pass(out, fx.get("cutoff", 4000), sample_rate,
                                epsilon=epsilon)
            done.append(kind)
        elif kind == "highpass":
            out = undo_high_pass(out, fx.get("cutoff", 2000), sample_rate,
                                 epsilon=epsilon)
            done.append(kind)
        elif kind == "bandstop":
            # attempted, and it will not work: see undo_band_stop
            out = undo_band_stop(out, fx.get("low", 3000), fx.get("high", 5000),
                                 sample_rate, epsilon=epsilon)
            attempted.append(kind)
        else:
            skipped.append(kind)

    if fix_gain:
        out = undo_gain(out, target_peak)

    return out, {"undone": done, "attempted": attempted, "skipped": skipped}
