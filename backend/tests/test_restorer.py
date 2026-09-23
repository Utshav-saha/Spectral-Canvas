"""The Track 1 restoration model.

Two things are protected here.

**That the model is swappable.** The architecture is read off the checkpoint
rather than hardcoded, so dropping a retrained `restore_best.pt` in place is
all that a new model should need - including one with a different width or a
different number of input planes. `load_state_dict` is strict, so a file that
is not this network fails at load instead of quietly producing worse pictures.

**That the input planes are built the way the model was trained.** Row index
and confidence mask are not decoration: get the polarity backwards and the
output degrades quietly, which is the hardest kind of bug to notice.
"""

import numpy as np
import pytest

from spectral.restore import model as unet
from spectral.restore import restorer

needs_model = pytest.mark.skipif(
    not restorer.available(), reason="no PyTorch or no restore_best.pt")


def _torch_available():
    try:
        unet.torch_module()
        return True
    except RuntimeError:
        return False


needs_torch = pytest.mark.skipif(not _torch_available(), reason="no PyTorch")


# --------------------------------------------------------------------------
# The input planes
# --------------------------------------------------------------------------

def test_the_row_plane_runs_top_to_bottom():
    """Row 0 is the highest frequency. The model has to know which row it is
    standing on, because a convolution looks the same everywhere."""
    plane = restorer.row_index_plane(64, 8)
    assert plane.shape == (64, 8)
    assert plane[0, 0] == 0.0 and plane[-1, 0] == 1.0
    assert np.all(np.diff(plane[:, 0]) > 0)
    # every column carries the same ramp
    assert np.allclose(plane[:, 0], plane[:, -1])


def test_the_mask_marks_dead_rows_as_zero():
    """1 where the row arrived, 0 where a stop-band deleted it."""
    activation = np.full((16, 16, 3), 0.5, dtype=np.float32)
    activation[4:7] = 0.0                       # three annihilated rows
    mask = restorer.confidence_mask(activation)

    assert mask.shape == (16, 16)
    assert mask[4:7].max() == 0.0               # dead
    assert mask[0].min() == 1.0                 # alive
    assert mask[8].min() == 1.0


def test_a_dark_picture_is_not_mistaken_for_dead_rows():
    """A genuinely dark row is data, not a hole. The test is relative to the
    other rows, so an evenly dark picture keeps full confidence."""
    activation = np.full((16, 16, 3), 0.02, dtype=np.float32)
    assert restorer.confidence_mask(activation).min() == 1.0


def test_all_alive_when_there_is_no_signal_at_all():
    assert restorer.confidence_mask(np.zeros((8, 8, 3), np.float32)).min() == 1.0


@needs_model
def test_the_planes_match_what_the_checkpoint_wants():
    _, shape = restorer.load()
    planes = restorer.planes_for(np.zeros((32, 32), np.float32), shape[0])
    assert planes.shape == (32, 32, shape[0])


def test_a_three_channel_model_gets_only_the_picture():
    """A future 3-channel checkpoint must not be handed the extra planes."""
    planes = restorer.planes_for(np.zeros((8, 8), np.float32), 3)
    assert planes.shape == (8, 8, 3)


# --------------------------------------------------------------------------
# Swappability
# --------------------------------------------------------------------------

@needs_torch
def test_the_shape_is_read_off_the_checkpoint():
    """This is what "changing the model file will suffice" rests on."""
    torch = unet.torch_module()
    for in_channels, base in ((5, 32), (3, 16), (7, 8)):
        net = unet.build(torch, in_channels=in_channels, base=base)
        assert unet.shape_of(net.state_dict()) == (in_channels, base, 3)


@needs_torch
def test_a_file_that_is_not_this_network_is_refused(tmp_path):
    torch = unet.torch_module()
    path = str(tmp_path / "wrong.pt")
    torch.save({"something": torch.zeros(3)}, path)
    with pytest.raises(RuntimeError, match="state_dict"):
        unet.load(path)


@needs_torch
def test_odd_sizes_are_padded_and_cropped_back():
    """The U-Net halves three times, so anything not a multiple of 8 has to be
    padded - and the answer still has to come back the original shape."""
    torch = unet.torch_module()
    net = unet.build(torch, in_channels=3, base=4)
    for size in (30, 33, 64):
        residual = unet.predict_residual(net, np.zeros((size, size, 3), np.float32))
        assert residual.shape[:2] == (size, size)


