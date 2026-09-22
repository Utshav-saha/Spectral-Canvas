"""The learned upscaler: the Track 2 half of the restoration plan.

Generation B sends a small picture - 32x32 at 4 gray levels - because that is
all a voice call has the airtime for. The bits arrive exact, so nothing is
broken in transit. What is missing was thrown away *before* the call: detail
(32x32 instead of 128x128) and shades (4 levels instead of 256).

Those two losses are many-to-one, so no formula undoes them. A model can make
a good guess, because it has seen a lot of pictures. That is all this is.

    small, blocky, 4 shades   ->   model   ->   128x128, smooth

**How the checkpoint expects to be used.** `tools/upscaler_best.pt` is a
plain state_dict for the U-Net below. Nothing recorded how it was fed, so it
was measured, by running every plausible convention on known pairs and keeping
the best:

    input      RGB, values 0..1, already resized to 128x128 with BICUBIC
    output     the RESIDUAL - what to ADD to the input, not the picture
    activation ReLU

Reading the output as the picture gives 0.187 mean error; adding it to the
input gives 0.0735, against 0.0952 for plain bicubic. So residual it is.

**What it does not do.** It was trained on upscaling and dequantising, so
that is all it is good at. Measured on Track 1 activation matrices (clipping,
dead rows, noise) it made every case worse, including clean input, which is
what happens when a model is used outside the job it learned. Track 1 is
handled by `spectral/channel/inverse.py` instead.
"""

import os

import numpy as np
from PIL import Image

# The trained weights, sitting next to the script that made the dataset.
MODEL_PATH = os.environ.get("SPECTRAL_UPSCALER", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools", "upscaler_best.pt"))

SIZE = 128          # what the model was trained at

MISSING_TORCH = ("The restoration model needs PyTorch, which is not installed "
                 "on this server. Install it with: pip install torch")

_CACHE = {}         # loaded once; loading 15 MB per request would be silly


def _torch():
    try:
        import torch
        return torch
    except ImportError as exc:
        raise RuntimeError(MISSING_TORCH) from exc


def available():
    """Both halves have to be here: the library and the weights."""
    try:
        _torch()
    except RuntimeError:
        return False
    return os.path.isfile(MODEL_PATH)


def status():
    """Why it cannot run, in words a person can act on."""
    try:
        _torch()
    except RuntimeError:
        return {"ready": False, "message": MISSING_TORCH}
    if not os.path.isfile(MODEL_PATH):
        return {"ready": False,
                "message": f"No model file at {MODEL_PATH}. Put upscaler_best.pt "
                           "there, or set SPECTRAL_UPSCALER to where it is."}
    return {"ready": True, "message": None, "path": MODEL_PATH, "size": SIZE}


# --------------------------------------------------------------------------
# The network
# --------------------------------------------------------------------------
# A U-Net. It shrinks the picture three times while widening it (more channels
# = more kinds of pattern it can notice), does its thinking at the smallest
# size, then grows it back. Each step up is joined to the matching step down
# ("skip connection"), so fine detail is not lost on the way through.
#
# The shapes here are not a choice - they are read off the checkpoint, and
# load_state_dict(strict=True) fails loudly if any of it is wrong.

def _build(torch):
    nn = torch.nn

    class Residual(nn.Module):
        """conv -> relu -> conv, then add the input back.

        Adding the input back means the block only has to learn the *change*,
        which is easier than relearning the whole picture each time.
        """

        def __init__(self, channels):
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv2d(channels, channels, 3, padding=1),
                nn.ReLU(),
                nn.Conv2d(channels, channels, 3, padding=1),
            )

        def forward(self, x):
            return x + self.body(x)

    class UNet(nn.Module):
        def __init__(self, base=32):
            super().__init__()
            c1, c2, c3, c4 = base, base * 2, base * 4, base * 8

            self.inp = nn.Conv2d(3, c1, 3, padding=1)        # 3 colours -> 32
            self.e1 = nn.Sequential(Residual(c1), Residual(c1))
            self.d1 = nn.Conv2d(c1, c2, 2, stride=2)         # 128 -> 64
            self.e2 = nn.Sequential(Residual(c2), Residual(c2))
            self.d2 = nn.Conv2d(c2, c3, 2, stride=2)         # 64 -> 32
            self.e3 = nn.Sequential(Residual(c3), Residual(c3))
            self.d3 = nn.Conv2d(c3, c4, 2, stride=2)         # 32 -> 16
            self.mid = nn.Sequential(Residual(c4), Residual(c4))

            self.u3 = nn.ConvTranspose2d(c4, c3, 2, stride=2)   # 16 -> 32
            self.f3 = nn.Conv2d(c3 * 2, c3, 1)                  # join the skip
            self.r3 = Residual(c3)
            self.u2 = nn.ConvTranspose2d(c3, c2, 2, stride=2)   # 32 -> 64
            self.f2 = nn.Conv2d(c2 * 2, c2, 1)
            self.r2 = Residual(c2)
            self.u1 = nn.ConvTranspose2d(c2, c1, 2, stride=2)   # 64 -> 128
            self.f1 = nn.Conv2d(c1 * 2, c1, 1)
            self.r1 = Residual(c1)
            self.out = nn.Conv2d(c1, 3, 3, padding=1)        # 32 -> 3 colours

        def forward(self, x):
            skip1 = self.e1(self.inp(x))            # full size
            skip2 = self.e2(self.d1(skip1))         # half
            skip3 = self.e3(self.d2(skip2))         # quarter
            deep = self.mid(self.d3(skip3))         # eighth

            y = self.r3(self.f3(torch.cat([self.u3(deep), skip3], dim=1)))
            y = self.r2(self.f2(torch.cat([self.u2(y), skip2], dim=1)))
            y = self.r1(self.f1(torch.cat([self.u1(y), skip1], dim=1)))
            return self.out(y)                      # the residual, not the image

    return UNet


