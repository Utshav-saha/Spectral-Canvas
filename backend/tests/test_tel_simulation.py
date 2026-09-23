"""/api/tel end to end: the Call page's whole path, with the call simulated.

stage -> send -> (call) -> receive, for both generations. Straight from tx.wav
both are exact; through the simulated GSM call Generation B stays exact and
Generation A is damaged, which is the point of it.
"""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import needs_gsm


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def image_id(client):
    grid = (np.add.outer(np.arange(64), np.arange(64)) * 4 % 256).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(grid, "L").save(buf, format="PNG")
    response = client.post("/api/tel/stage",
                           files={"file": ("grid.png", buf.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()["image_id"]


def _send(client, image_id, generation, size, levels):
    response = client.post("/api/tel/send", json={
        "image_id": image_id, "generation": generation, "size": size,
        "levels": levels})
    assert response.status_code == 200, response.text
    return response.json()


def _receive(client, session_id, reference_id):
    response = client.post("/api/tel/receive", json={
        "session_id": session_id, "reference_id": reference_id})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("generation,size,levels", [("A", 24, 4), ("B", 16, 4)])
def test_straight_from_tx_is_exact(client, image_id, generation, size, levels):
    tx = _send(client, image_id, generation, size, levels)
    rebuilt = _receive(client, tx["session_id"], tx["session_id"])
    assert rebuilt["match"]["identical"] is True
    assert client.get(rebuilt["image_url"]).status_code == 200


@needs_gsm
def test_generation_b_survives_the_simulated_call(client, image_id):
    tx = _send(client, image_id, "B", 16, 4)
    rx = client.post("/api/tel/call", json={"session_id": tx["session_id"],
                                            "loss": 0.0, "seed": 1}).json()
    rebuilt = _receive(client, rx["session_id"], tx["session_id"])
    assert rebuilt["match"]["exact_fraction"] == 1.0


@needs_gsm
def test_generation_a_is_damaged_by_the_simulated_call(client, image_id):
    tx = _send(client, image_id, "A", 24, 16)
    rx = client.post("/api/tel/call", json={"session_id": tx["session_id"],
                                            "seed": 1}).json()
    rebuilt = _receive(client, rx["session_id"], tx["session_id"])
    assert rebuilt["match"]["exact_fraction"] < 1.0


def test_the_real_call_endpoints_are_served(client):
    """Both routes answer whether or not this machine can actually dial: they
    report what is missing rather than 404ing, so the page can say why."""
    for path in ("/api/tel/dial/status", "/api/tel/play/devices"):
        body = client.get(path)
        assert body.status_code == 200
        assert "message" in body.json() or body.json().get("ready") is not None

    # no file attached is a validation error, not a missing route
    assert client.post("/api/tel/upload").status_code == 422


# --------------------------------------------------------------------------
# Airtime limits
# --------------------------------------------------------------------------

def test_generation_b_has_no_airtime_cap(client):
    """B is the exact one, and the only reason to choose it is when
    correctness matters more than the wait. Capping it would refuse the whole
    point of it, so the biggest thing it can carry has to plan cleanly."""
    from app.services import tel_pipeline as tel

    plan = client.get("/api/tel/plan",
                      params={"generation": "B", "size": 64, "levels": 16,
                              "colour": True}).json()

    assert plan["seconds"] > tel.MAX_SECONDS      # well past A's limit
    assert plan["max_seconds"] is None            # and not refused
    assert plan["long"] is True                   # but the page is warned


def test_generation_a_keeps_its_cap():
    """A is the lossy one: a picture nobody would sit through is still worth
    refusing."""
    from app.services import tel_pipeline as tel

    assert tel.GENERATIONS["A"]["max_seconds"] == tel.MAX_SECONDS
    assert tel.GENERATIONS["B"]["max_seconds"] is None


@pytest.mark.parametrize("size", [48, 64])
def test_the_bigger_generation_b_grids_round_trip(client, image_id, size):
    """64x64 is new, and the modem sends no length header on this path, so the
    only thing that could break is the geometry coming back from the session."""
    tx = _send(client, image_id, "B", size, 4)
    rebuilt = _receive(client, tx["session_id"], tx["session_id"])

    assert (rebuilt["rows"], rebuilt["columns"]) == (size, size)
    assert rebuilt["match"]["identical"] is True


def test_a_call_recording_on_the_wrong_page_is_pointed_at_the_right_one(client):
    """A recording of a real call has no SpCv header and never can.

    It is sound captured off a voice line, not the WAV this server wrote, so
    /api/inspect cannot rebuild it - its geometry lives in the Call page's send
    session. The page is called "Receive", so that is where people take it
    first; saying only "no header" reads as "your recording is broken" and
    sends them off to re-record it.
    """
    from app.services import tel_pipeline as tel

    audio = tel.to_wav_bytes(__import__("numpy").zeros(8000))
    response = client.post("/api/inspect",
                           files={"file": ("call.wav", audio, "audio/wav")})
    assert response.status_code == 200
    assert "Call page" in response.json()["message"]


# --------------------------------------------------------------------------
# Whether the recording could carry a picture at all
# --------------------------------------------------------------------------

def _genb_recording(seconds=8.0, tilt_db=0.0, clip=None):
    """A Generation B transmission, optionally through a channel that tilts the
    spectrum the way a phone's voice processing does."""
    import numpy as np
    from PIL import Image
    from spectral.tel import call_track

    grid = (np.add.outer(np.arange(32), np.arange(32)) * 7 % 256).astype("uint8")
    audio, meta, _ = call_track.encode(Image.fromarray(grid, "L"),
                                       target_width=16, target_height=16,
                                       gray_levels=4, mode="L", generation="B")
    if tilt_db:
        spec = np.fft.rfft(audio)
        freqs = np.fft.rfftfreq(len(audio), 1 / 8000)
        # straight line in dB across the modem's band: loud at the bottom,
        # buried at the top, which is what killed the real call
        slope = np.clip((freqs - 700) / (3200 - 700), 0, 1)
        spec *= 10 ** (-tilt_db * slope / 20)
        audio = np.fft.irfft(spec, n=len(audio))
    if clip:
        audio = np.clip(audio / max(np.max(np.abs(audio)), 1e-9) * clip, -1.0, 1.0)
    return audio, meta


def test_a_mostly_blank_picture_is_not_mistaken_for_a_dead_channel():
    """The false alarm that told a user their working recording was ruined.

    A mostly-white picture sends the lowest tone over and over, so one bin
    legitimately carries a hundred times the median. Imbalance alone therefore
    means nothing; the margin, which does not depend on the picture, is what
    says whether the symbols were decided cleanly.
    """
    import numpy as np
    from PIL import Image
    from app.services import tel_pipeline as tel
    from spectral.tel import call_track

    blank = np.full((64, 64), 255, dtype="uint8")
    blank[20:28, 20:44] = 0
    audio, meta, _ = call_track.encode(Image.fromarray(blank, "L"),
                                       target_width=64, target_height=64,
                                       gray_levels=4, mode="L", generation="B")
    health = tel.signal_health(audio, meta, 0)

    assert health["bin_imbalance"] > tel.BIN_IMBALANCE      # looks alarming
    assert health["median_margin"] > tel.WEAK_MEDIAN_MARGIN  # but decided cleanly
    assert health["warning"] is None, health


def test_a_healthy_recording_raises_no_warning():
    """A flat-area picture sends the same symbol over and over, so a high share
    of one tone is normal and must not be mistaken for a dead channel."""
    from app.services import tel_pipeline as tel

    audio, meta = _genb_recording()
    health = tel.signal_health(audio, meta, 0)
    assert health["warning"] is None, health
    assert health["bin_imbalance"] < tel.BIN_IMBALANCE


def test_a_recording_with_only_one_tone_left_says_so():
    """The real failure: sync is perfect, the picture is not rebuildable, and
    the only visible symptom used to be a wrong picture blamed on the PIN."""
    from app.services import tel_pipeline as tel

    # 85 dB of tilt to reach both the imbalance AND the weak margin the real
    # destroyed call showed (57x, margin 48);
    # a pure tilt is gentler than the real damage, which also had a tone
    # sitting on top of the lowest bin.
    audio, meta = _genb_recording(tilt_db=85.0)
    health = tel.signal_health(audio, meta, 0)
    assert health["bin_imbalance"] > tel.BIN_IMBALANCE
    assert health["median_margin"] < tel.WEAK_MEDIAN_MARGIN
    assert "read wrong" in health["warning"]
    assert "noise suppression" in health["warning"]


def test_a_clipped_recording_says_so():
    from app.services import tel_pipeline as tel

    audio, meta = _genb_recording(clip=3.0)
    health = tel.signal_health(audio, meta, 0)
    assert health["clipped_fraction"] > tel.CLIPPED_FRACTION
    assert health["warning"]


def test_the_real_call_defaults_to_the_codec_the_project_is_about():
    """Left unrestricted the call negotiates whatever both ends prefer, and a
    transparent codec makes a clean decode prove nothing. GSM 06.10 is the one
    channel_sim models, so a real call and the simulated one are comparable.
    Both directions have to agree: which end dialled must not change the
    channel."""
    from app.api.tel_routes import AnswerBody, DialBody

    assert DialBody(session_id="x", target="sip:a@b").codec == "GSM"
    assert AnswerBody(session_id="x").codec == "GSM"


def test_inspect_reports_whether_the_transmission_was_locked(client, image_id):
    """The card used to read a key no endpoint has ever returned, so every
    recording came up "Locked" - including unlocked ones."""
    for locked in (False, True):
        body = {"image_id": image_id, "generation": "B", "size": 16,
                "levels": 4, "security_enabled": locked}
        if locked:
            body |= {"caller": "01234567890", "receiver": "09876543210",
                     "pin": "12345"}
        tx = client.post("/api/tel/send", json=body).json()
        wav = client.get(f"/api/tel/audio/{tx['session_id']}").content
        up = client.post("/api/tel/upload",
                         files={"file": ("r.wav", wav, "audio/wav")}).json()
        seen = client.post("/api/tel/inspect",
                           json={"session_id": up["session_id"],
                                 "reference_id": tx["session_id"]}).json()
        assert seen["locked"] is locked
