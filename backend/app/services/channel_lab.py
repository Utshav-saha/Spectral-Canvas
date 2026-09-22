"""The experiments bench: put a transmission through a channel and measure it.

`spectral/channel/effects.py` has been in the repo since the start with nothing
calling it. This is the layer that exposes it, because the interesting question
is not "what does a low-pass filter sound like" but "which rows of the picture
does it destroy, and by how much".

Every effect here is y[n] = x[n] * h[n] + w[n] for some h and w, and each has a
visible consequence in the recovered image, because the encoder puts one image
row on one frequency:

    low-pass    the top rows fade      (high frequencies carry the top rows)
    high-pass   the bottom rows fade
    band-stop   a horizontal band vanishes outright
    echo        columns smear rightwards
    clipping    intermodulation invents rows that were never sent
    resample    without anti-aliasing, two rows fold into one

The first four are linear and time-invariant, so they have an inverse and the
analytic stage can undo them. Clipping is memoryless but nonlinear, and
aliasing is many-to-one; neither has an inverse, which is what the restoration
model is for. Keeping that boundary visible is the point of this page.
"""

import numpy as np

from spectral.channel import effects, inverse
from spectral.analysis import waveform as wf
from app.services import pipeline

SAMPLE_RATE_FALLBACK = 44100

# What the UI offers, and the bounds each parameter is clamped to. Served from
# /api/channel/effects so the page and the validator cannot drift apart.
CATALOGUE = [
    {
        "id": "lowpass",
        "label": "Low-pass",
        "invertible": True,
        "summary": "Rolls off everything above the cutoff. The top image rows "
                   "ride the highest tones, so they fade first.",
        "params": [
            {"id": "cutoff", "label": "Cutoff", "unit": "Hz", "type": "number",
             "min": 200, "max": 20000, "step": 100, "default": 4000},
        ],
    },
    {
        "id": "highpass",
        "label": "High-pass",
        "invertible": True,
        "summary": "The mirror image: the bottom rows go first.",
        "params": [
            {"id": "cutoff", "label": "Cutoff", "unit": "Hz", "type": "number",
             "min": 100, "max": 18000, "step": 100, "default": 2000},
        ],
    },
    {
        "id": "bandstop",
        "label": "Band-stop",
        "invertible": False,
        "summary": "Removes a band entirely. Those rows are not attenuated, "
                   "they are annihilated - dividing them back up only "
                   "amplifies noise, so this is a job for the model.",
        "params": [
            {"id": "low", "label": "From", "unit": "Hz", "type": "number",
             "min": 100, "max": 19000, "step": 100, "default": 3000},
            {"id": "high", "label": "To", "unit": "Hz", "type": "number",
             "min": 200, "max": 20000, "step": 100, "default": 5000},
        ],
    },
    {
        "id": "echo",
        "label": "Echo",
        "invertible": True,
        "summary": "h[n] = d[n] + decay*d[n-D]. Stable inverse for decay < 1, "
                   "so a recursive filter undoes it exactly.",
        "params": [
            {"id": "delay", "label": "Delay", "unit": "s", "type": "number",
             "min": 0.005, "max": 0.5, "step": 0.005, "default": 0.08},
            {"id": "decay", "label": "Decay", "unit": "", "type": "number",
             "min": 0.05, "max": 0.95, "step": 0.05, "default": 0.4},
        ],
    },
    {
        "id": "noise",
        "label": "Noise",
        "invertible": False,
        "summary": "Additive white Gaussian noise at a chosen SNR. Not "
                   "invertible, but averaging over a frame suppresses it.",
        "params": [
            {"id": "snr_db", "label": "SNR", "unit": "dB", "type": "number",
             "min": -10, "max": 60, "step": 1, "default": 20},
        ],
    },
    {
        "id": "clip",
        "label": "Clipping",
        "invertible": False,
        "summary": "Memoryless but nonlinear, so there is no h[n] and no "
                   "H(f). Intermodulation puts energy at 2f1-f2, inventing "
                   "rows that were never transmitted. The model's main job.",
        "params": [
            {"id": "threshold", "label": "Threshold", "unit": "", "type": "number",
             "min": 0.02, "max": 1.0, "step": 0.01, "default": 0.3},
        ],
    },
    {
        "id": "resample",
        "label": "Resample",
        "invertible": False,
        "summary": "Down and back up. With the anti-alias filter off, two "
                   "rows fold into one bin and the sum is many-to-one.",
        "params": [
            {"id": "target_rate", "label": "Rate", "unit": "Hz", "type": "number",
             "min": 4000, "max": 44100, "step": 1000, "default": 16000},
            {"id": "anti_alias", "label": "Anti-alias filter", "type": "boolean",
             "default": True},
        ],
    },
]

