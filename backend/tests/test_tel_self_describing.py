"""A recording rebuilds on a machine that never saw the send.

Every Call-page transmission opens with a descriptor (spectral/tel/
descriptor.py). These walk the Receive tab's path with no send session at all:
the audio is uploaded bare, as a recording from someone else's call would be,
with seconds of silence either side because Record is pressed whenever.
"""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from scipy.io import wavfile

from conftest import needs_gsm


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def image_id(client):
    grid = np.random.RandomState(3).randint(0, 256, (64, 64, 3)).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(grid, "RGB").save(buf, format="PNG")
    response = client.post("/api/tel/stage",
                           files={"file": ("grid.png", buf.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()["image_id"]


def _send(client, image_id, **settings):
    response = client.post("/api/tel/send", json={"image_id": image_id, **settings})
    assert response.status_code == 200, response.text
    return response.json()


def _as_recording(wav_bytes, lead=7.3, tail=2.0):
    """Bury a transmission in silence, the way a phone recording holds it."""
    rate, pcm = wavfile.read(io.BytesIO(wav_bytes))
    noise = np.random.RandomState(0).normal(0, 30, int((lead + tail) * rate))
    lead_n = int(lead * rate)
    audio = np.concatenate([noise[:lead_n], pcm.astype(float), noise[lead_n:]])
    buf = io.BytesIO()
    wavfile.write(buf, rate, np.clip(audio, -32768, 32767).astype(np.int16))
    return buf.getvalue()


def _upload(client, wav_bytes):
    response = client.post("/api/tel/upload",
                           files={"file": ("recording.wav", wav_bytes, "audio/wav")})
    assert response.status_code == 200, response.text
    return response.json()


def _rebuild(client, session_id, **extra):
    response = client.post("/api/tel/receive",
                           json={"session_id": session_id, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _sent(client, tx):
    return np.asarray(Image.open(io.BytesIO(client.get(tx["sent_url"]).content)))


def _got(client, rebuilt):
    return np.asarray(Image.open(io.BytesIO(client.get(rebuilt["image_url"]).content)))


@pytest.mark.parametrize("settings", [
    {"generation": "B", "size": 32, "levels": 4},
    {"generation": "B", "size": 24, "levels": 16, "colour": True},
    {"generation": "A", "size": 48, "levels": 16},
    {"generation": "A", "size": 24, "levels": 4, "colour": True},
])
def test_a_bare_recording_describes_itself(client, image_id, settings):
    tx = _send(client, image_id, **settings)
    wav = client.get(tx["audio_url"]).content
    found = _upload(client, _as_recording(wav))

    assert found["found"] is True
    assert found["self_described"] is True
    assert found["truncated"] is False
    assert found["generation"] == settings["generation"]
    assert found["rows"] == settings["size"]
    assert found["gray_levels"] == settings["levels"]
    assert found["mode"] == ("RGB" if settings.get("colour") else "L")
    assert abs(found["offset_seconds"] - 7.3) < 0.01

    # no reference_id: nothing but the recording
    rebuilt = _rebuild(client, found["session_id"])
    assert np.array_equal(_got(client, rebuilt), _sent(client, tx))


def test_the_lock_is_announced_and_still_needs_the_pin(client, image_id):
    keys = {"caller": "01712345678", "receiver": "01898765432", "pin": "4321"}
    tx = _send(client, image_id, generation="B", size=16, levels=4,
               security_enabled=True, **keys)
    found = _upload(client, _as_recording(client.get(tx["audio_url"]).content))
    assert found["locked"] is True

    opened = _rebuild(client, found["session_id"], security_enabled=True, **keys)
    assert np.array_equal(_got(client, opened), _sent(client, tx))

    wrong = _rebuild(client, found["session_id"], security_enabled=True,
                     **{**keys, "pin": "9999"})
    assert not np.array_equal(_got(client, wrong), _sent(client, tx))


def test_a_different_send_on_the_page_does_not_override_the_recording(client, image_id):
    """The failure this replaced: a complete recording read against the wrong
    send was reported cut short. What the recording says wins."""
    tx = _send(client, image_id, generation="B", size=16, levels=4)
    other = _send(client, image_id, generation="B", size=48, levels=16)
    found = _upload(client, _as_recording(client.get(tx["audio_url"]).content))

    inspected = client.post("/api/tel/inspect", json={
        "session_id": found["session_id"], "reference_id": other["session_id"]}).json()
    assert inspected["truncated"] is False
    assert inspected["rows"] == 16

    rebuilt = _rebuild(client, found["session_id"], reference_id=other["session_id"])
    assert np.array_equal(_got(client, rebuilt), _sent(client, tx))
    assert "match" not in rebuilt        # different picture: nothing to score

    scored = _rebuild(client, found["session_id"], reference_id=tx["session_id"])
    assert scored["match"]["identical"] is True


def test_a_really_short_recording_is_still_reported_short(client, image_id):
    tx = _send(client, image_id, generation="B", size=32, levels=4)
    rate, pcm = wavfile.read(io.BytesIO(client.get(tx["audio_url"]).content))
    buf = io.BytesIO()
    wavfile.write(buf, rate, pcm[: len(pcm) * 2 // 3])
    found = _upload(client, _as_recording(buf.getvalue(), tail=0.0))
    assert found["self_described"] is True
    assert found["truncated"] is True


@needs_gsm
@pytest.mark.parametrize("generation,size,levels", [("B", 32, 4), ("A", 24, 4)])
def test_the_descriptor_survives_the_simulated_call(client, image_id,
                                                    generation, size, levels):
    tx = _send(client, image_id, generation=generation, size=size, levels=levels)
    rx = client.post("/api/tel/call", json={"session_id": tx["session_id"],
                                            "loss": 0.02, "seed": 4}).json()
    found = _upload(client, _as_recording(client.get(rx["audio_url"]).content))
    assert found["self_described"] is True
    assert (found["generation"], found["rows"], found["gray_levels"]) == (generation, size, levels)

    rebuilt = _rebuild(client, found["session_id"], reference_id=tx["session_id"])
    if generation == "B":
        assert rebuilt["match"]["identical"] is True
    else:
        # lossy by design; measured 0.60-0.72 over seeds on this noise image,
        # against 0.40-0.60 before the announcement existed
        assert rebuilt["match"]["exact_fraction"] > 0.4
