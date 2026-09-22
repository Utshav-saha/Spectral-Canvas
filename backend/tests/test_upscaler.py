"""The learned upscaler (Track 2).

Skips cleanly where PyTorch or the checkpoint is missing, because neither is
needed to run the rest of the project.

The load test is the important one: the architecture in upscaler.py is a
reconstruction of whatever trained that file, and `load_state_dict` is strict,
so if the two ever drift apart this fails loudly instead of quietly producing
a worse picture.
"""

import numpy as np
import pytest
from PIL import Image

from spectral.restore import upscaler

needs_model = pytest.mark.skipif(
    not upscaler.available(),
    reason="no PyTorch or no upscaler_best.pt")


def small_picture(size=32, levels=4):
    """What Generation B actually delivers: small, and only a few shades."""
    grid = (np.add.outer(np.arange(size), np.arange(size)) * 6 % 256).astype("uint8")
    stepped = np.round(grid / 255 * (levels - 1)) / (levels - 1) * 255
    return stepped.astype(np.uint8)


def test_status_always_answers():
    state = upscaler.status()
    assert "ready" in state
    if not state["ready"]:
        assert state["message"]          # and says what to install


@needs_model
def test_the_checkpoint_matches_the_architecture():
    """Strict load: every weight in the file has a home, and every layer in
    the code gets a weight."""
    net = upscaler.load()
    assert sum(p.numel() for p in net.parameters()) > 1_000_000
    assert upscaler.load() is net        # cached, not re-read per request


@needs_model
@pytest.mark.parametrize("size", [16, 32])
def test_grayscale_comes_back_grayscale(size):
    out = upscaler.enhance(small_picture(size))
    assert out.shape == (upscaler.SIZE, upscaler.SIZE)
    assert out.dtype == np.uint8


@needs_model
def test_colour_comes_back_colour():
    colour = np.stack([small_picture()] * 3, axis=-1)
    out = upscaler.enhance(colour)
    assert out.shape == (upscaler.SIZE, upscaler.SIZE, 3)


@needs_model
def test_it_fills_in_the_missing_shades():
    """The whole point of the dequantising half: 4 levels in, a continuous
    ramp out."""
    small = small_picture(levels=4)
    out = upscaler.enhance(small)
    assert len(np.unique(small)) <= 4
    assert len(np.unique(out)) > 40


@needs_model
def test_it_beats_plain_bicubic_on_a_real_photo_like_target():
    """The honest comparison: the model has to beat simply resizing, or there
    is no reason to run it.

    Built the same way the training pairs were (make_dataset.make_dataset_upscale):
    shrink to 32x32, crush to 4 levels, and ask for 128x128 back.
    """
    source = Image.fromarray(
        (np.add.outer(np.arange(128), np.arange(128)) * 2 % 256).astype("uint8"), "L")
    clean = np.asarray(source, dtype=np.float32)

    tiny = np.asarray(source.resize((32, 32), Image.LANCZOS), dtype=np.float32) / 255
    tiny = np.round(np.round(tiny * 3) / 3 * 255).astype(np.uint8)

    bicubic = np.asarray(Image.fromarray(tiny).resize((128, 128), Image.BICUBIC),
                         dtype=np.float32)
    model = upscaler.enhance(tiny).astype(np.float32)

    assert np.abs(model - clean).mean() < np.abs(bicubic - clean).mean()


@needs_model
def test_compare_reports_what_changed():
    small = small_picture()
    stats = upscaler.compare(small, upscaler.enhance(small))
    assert stats["levels_after"] > stats["levels_before"]
    assert stats["mean_change"] > 0


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_info_says_whether_the_model_can_run(client):
    model = client.get("/api/tel/info").json()["model"]
    assert model["ready"] == upscaler.available()
    if not model["ready"]:
        assert model["message"]


def test_enhancing_nothing_is_a_404(client):
    assert client.post("/api/tel/enhance", json={"session_id": "nope"}).status_code == 404
    assert client.get("/api/tel/enhanced/nope").status_code == 404


@needs_model
def test_enhance_after_a_generation_b_call(client):
    """The Call page's button, end to end: send, rebuild, enhance."""
    import io

    source = Image.fromarray(
        (np.add.outer(np.arange(96), np.arange(96)) * 3 % 256).astype("uint8"), "L")
    buf = io.BytesIO()
    source.convert("RGB").save(buf, format="PNG")

    image_id = client.post("/api/tel/stage",
                           files={"file": ("x.png", buf.getvalue(), "image/png")}
                           ).json()["image_id"]
    tx = client.post("/api/tel/send", json={"image_id": image_id, "generation": "B",
                                            "size": 32, "levels": 4}).json()
    client.post("/api/tel/receive", json={"session_id": tx["session_id"],
                                          "reference_id": tx["session_id"]})

    result = client.post("/api/tel/enhance",
                         json={"session_id": tx["session_id"]})
    assert result.status_code == 200, result.text
    body = result.json()
    assert (body["from_size"], body["size"]) == (32, upscaler.SIZE)
    assert body["compare"]["levels_after"] > body["compare"]["levels_before"]
    assert client.get(body["image_url"]).status_code == 200