BY_ID = {entry["id"]: entry for entry in CATALOGUE}


def catalogue():
    """What the page offers, plus what an LTI inverse can and cannot undo.

    `inversion` is served from spectral/channel/inverse.py, so the table on
    the page and the code that does the undoing can never disagree.
    """
    return {"effects": CATALOGUE,
            "presets": PRESETS,
            "max_effects": MAX_EFFECTS,
            "inversion": inverse.report(),
            "default_epsilon": inverse.EPSILON}


# Ready-made chains, so the page opens on something worth looking at rather
# than an empty list.
PRESETS = [
    {"id": "telephone", "label": "Telephone line",
     "note": "300-3400 Hz, which is what a voice call actually passes.",
     "effects": [{"type": "highpass", "cutoff": 300},
                 {"type": "lowpass", "cutoff": 3400}]},
    {"id": "hall", "label": "Room echo",
     "note": "A single reflection. Invertible, and a good derivation.",
     "effects": [{"type": "echo", "delay": 0.08, "decay": 0.4}]},
    {"id": "overdriven", "label": "Overdriven amplifier",
     "note": "The nonlinear case. No inverse exists.",
     "effects": [{"type": "clip", "threshold": 0.15}]},
    {"id": "notch", "label": "Notched band",
     "note": "A band of rows destroyed rather than attenuated.",
     "effects": [{"type": "bandstop", "low": 3000, "high": 5000}]},
    {"id": "aliased", "label": "Aliased downsample",
     "note": "Anti-alias filter off, so rows fold onto each other.",
     "effects": [{"type": "resample", "target_rate": 12000, "anti_alias": False}]},
    {"id": "noisy", "label": "Noisy line",
     "note": "10 dB SNR on top of a telephone band.",
     "effects": [{"type": "highpass", "cutoff": 300},
                 {"type": "lowpass", "cutoff": 3400},
                 {"type": "noise", "snr_db": 10}]},
]

MAX_EFFECTS = 6


def validate_chain(chain, sample_rate):
    """Raise ValueError with something a person can act on, and clamp the rest.

    The messages here reach the user verbatim, so they say what to do rather
    than what went wrong.
    """
    if not chain:
        raise ValueError("Add at least one effect to run an experiment.")
    if len(chain) > MAX_EFFECTS:
        raise ValueError(f"Keep the chain to {MAX_EFFECTS} effects or fewer.")

    nyquist = sample_rate / 2.0
    clean = []

    for step in chain:
        kind = (step or {}).get("type")
        spec = BY_ID.get(kind)
        if spec is None:
            raise ValueError(f"There is no effect called {kind!r}.")

        out = {"type": kind}
        for param in spec["params"]:
            value = step.get(param["id"], param["default"])
            if param["type"] == "boolean":
                out[param["id"]] = bool(value)
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{spec['label']}: {param['label']} must be a number.")
            value = max(param["min"], min(param["max"], value))
            out[param["id"]] = value

        # Frequencies above Nyquist are not an opinion, they are undefined.
        for key in ("cutoff", "low", "high"):
            if key in out and out[key] >= nyquist:
                raise ValueError(
                    f"{spec['label']}: {key} must stay below half the sample "
                    f"rate ({nyquist:.0f} Hz).")
        if kind == "bandstop" and out["low"] >= out["high"]:
            raise ValueError("Band-stop: the upper edge must be above the lower one.")
        if kind == "resample" and out["target_rate"] > sample_rate:
            raise ValueError("Resample: the target rate must be below the "
                             "transmission's own sample rate.")
        clean.append(out)

    return clean


def describe(chain):
    """One readable line per step, for the report and the page's slug."""
    lines = []
    for step in chain:
        spec = BY_ID[step["type"]]
        bits = []
        for param in spec["params"]:
            value = step[param["id"]]
            if param["type"] == "boolean":
                bits.append(f"{param['label']}: {'on' if value else 'off'}")
            else:
                shown = f"{value:g}"
                bits.append(f"{param['label']} {shown}{param['unit']}")
        lines.append(f"{spec['label']} ({', '.join(bits)})" if bits else spec["label"])
    return lines


