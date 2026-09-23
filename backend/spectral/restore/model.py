"""The U-Net both restoration models use, and the loader that reads one.

There are two checkpoints in `tools/`:

    upscaler_best.pt   Track 2: 3 channels in  (a small picture)
    restore_v3.pt      Track 1: 5 channels in  (activation + row index + mask)

They are the same network apart from that first layer, so it is written once
here and the shape is read off the file rather than hardcoded:

    inp.weight is (base, in_channels, 3, 3)
    out.weight is (out_channels, base, 3, 3)

That is what makes a model swappable. Retrain it, drop the new .pt in place of
the old one, and this picks up the new shape on its own. `load_state_dict` is
strict, so a file that is not this architecture fails loudly at load instead
of quietly producing a worse picture.

**What both were trained to output is a residual** - the CHANGE to add to the
input, not the finished picture. Measured on both checkpoints: reading the
output directly scores far worse than adding it. See each wrapper for numbers.
"""

import numpy as np

MISSING_TORCH = ("The restoration models need PyTorch, which is not installed "
                 "on this server. Install it with: "
                 "pip install torch --index-url https://download.pytorch.org/whl/cpu")


def torch_module():
    try:
        import torch
        return torch
    except ImportError as exc:
        raise RuntimeError(MISSING_TORCH) from exc


def build(torch, in_channels=3, base=32, out_channels=3):
    """The network. Shapes come from the caller, which read them off the file."""
    nn = torch.nn

    class Residual(nn.Module):
        """conv -> relu -> conv, then add the input back on.

        Adding the input back means each block only has to learn the *change*,
        which trains faster than relearning the whole picture every time.
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
        """Shrink three times while widening, think, then grow back.

        Each step up is joined to the matching step down (the "skip"), so the
        fine detail that survived is not lost on the way through.
        """

        def __init__(self):
            super().__init__()
            c1, c2, c3, c4 = base, base * 2, base * 4, base * 8

            self.inp = nn.Conv2d(in_channels, c1, 3, padding=1)
            self.e1 = nn.Sequential(Residual(c1), Residual(c1))
            self.d1 = nn.Conv2d(c1, c2, 2, stride=2)          # size / 2
            self.e2 = nn.Sequential(Residual(c2), Residual(c2))
            self.d2 = nn.Conv2d(c2, c3, 2, stride=2)          # size / 4
            self.e3 = nn.Sequential(Residual(c3), Residual(c3))
            self.d3 = nn.Conv2d(c3, c4, 2, stride=2)          # size / 8
            self.mid = nn.Sequential(Residual(c4), Residual(c4))

            self.u3 = nn.ConvTranspose2d(c4, c3, 2, stride=2)
            self.f3 = nn.Conv2d(c3 * 2, c3, 1)                # join the skip
            self.r3 = Residual(c3)
            self.u2 = nn.ConvTranspose2d(c3, c2, 2, stride=2)
            self.f2 = nn.Conv2d(c2 * 2, c2, 1)
            self.r2 = Residual(c2)
            self.u1 = nn.ConvTranspose2d(c2, c1, 2, stride=2)
            self.f1 = nn.Conv2d(c1 * 2, c1, 1)
            self.r1 = Residual(c1)
            self.out = nn.Conv2d(c1, out_channels, 3, padding=1)

        def forward(self, x):
            skip1 = self.e1(self.inp(x))
            skip2 = self.e2(self.d1(skip1))
            skip3 = self.e3(self.d2(skip2))
            deep = self.mid(self.d3(skip3))

            y = self.r3(self.f3(torch.cat([self.u3(deep), skip3], dim=1)))
            y = self.r2(self.f2(torch.cat([self.u2(y), skip2], dim=1)))
            y = self.r1(self.f1(torch.cat([self.u1(y), skip1], dim=1)))
            return self.out(y)                  # the residual, not the picture

    return UNet()


def shape_of(state_dict):
    """(in_channels, base, out_channels), read off the weights themselves."""
    base, in_channels = state_dict["inp.weight"].shape[:2]
    out_channels = state_dict["out.weight"].shape[0]
    return int(in_channels), int(base), int(out_channels)


def load(path):
    """Read a checkpoint and return (net, shape). Strict: no silent mismatch."""
    torch = torch_module()
    state = torch.load(path, map_location="cpu")
    if not isinstance(state, dict) or "inp.weight" not in state:
        # a whole-model torch.save, or a training checkpoint with the weights
        # tucked under a key
        for key in ("model", "state_dict", "net", "weights"):
            if isinstance(state, dict) and key in state:
                state = state[key]
                break
    if "inp.weight" not in state:
        raise RuntimeError(
            f"{path} does not look like a state_dict for this U-Net. Save it "
            "with torch.save(model.state_dict(), path).")

    in_channels, base, out_channels = shape_of(state)
    net = build(torch, in_channels, base, out_channels)
    net.load_state_dict(state)
    net.eval()          # predicting, not training
    return net, (in_channels, base, out_channels)


# --------------------------------------------------------------------------
# Running one
# --------------------------------------------------------------------------

BLOCK = 8       # three halvings, so the input has to be a multiple of 8


def pad_to_block(array, block=BLOCK):
    """Pad (H, W, C) up to a multiple of `block`, and say how much was added.

    The U-Net halves the picture three times. A size that is not a multiple of
    8 would not come back the same shape, so anything odd is padded by
    repeating the edge (not zeros, which would look like a black border and
    make the model invent an edge that is not there).
    """
    height, width = array.shape[:2]
    down = (block - height % block) % block
    right = (block - width % block) % block
    if not down and not right:
        return array, (0, 0)
    padded = np.pad(array, ((0, down), (0, right), (0, 0)), mode="edge")
    return padded, (down, right)


def predict_residual(net, planes):
    """planes is (H, W, C) float32 0..1. Returns the residual, same H and W."""
    torch = torch_module()

    padded, (down, right) = pad_to_block(np.asarray(planes, dtype=np.float32))
    batch = torch.from_numpy(padded).permute(2, 0, 1).unsqueeze(0)
    with torch.no_grad():
        out = net(batch)
    residual = out.squeeze(0).permute(1, 2, 0).numpy()

    if down or right:
        residual = residual[:residual.shape[0] - down or None,
                            :residual.shape[1] - right or None]
    return residual