@needs_model
def test_reloads_when_the_file_changes(tmp_path, monkeypatch):
    """Retraining should not need a server restart."""
    import shutil

    copy = str(tmp_path / "restore_best.pt")
    shutil.copyfile(restorer.MODEL_PATH, copy)
    monkeypatch.setattr(restorer, "MODEL_PATH", copy)
    restorer._CACHE.clear()

    first, _ = restorer.load()
    assert restorer.load()[0] is first          # cached while unchanged

    import os

    os.utime(copy, (0, 0))                      # pretend it was rewritten
    assert restorer.load()[0] is not first
    restorer._CACHE.clear()


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------

@needs_model
def test_status_reports_the_checkpoint_it_found():
    state = restorer.status()
    assert state["ready"] is True
    assert state["inputs"] >= 3 and state["outputs"] == 3


def test_status_explains_itself_when_it_cannot_run(monkeypatch):
    monkeypatch.setattr(restorer, "MODEL_PATH", "/nope/missing.pt")
    state = restorer.status()
    assert state["ready"] is False
    assert "missing.pt" in state["message"] or "PyTorch" in state["message"]


@needs_model
@pytest.mark.parametrize("shape", [(64, 64), (64, 64, 3), (32, 32)])
def test_shape_and_range_survive(shape):
    """Grayscale in, grayscale out; colour in, colour out; always 0..1."""
    rng = np.random.default_rng(0)
    activation = rng.random(shape, dtype=np.float32)
    out = restorer.restore(activation)

    assert out.shape == shape
    assert out.min() >= 0.0 and out.max() <= 1.0


@needs_model
def test_it_helps_clipping_which_is_the_case_it_exists_for():
    """Clipping is nonlinear: there is no inverse, so this is the model's job.
    Measured on the real pipeline, not on a synthetic array."""
    import sys

    sys.path.insert(0, "spectral/decoder")
    from PIL import Image

    from spectral.channel.effects import apply_chain
    from spectral.decoder.image_reconstructor import recover_activation
    from spectral.decoder.stft_decoder import decode
    from spectral.encoder.audio_encoder import encode

    grid = (np.add.outer(np.arange(64), np.arange(64)) * 4 % 256).astype("uint8")
    audio, metadata, clean = encode(
        Image.fromarray(grid, "L").convert("RGB"), target_width=64, target_height=64,
        sample_rate=44100, frame_duration=0.1, gray_levels=16, mode="RGB",
        security_enabled=False)
    clean = np.asarray(clean, dtype=np.float32)

    damaged = apply_chain(audio, metadata["sample_rate"],
                          [{"type": "clip", "threshold": 0.3}])
    per = metadata["columns"] * metadata["frame_samples"]
    damaged = np.concatenate([damaged, np.zeros(max(0, 3 * per - len(damaged)))])[:3 * per]
    activation = np.clip(np.stack([
        recover_activation(decode(damaged[i * per:(i + 1) * per], metadata), metadata)
        for i in range(3)], axis=-1), 0, 1).astype(np.float32)

    before = np.abs(activation - clean).mean()
    after = np.abs(restorer.restore(activation) - clean).mean()
    assert after < before, f"model made clipping worse: {before:.4f} -> {after:.4f}"


# --------------------------------------------------------------------------
# Through the bench
# --------------------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_the_catalogue_says_whether_the_model_can_run(client):
    body = client.get("/api/channel/effects").json()
    assert body["model"]["ready"] == restorer.available()


@needs_model
def test_the_bench_returns_all_three_stages(client):
    """Damaged, inverted, model - separately, never merged into one number."""
    import io
    import json

    from PIL import Image

    grid = (np.add.outer(np.arange(96), np.arange(96)) * 3 % 256).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(grid, "L").convert("RGB").save(buf, format="PNG")

    session = client.post(
        "/api/encode", files={"file": ("x.png", buf.getvalue(), "image/png")},
        data={"payload": json.dumps({"source_type": "image",
                                     "security_enabled": False})}).json()

    body = client.post("/api/channel", json={
        "session_id": session["session_id"],
        "effects": [{"type": "clip", "threshold": 0.3}],
        "undo": True, "restore": True}).json()

    assert body["undone"]["skipped"] == ["clip"]        # no inverse for it
    assert body["restored"]["after"] == "inverse"
    assert client.get(body["restored"]["image_url"]).status_code == 200
    # the model is the only stage that can help a clipped picture
    assert body["restored"]["metrics"]["mae"] < body["metrics"]["mae"]
    assert len(body["restored"]["row_error"]) == body["rows"]
