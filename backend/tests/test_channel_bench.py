"""The experiments bench: /api/channel over spectral/channel/effects.py.

The claim being protected is the one the page is built to show. One image row
is one frequency, so an LTI channel's fingerprint is *which rows* it damages,
not how much damage there is overall. A low-pass has to hurt the top rows
(they ride the highest tones) and a high-pass the bottom ones. If that ever
inverts, the encoder's row-to-frequency mapping has been flipped and every
figure in the report is wrong.
"""

import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.services import channel_lab


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def encoded(client):
    """A 64-row grayscale transmission, 1-8 kHz. Row 0 is 8 kHz, row 63 is 1 kHz."""
    canvas = np.zeros((120, 160), np.uint8) + 255
    canvas[20:100, 30:130] = 40
    canvas[40:80, 60:100] = 200
    buf = io.BytesIO()
    Image.fromarray(canvas, "L").save(buf, "PNG")

    payload = {"track": "wav", "source_type": "image", "target_width": 64,
               "target_height": 64, "mode": "L", "gray_levels": 16,
               "security_enabled": False}
    response = client.post("/api/encode",
                           data={"payload": json.dumps(payload)},
                           files={"file": ("in.png", buf.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()


def run(client, encoded, effects):
    return client.post("/api/channel", json={"session_id": encoded["session_id"],
                                             "effects": effects})


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

def test_the_catalogue_is_served(client):
    body = client.get("/api/channel/effects").json()
    ids = {e["id"] for e in body["effects"]}
    assert ids == {"lowpass", "highpass", "bandstop", "echo", "noise", "clip",
                   "resample"}
    assert body["presets"]
    assert body["max_effects"] >= 1


def test_every_effect_actually_runs(client, encoded):
    """The page offers these, so all of them have to survive a round trip."""
    for entry in channel_lab.CATALOGUE:
        step = {"type": entry["id"]}
        for param in entry["params"]:
            step[param["id"]] = param["default"]
        response = run(client, encoded, [step])
        assert response.status_code == 200, f"{entry['id']}: {response.text}"
        assert response.json()["metrics"] is not None


def test_every_preset_actually_runs(client, encoded):
    for preset in channel_lab.PRESETS:
        response = run(client, encoded, preset["effects"])
        assert response.status_code == 200, f"{preset['id']}: {response.text}"


# --------------------------------------------------------------------------
# The mapping the whole page rests on
# --------------------------------------------------------------------------

def _worst_row(body):
    return int(np.argmax(body["row_error"]))


def test_a_low_pass_damages_the_top_rows(client, encoded):
    """Row 0 carries f_max. Cut the high frequencies and the top rows go."""
    body = run(client, encoded, [{"type": "lowpass", "cutoff": 4000}]).json()
    error = np.asarray(body["row_error"])

    assert error[:32].mean() > error[32:].mean() * 3
    assert _worst_row(body) < 32


def test_a_high_pass_damages_the_bottom_rows(client, encoded):
    """The mirror image, and the reason this pair is worth testing together:
    if the row-to-frequency mapping is ever flipped, both of these invert."""
    body = run(client, encoded, [{"type": "highpass", "cutoff": 5000}]).json()
    error = np.asarray(body["row_error"])

    assert error[32:].mean() > error[:32].mean() * 3
    assert _worst_row(body) >= 32


def test_a_band_stop_damages_a_contiguous_band(client, encoded):
    """A stop-band annihilates rows rather than attenuating them, so the
    damage is a block, not a slope."""
    body = run(client, encoded, [{"type": "bandstop", "low": 3000, "high": 5000}]).json()
    error = np.asarray(body["row_error"])

    hit = np.flatnonzero(error > error.max() * 0.4)
    assert len(hit) > 3
    # one run, not scattered noise
    assert hit[-1] - hit[0] < len(hit) * 2.5


def test_clipping_spreads_damage_beyond_any_band(client, encoded):
    """Nonlinear, so it has no h[n] and no pass band. Intermodulation puts
    energy at 2f1-f2, which lands on rows that were never transmitted."""
    body = run(client, encoded, [{"type": "clip", "threshold": 0.15}]).json()
    error = np.asarray(body["row_error"])

    touched = np.flatnonzero(error > error.max() * 0.2)
    assert touched[-1] - touched[0] > len(error) * 0.4


def test_a_harsher_channel_measures_worse(client, encoded):
    gentle = run(client, encoded, [{"type": "lowpass", "cutoff": 7500}]).json()
    harsh = run(client, encoded, [{"type": "lowpass", "cutoff": 2500}]).json()
    assert harsh["metrics"]["mae"] > gentle["metrics"]["mae"]


def test_a_perfect_decode_reports_no_psnr_rather_than_infinity(client, encoded):
    """json.dumps cannot encode inf, and a clean baseline hits exactly that."""
    body = run(client, encoded, [{"type": "noise", "snr_db": 60}]).json()
    assert body["metrics"]["psnr"] is None or body["metrics"]["psnr"] > 0
    assert body["baseline_metrics"]["psnr"] is None


# --------------------------------------------------------------------------
# Refusals, which the frontend shows verbatim
# --------------------------------------------------------------------------

def test_an_empty_chain_is_refused(client, encoded):
    response = run(client, encoded, [])
    assert response.status_code == 400
    assert "at least one" in response.json()["detail"]


def test_an_unknown_effect_is_refused(client, encoded):
    response = run(client, encoded, [{"type": "reverb"}])
    assert response.status_code == 400


def test_a_cutoff_above_the_catalogue_maximum_is_clamped(client, encoded):
    """Overshooting a slider's own bounds is clamped, not refused: 20 kHz is
    still below Nyquist at 44.1 kHz, so there is nothing to complain about."""
    response = run(client, encoded, [{"type": "lowpass", "cutoff": 30000}])
    assert response.status_code == 200
    assert response.json()["chain"][0]["cutoff"] == 20000


def test_a_cutoff_above_nyquist_is_refused():
    """Which only bites below 40 kHz, so it is tested where it can: a
    transmission sent at a lower sample rate."""
    with pytest.raises(ValueError, match="half the sample rate"):
        channel_lab.validate_chain([{"type": "lowpass", "cutoff": 8000}], 12000)


def test_an_inverted_band_is_refused(client, encoded):
    response = run(client, encoded, [{"type": "bandstop", "low": 6000, "high": 2000}])
    assert response.status_code == 400


def test_an_expired_session_is_a_404(client):
    response = client.post("/api/channel", json={"session_id": "nope",
                                                 "effects": [{"type": "clip"}]})
    assert response.status_code == 404


def test_parameters_are_clamped_not_rejected(client, encoded):
    """A slider that overshoots its own bounds should still run."""
    chain = channel_lab.validate_chain([{"type": "clip", "threshold": 99}], 44100)
    assert chain[0]["threshold"] == 1.0


def test_the_degraded_audio_comes_back_as_a_wav(client, encoded):
    body = run(client, encoded, [{"type": "echo", "delay": 0.08, "decay": 0.4}]).json()
    audio = client.get(body["audio_url"])
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    assert client.get(body["image_url"]).status_code == 200
