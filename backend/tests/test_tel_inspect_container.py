"""/api/tel/inspect against the recordings a real call actually produces.

Two things are being protected here. First, that the existing Send/Receive flow
on the Call page is untouched -- the WAV path has to behave exactly as it did.
Second, that the two reasons a genuine phone recording used to bounce are gone:
it is Matroska rather than WAV, and its lead-in is far longer than the modem's
own three-second preamble search.
"""

import io
import os
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voip import encode, simulate
from voip.audio_io import to_wav_bytes, write_int16_wav

from conftest import needs_ffmpeg

LEAD = 30.0


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def recorded():
    """A Generation C transmission put through a call with a long lead-in."""
    from PIL import Image

    canvas = np.zeros((96, 96, 3), np.uint8)
    canvas[:48] = (220, 60, 60)
    canvas[48:] = (40, 80, 200)
    source = io.BytesIO()
    Image.fromarray(canvas, "RGB").save(source, "PNG")

    prepared = encode.prepare(source=source.getvalue(), generation="C",
                              size=64, quality=50)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=LEAD,
                                    gsm=False, seed=1)
    return received, prepared


def post(client, name, data):
    return client.post("/api/tel/inspect", files={"file": (name, data, "application/octet-stream")})


# --------------------------------------------------------------------------
# The WAV path must not have moved
# --------------------------------------------------------------------------

def test_a_wav_still_inspects(client, recorded):
    audio, prepared = recorded
    response = post(client, "rx.wav", to_wav_bytes(audio))
    assert response.status_code == 200

    body = response.json()
    assert body["found"] is True
    assert body["packet_bytes"] == prepared.manifest["payload"]["packet_bytes"]
    # the fields the Call page already reads
    for key in ("session_id", "found", "packet_bytes", "offset_seconds",
                "opens_without_key", "message", "stats"):
        assert key in body


def test_a_long_lead_in_is_now_found(client, recorded):
    """Before this change fsk.read_header only searched the first 3 seconds,
    so a recording that starts 30 seconds in came back found: false."""
    audio, _ = recorded
    body = post(client, "rx.wav", to_wav_bytes(audio)).json()

    assert body["found"] is True
    assert body["offset_seconds"] > LEAD
    assert body["preamble_score"] > 0.5


def test_noise_is_still_rejected(client):
    noise = np.random.default_rng(2).normal(0.0, 0.1, 25 * 8000)
    body = post(client, "noise.wav", to_wav_bytes(noise)).json()
    assert body["found"] is False
    assert "No call transmission" in body["message"]


def test_inspect_reports_the_new_diagnostics(client, recorded):
    audio, _ = recorded
    body = post(client, "rx.wav", to_wav_bytes(audio)).json()
    assert body["preamble_score"] is not None
    assert body["weak_symbols"] is not None
    assert body["truncated"] is False


def test_a_truncated_recording_is_flagged(client, recorded):
    audio, _ = recorded
    short = audio[:len(audio) - int(10.0 * 8000)]
    body = post(client, "short.wav", to_wav_bytes(short)).json()
    if body["found"]:
        assert body["truncated"] is True
        assert "stops before it ends" in body["message"]


# --------------------------------------------------------------------------
# What the phone actually writes
# --------------------------------------------------------------------------

@needs_ffmpeg
@pytest.mark.parametrize("name,args", [
    ("call.mka", ["-c:a", "libopus", "-b:a", "64k"]),
    ("call.m4a", ["-c:a", "aac", "-b:a", "96k"]),
])
def test_a_phone_recording_inspects(client, recorded, tmp_path, name, args):
    """Linphone's in-call recorder writes Matroska; scipy cannot open it."""
    audio, prepared = recorded
    wav = str(tmp_path / "src.wav")
    write_int16_wav(wav, audio)
    target = str(tmp_path / name)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav, *args, target],
                   check=True, capture_output=True)

    body = post(client, name, open(target, "rb").read()).json()
    assert body["found"] is True
    assert body["packet_bytes"] == prepared.manifest["payload"]["packet_bytes"]


@needs_ffmpeg
def test_a_phone_recording_rebuilds_the_picture(client, recorded, tmp_path):
    """The whole point: drop the .mka into the Receive tab and get the picture."""
    audio, prepared = recorded
    wav = str(tmp_path / "src.wav")
    write_int16_wav(wav, audio)
    mka = str(tmp_path / "call.mka")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav,
                    "-c:a", "libopus", "-b:a", "64k", mka],
                   check=True, capture_output=True)

    inspected = post(client, "call.mka", open(mka, "rb").read()).json()
    assert inspected["found"] and inspected["opens_without_key"]

    rebuilt = client.post("/api/tel/receive",
                          json={"session_id": inspected["session_id"]})
    assert rebuilt.status_code == 200
    body = rebuilt.json()
    assert body["ok"] is True
    assert (body["width"], body["height"]) == (
        prepared.manifest["payload"]["width"], prepared.manifest["payload"]["height"])
    assert body["offset_seconds"] > LEAD, \
        "the reported offset must be where the preamble sits in the original recording"

    image = client.get(body["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"


def test_an_unreadable_file_is_a_400_not_a_500(client):
    response = post(client, "notes.txt", b"this is not audio at all")
    assert response.status_code == 400
    assert "detail" in response.json()


def test_an_empty_upload_is_refused(client):
    assert post(client, "empty.wav", b"").status_code == 400
