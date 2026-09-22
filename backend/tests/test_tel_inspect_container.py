"""/api/tel against the recordings a real call actually produces.

Three things are protected here. That a recording made on a phone gets in at
all -- it is Matroska rather than WAV, and its lead-in is far longer than the
modem's own three-second preamble search. That both shipping generations make
the round trip through the HTTP layer. And that a recording is matched to the
transmission it came from, which is now required rather than optional:
Generation A carries no header on the wire and Generation B's header describes
the grid but not which send it belongs to, so the geometry comes from the send
session either way.
"""

import io
import os
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from voip import simulate
from voip.audio_io import to_wav_bytes

from conftest import needs_ffmpeg

LEAD = 30.0


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def picture():
    canvas = np.zeros((96, 96, 3), np.uint8)
    canvas[:48] = (220, 60, 60)
    canvas[48:] = (40, 80, 200)
    buf = io.BytesIO()
    Image.fromarray(canvas, "RGB").save(buf, "PNG")
    return buf.getvalue()


def staged(client, picture):
    response = client.post("/api/tel/stage",
                           files={"file": ("in.png", picture, "image/png")})
    assert response.status_code == 200, response.text
    return response.json()["image_id"]


def sent(client, picture, generation="A", size=24, levels=4):
    """Encode through the API and put the audio through a long-lead-in call."""
    response = client.post("/api/tel/send", json={
        "image_id": staged(client, picture), "generation": generation,
        "size": size, "levels": levels, "colour": False,
    })
    assert response.status_code == 200, response.text
    body = response.json()

    audio = client.get(body["audio_url"]).content
    from voip.audio_io import load_audio_bytes  # noqa: F401  (import check)
    from app.services.tel_pipeline import read_any_wav
    samples, _ = read_any_wav(audio)
    received, _ = simulate.simulate(samples, lead_seconds=LEAD, gsm=False, seed=1)
    return body, received


def upload(client, name, data):
    return client.post("/api/tel/upload",
                       files={"file": (name, data, "application/octet-stream")})


# --------------------------------------------------------------------------
# Getting a recording in
# --------------------------------------------------------------------------

def test_a_wav_uploads(client, picture):
    _, received = sent(client, picture)
    response = upload(client, "rx.wav", to_wav_bytes(received))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"]
    # nothing to match it against yet, so it cannot claim to have found one
    assert body["found"] is False
    assert "transmission" in body["message"]


def test_an_unreadable_file_is_a_400_not_a_500(client):
    response = upload(client, "rx.wav", b"this is not audio")
    assert response.status_code == 400
    assert "detail" in response.json()


def test_an_empty_upload_is_refused(client):
    response = upload(client, "rx.wav", b"")
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Matching it to the send it came from
# --------------------------------------------------------------------------

@pytest.mark.parametrize("generation,size", [("A", 24), ("B", 16)])
def test_a_long_lead_in_is_found_for_both_generations(client, picture,
                                                      generation, size):
    """30 seconds of lead-in is the case the modem's own 3-second preamble
    search cannot reach, and the reason voip.sync exists."""
    body, received = sent(client, picture, generation, size)
    session = upload(client, "rx.wav", to_wav_bytes(received)).json()["session_id"]

    response = client.post("/api/tel/inspect", json={
        "session_id": session, "reference_id": body["session_id"]})
    assert response.status_code == 200, response.text
    found = response.json()

    assert found["found"] is True
    assert found["generation"] == generation
    assert found["truncated"] is False
    assert LEAD <= found["offset_seconds"] <= LEAD + 4.0


@pytest.mark.parametrize("generation,size", [("A", 24), ("B", 16)])
def test_a_recording_rebuilds_the_picture(client, picture, generation, size):
    body, received = sent(client, picture, generation, size)
    session = upload(client, "rx.wav", to_wav_bytes(received)).json()["session_id"]

    response = client.post("/api/tel/receive", json={
        "session_id": session, "reference_id": body["session_id"]})
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["ok"] is True
    assert result["generation"] == generation
    assert (result["rows"], result["columns"]) == (size, size)
    assert client.get(result["image_url"]).status_code == 200

    # No codec in this rehearsal, so both generations should come back clean.
    # What GSM does to Generation A is measured in test_voip_channel.py, which
    # is the only place a real codec is in the path.
    assert result["match"]["exact_fraction"] > 0.9


def test_rebuilding_without_a_reference_is_refused(client, picture):
    _, received = sent(client, picture)
    session = upload(client, "rx.wav", to_wav_bytes(received)).json()["session_id"]

    response = client.post("/api/tel/receive", json={"session_id": session})
    assert response.status_code == 400
    assert "recording of" in response.json()["detail"]


def test_noise_finds_nothing(client, picture):
    body, _ = sent(client, picture, "B", 16)
    noise = np.random.default_rng(5).normal(0.0, 0.05, 20 * 8000)
    session = upload(client, "rx.wav", to_wav_bytes(noise)).json()["session_id"]

    found = client.post("/api/tel/inspect", json={
        "session_id": session, "reference_id": body["session_id"]}).json()
    assert found["found"] is False


# --------------------------------------------------------------------------
# The container a phone actually hands you
# --------------------------------------------------------------------------

def _transcode(source, target, *args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", source,
                    *args, target], check=True)


@needs_ffmpeg
@pytest.mark.parametrize("name,args", [
    ("call.mka", ("-c:a", "libopus", "-b:a", "64k")),
    ("call.m4a", ("-c:a", "aac", "-b:a", "96k")),
])
def test_a_phone_recording_inspects(client, picture, tmp_path, name, args):
    """Linphone's in-call recorder writes Matroska, which scipy cannot open."""
    body, received = sent(client, picture, "B", 16)

    wav = str(tmp_path / "rx.wav")
    from voip.audio_io import write_int16_wav
    write_int16_wav(wav, received)
    target = str(tmp_path / name)
    _transcode(wav, target, *args)

    with open(target, "rb") as handle:
        session = upload(client, name, handle.read())
    assert session.status_code == 200, session.text

    found = client.post("/api/tel/inspect", json={
        "session_id": session.json()["session_id"],
        "reference_id": body["session_id"]}).json()
    assert found["found"] is True


@needs_ffmpeg
def test_a_phone_recording_rebuilds_the_picture(client, picture, tmp_path):
    body, received = sent(client, picture, "B", 16)

    wav = str(tmp_path / "rx.wav")
    from voip.audio_io import write_int16_wav
    write_int16_wav(wav, received)
    mka = str(tmp_path / "call.mka")
    _transcode(wav, mka, "-c:a", "libopus", "-b:a", "64k")

    with open(mka, "rb") as handle:
        session = upload(client, "call.mka", handle.read()).json()["session_id"]

    result = client.post("/api/tel/receive", json={
        "session_id": session, "reference_id": body["session_id"]}).json()
    assert result["ok"] is True
    assert os.path.basename(result["image_url"])