def load():
    """Load the weights once and keep them."""
    if "net" in _CACHE:
        return _CACHE["net"]

    torch = _torch()
    if not os.path.isfile(MODEL_PATH):
        raise RuntimeError(status()["message"])

    net = _build(torch)()
    net.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    net.eval()                      # no dropout/batchnorm updates; just predict
    _CACHE["net"] = net
    return net


# --------------------------------------------------------------------------
# Using it
# --------------------------------------------------------------------------

def enhance(image_array, size=SIZE):
    """Small blocky picture in, bigger smoother picture out.

    `image_array` is what the decoder rebuilt: uint8, either (H, W) grayscale
    or (H, W, 3) colour. The result is (size, size) or (size, size, 3) uint8.

    The five steps:
      1. remember whether it was grayscale, and make it 3-channel either way
      2. resize up to 128x128 with bicubic - the model expects that
      3. run the model, which returns a residual
      4. add the residual to the input and clamp back into 0..1
      5. put it back to uint8, and back to grayscale if it started that way
    """
    torch = _torch()
    net = load()

    array = np.asarray(image_array)
    was_gray = (array.ndim == 2)
    if was_gray:
        array = np.stack([array] * 3, axis=-1)
    if array.shape[-1] == 4:                    # drop alpha if one turns up
        array = array[..., :3]

    # step 2: up to the size the model was trained at
    big = Image.fromarray(array.astype(np.uint8), "RGB").resize(
        (size, size), Image.BICUBIC)
    x = np.asarray(big, dtype=np.float32) / 255.0

    # step 3: (H, W, 3) -> (1, 3, H, W), which is the order torch wants
    batch = torch.from_numpy(x).permute(2, 0, 1).unsqueeze(0)
    with torch.no_grad():                       # predicting, not training
        residual = net(batch)
    residual = residual.squeeze(0).permute(1, 2, 0).numpy()

    # step 4: the model says what to CHANGE, so add it on
    out = np.clip(x + residual, 0.0, 1.0)

    # step 5
    out = np.round(out * 255).astype(np.uint8)
    if was_gray:
        out = np.round(out.mean(axis=-1)).astype(np.uint8)
    return out


def compare(before, after):
    """A couple of numbers for the page: how much actually changed."""
    a = np.asarray(before, dtype=np.float64)
    b = np.asarray(after, dtype=np.float64)
    if a.shape != b.shape:                      # different sizes: match them up
        a = np.asarray(Image.fromarray(np.asarray(before).astype(np.uint8)).resize(
            (b.shape[1], b.shape[0]), Image.BICUBIC), dtype=np.float64)
    return {
        "mean_change": round(float(np.mean(np.abs(b - a))), 2),
        "levels_before": int(len(np.unique(np.asarray(before)))),
        "levels_after": int(len(np.unique(np.asarray(after)))),
    }
