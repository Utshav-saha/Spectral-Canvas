"""The Track 1 restoration model: what the LTI inverse cannot undo.

Order matters, and it is the order `docs/RESTORATION_PLAN.md` asks for:

    damaged audio  ->  LTI inverse (inverse.py)  ->  activation  ->  model

The inverse goes first because it is exact where it applies. The model's job
is only what is left: clipping (nonlinear, no H(f) to divide by), rows a
stop-band annihilated, aliasing that folded two rows into one, and whatever
noise survives. Handing it work the inverse already did just teaches it to
redo that work worse.

**The input is 5 planes**, which is what `restore_v3.pt` expects:

    0,1,2   the activation, 0..1, red/green/blue (grayscale is repeated)
    3       the row index, 0 at the top row and 1 at the bottom
    4       a confidence mask: 1 where the row arrived, 0 where it was killed

Channel 3 is there because a convolution is the same everywhere it looks, and
this damage is not: row 0 is the highest frequency and row N the lowest, so
the model has to be told which row it is standing on. Channel 4 marks rows
that carry no signal at all, so it treats them as blanks to fill rather than
data to polish.

**The output is a residual** - the change to add to the activation. Neither
checkpoint recorded how it was fed, so both conventions were measured rather
than assumed, and `restore_v3.pt` agrees with the one before it: reading the
output as the picture scores 0.23-0.24 mean activation error against
0.044-0.050 for adding it, and inverting the channel-4 mask scores 0.33-0.35.
Both wrong turns degrade quietly, which is why they are measured.

**What this checkpoint currently does.** Measured end to end on the real
pipeline at 128x128 grayscale, 16 levels, against the activation that was
sent (mean activation error, lower is better). `restore_best.pt` is the
checkpoint this one replaced, run on the same bench:

                   damaged   +inverse   v3     restore_best
    clipping        0.0453    0.0453   0.0431    0.0373
    clip + noise    0.0423    0.0423   0.0424    0.0365
    noise           0.0003    0.0003   0.0004    0.0379
    band-stop       0.0826    0.0472   0.0427    0.0820
    low-pass        0.1444    0.0450   0.0417    0.0795
    echo            0.0533    0.0000   0.0000    0.0377
    already clean   0.0000    0.0000   0.0000    0.0377

The shape of the trade changed, and that is the reason to prefer v3. The old
checkpoint bought its win on clipping by repainting everything it touched: it
put 0.0377 of error into a picture that had *nothing* wrong with it, and it
broke echo, which the inverse had already undone exactly. v3 leaves clean
input alone (0.0000), keeps echo exact, and now *improves* band-stop and
low-pass on top of the inverse where the old one made both markedly worse.
It gives up a little on raw clipping (0.0431 against 0.0373) to do it.

So it no longer has to be kept away from undamaged pictures. The bench still
reports all three columns - damaged, inverted, model - because which stage
earned the repair is the thing worth showing, not the final number alone.
Swap in another checkpoint and these numbers move; nothing else has to change,
because the shape is read off the file.
"""

import os

import numpy as np

from spectral.restore import model as unet

MODEL_PATH = os.environ.get("SPECTRAL_RESTORER", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools", "restore_v3.pt"))

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
                "message": f"No model file at {MODEL_PATH}. Put restore_v3.pt "
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
