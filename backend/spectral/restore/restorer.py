"""The Track 1 restoration model: what the LTI inverse cannot undo.

Order matters, and it is the order `docs/RESTORATION_PLAN.md` asks for:

    damaged audio  ->  LTI inverse (inverse.py)  ->  activation  ->  model

The inverse goes first because it is exact where it applies. The model's job
is only what is left: clipping (nonlinear, no H(f) to divide by), rows a
stop-band annihilated, aliasing that folded two rows into one, and whatever
noise survives. Handing it work the inverse already did just teaches it to
redo that work worse.

**The input is 5 planes**, which is what `restore_best.pt` expects:

    0,1,2   the activation, 0..1, red/green/blue (grayscale is repeated)
    3       the row index, 0 at the top row and 1 at the bottom
    4       a confidence mask: 1 where the row arrived, 0 where it was killed

Channel 3 is there because a convolution is the same everywhere it looks, and
this damage is not: row 0 is the highest frequency and row N the lowest, so
the model has to be told which row it is standing on. Channel 4 marks rows
that carry no signal at all, so it treats them as blanks to fill rather than
data to polish.

**The output is a residual** - the change to add to the activation. Measured
on this checkpoint, taking the output as the picture scores about 0.33 mean
activation error against 0.05-0.09 for adding it, so residual it is.

**What this checkpoint currently does**, measured end to end at 128x128 on
the real pipeline (mean activation error, lower is better):

    clipping         0.1471 -> 0.0939     helps, the case it was built for
    band-stop        0.2011 -> 0.1658     helps
    low-pass (raw)   0.3213 -> 0.2593     helps
    low-pass (after the inverse)  0.0578 -> 0.0958   hurts
    noise            0.0246 -> 0.0527     hurts
    already clean    0.0000 -> 0.0523     hurts

So it earns its place on heavy, non-invertible damage and gets in the way of
everything else. That is why the bench reports all three columns - damaged,
inverted, model - rather than quietly applying the model and showing one
number. Swap in a better `restore_best.pt` and these numbers move; nothing
else has to change.
"""

import os

import numpy as np

from spectral.restore import model as unet

MODEL_PATH = os.environ.get("SPECTRAL_RESTORER", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools", "restore_best.pt"))

# A row carrying less than this share of the average row's energy is treated
# as killed rather than quiet. From RESTORATION_PLAN 1b.
DEAD_ROW = 0.05

_CACHE = {}


def available():
    try:
        unet.torch_module()
    except RuntimeError:
        return False
    return os.path.isfile(MODEL_PATH)


def status():
    """Why it cannot run, in words someone can act on."""
    try:
        unet.torch_module()
    except RuntimeError:
        return {"ready": False, "message": unet.MISSING_TORCH}
    if not os.path.isfile(MODEL_PATH):
        return {"ready": False,
                "message": f"No model file at {MODEL_PATH}. Put restore_best.pt "
                           "there, or set SPECTRAL_RESTORER to where it is."}
    try:
        _, shape = load()
    except Exception as exc:
        return {"ready": False, "message": f"{MODEL_PATH} could not be loaded: {exc}"}
    return {"ready": True, "message": None, "path": MODEL_PATH,
            "inputs": shape[0], "outputs": shape[2]}


def load():
    """Load once and keep it; re-reading 15 MB per request would be silly."""
    stamp = os.path.getmtime(MODEL_PATH)
    if _CACHE.get("stamp") != stamp:
        # the file changed on disk, so pick the new one up without a restart
        net, shape = unet.load(MODEL_PATH)
        _CACHE.update(stamp=stamp, net=net, shape=shape)
    return _CACHE["net"], _CACHE["shape"]


# --------------------------------------------------------------------------
# The extra input planes
# --------------------------------------------------------------------------

def row_index_plane(rows, columns):
    """0 at the top row, 1 at the bottom. Top row = highest frequency."""
    ramp = np.linspace(0.0, 1.0, rows, dtype=np.float32)
    return np.repeat(ramp[:, None], columns, axis=1)


def confidence_mask(activation, threshold=DEAD_ROW):
    """1 where the row arrived, 0 where nothing did.

    A stop-band does not quieten a row, it deletes it, and the difference
    matters: a genuinely dark row of the picture is data, while a row with no
    energy at all is a hole. Comparing each row's energy with the average row
    separates the two.
    """
    activation = np.asarray(activation, dtype=np.float32)
    per_row = activation.mean(axis=tuple(range(1, activation.ndim)))
    reference = float(np.mean(per_row))
    if reference <= 0:
        return np.ones(activation.shape[:2], dtype=np.float32)

    alive = (per_row >= threshold * reference).astype(np.float32)
    return np.repeat(alive[:, None], activation.shape[1], axis=1)


def planes_for(activation, in_channels):
    """Stack the activation and whatever extra planes the checkpoint wants."""
    activation = np.asarray(activation, dtype=np.float32)
    if activation.ndim == 2:
        colour = np.stack([activation] * 3, axis=-1)
    else:
        colour = activation[..., :3]
        if colour.shape[-1] == 1:
            colour = np.repeat(colour, 3, axis=-1)

    if in_channels == 3:            # a 3-channel model: just the picture
        return colour

    rows, columns = colour.shape[:2]
    extra = [row_index_plane(rows, columns)[..., None],
             confidence_mask(colour)[..., None]]
    return np.concatenate([colour] + extra[:in_channels - 3], axis=-1)


# --------------------------------------------------------------------------
# Using it
# --------------------------------------------------------------------------

def restore(activation):
    """Cleaned activation, same shape and range as what went in.

      1. build the input planes the checkpoint asks for
      2. run the model, which returns a residual
      3. add it on and clamp back into 0..1
      4. hand back grayscale if grayscale went in
    """
    net, shape = load()
    in_channels = shape[0]

    array = np.asarray(activation, dtype=np.float32)
    was_gray = (array.ndim == 2)

    planes = planes_for(array, in_channels)
    residual = unet.predict_residual(net, np.clip(planes, 0.0, 1.0))

    colour = planes[..., :3]
    out = np.clip(colour + residual, 0.0, 1.0)
    return out.mean(axis=-1) if was_gray else out


def restore_image(image_array, gray_levels=16):
    """The same thing for callers holding pixels rather than activation.

    Pixels are the activation upside down - 0 activation is white, 255 - so it
    converts, restores, and converts back through the decoder's own map.
    """
    from spectral.decoder.image_reconstructor import to_image_array

    array = np.asarray(image_array, dtype=np.float32) / 255.0
    restored = restore(1.0 - array)         # pixels -> activation
    return to_image_array(restored, gray_levels)
