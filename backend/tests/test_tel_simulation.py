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


def test_the_real_call_endpoints_are_gone(client):
    for path in ("/api/tel/dial/status", "/api/tel/play/devices"):
        assert client.get(path).status_code in (404, 405)
    assert client.post("/api/tel/upload").status_code in (404, 405)