def row_profile(activation_before, activation_after):
    """Mean absolute error per image row.

    This is the plot that makes the point: one image row is one frequency, so
    a band-stop shows up as a spike in a contiguous run of rows, and a low-pass
    as a ramp at one end. The error is not spread evenly, and saying so is
    most of the Signals and Systems argument.
    """
    before = np.asarray(activation_before, dtype=float)
    after = np.asarray(activation_after, dtype=float)
    if before.shape != after.shape:
        return None
    if before.ndim == 3:
        before = before.mean(axis=2)
        after = after.mean(axis=2)
    return [round(float(v), 4) for v in np.mean(np.abs(after - before), axis=1)]


def _same_length(audio, reference):
    """apply_chain can change the length (resampling rounds), and the decoder
    slices by frame, so bring it back to exactly what was sent."""
    if len(audio) == len(reference):
        return audio
    fixed = np.zeros_like(reference)
    usable = min(len(fixed), len(audio))
    fixed[:usable] = audio[:usable]
    return fixed


def model_status():
    """Whether the Track 1 restoration model can run here."""
    from spectral.restore import restorer
    return restorer.status()


def run_channel(session, chain, caller=None, receiver=None, pin=None,
                undo=False, epsilon=None, restore=False):
    """Clean transmission -> degraded audio -> decode -> what it cost.

    With `undo`, the same degraded audio also goes through the LTI inverse
    (spectral/channel/inverse.py) and is decoded a second time, so the page
    can show damaged and repaired side by side. Effects with no inverse are
    left alone, which is the honest half of the demonstration.

    With `restore`, the learned model runs on top of that - on the inverted
    picture when there is one, otherwise on the damaged one, because the
    inverse is exact where it applies and the model should only be asked for
    what is left. All three are returned, never merged: that is the
    three-column table RESTORATION_PLAN Phase 5 asks for.
    """
    metadata = session.get("metadata")
    if not metadata:
        raise ValueError("That transmission has no header to rebuild from.")
    if metadata.get("kind") == "text":
        raise ValueError("The channel bench works on pictures. Send an image "
                         "or a doodle, then come back.")

    sample_rate = metadata.get("sample_rate", SAMPLE_RATE_FALLBACK)
    chain = validate_chain(chain, sample_rate)

    clean = np.asarray(session["audio"], dtype=np.float64)
    degraded = effects.apply_chain(clean, sample_rate, chain)

    degraded = _same_length(degraded, clean)

    decoded = pipeline.run_decode(
        degraded, metadata, caller=caller, receiver=receiver, pin=pin,
        source_activation=session.get("activation"))

    result = {
        "audio": degraded,
        "chain": chain,
        "description": describe(chain),
        "png": decoded["png"],
        "image_array": decoded["image_array"],
        "metrics": decoded["metrics"],
        "stats": wf.global_stats(degraded, sample_rate),
        "clean_stats": wf.global_stats(clean, sample_rate),
        "sample_rate": sample_rate,
    }

    if undo:
        eps = inverse.EPSILON if epsilon is None else float(epsilon)
        repaired, what = inverse.undo_chain(degraded, sample_rate, chain,
                                            epsilon=eps)
        repaired = _same_length(repaired, clean)
        redecoded = pipeline.run_decode(
            repaired, metadata, caller=caller, receiver=receiver, pin=pin,
            source_activation=session.get("activation"))
        result["undone"] = {
            "audio": repaired,
            "png": redecoded["png"],
            "image_array": redecoded["image_array"],
            "metrics": redecoded["metrics"],
            "stats": wf.global_stats(repaired, sample_rate),
            "epsilon": eps,
            **what,
        }

    if restore:
        from spectral.restore import restorer

        if not restorer.available():
            raise ValueError(restorer.status()["message"])

        # on top of the inverse when it ran: the inverse is exact where it
        # applies, and the model should only be handed what is left
        source = result.get("undone", result)
        levels = metadata.get("gray_levels", 16)
        restored_image = restorer.restore_image(source["image_array"], levels)
        result["restored"] = {
            "png": pipeline.to_png_bytes(restored_image),
            "image_array": restored_image,
            "metrics": pipeline.image_metrics(
                restored_image, session.get("activation"), levels),
            "after": "inverse" if "undone" in result else "channel",
        }

    return result
