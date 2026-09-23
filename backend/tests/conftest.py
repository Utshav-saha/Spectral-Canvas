"""Shared fixtures and skip markers for the backend test suite.

`test_roundtrip.py` is deliberately not collected: it is a `__main__` script
that prints a table, not a pytest module, so collecting it would find no tests.
Run it directly instead, from `backend/`:

    python -m tests.test_roundtrip
"""

import shutil

import pytest

collect_ignore = ["test_roundtrip.py"]


def _gsm_available():
    try:
        from app.services.tel_pipeline import gsm_available
        return gsm_available()
    except Exception:
        return False


def _sdk_available():
    try:
        from voip.call import sdk
        return sdk.available()
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
needs_gsm = pytest.mark.skipif(
    not _gsm_available(), reason="no GSM 06.10 codec (brew install libgsm)")
needs_pjsua = pytest.mark.skipif(
    shutil.which("pjsua") is None, reason="pjsua not installed")
needs_sdk = pytest.mark.skipif(
    not _sdk_available(), reason="liblinphone Python wrapper not installed")


@pytest.fixture
def synthetic_image(tmp_path):
    """A shape with hard edges and flat areas: obvious when it comes back wrong."""
    import numpy as np
    from PIL import Image

    size = 128
    canvas = np.full((size, size, 3), 240, dtype=np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    circle = (yy - 48) ** 2 + (xx - 48) ** 2 < 30 ** 2
    canvas[circle] = (200, 40, 40)
    canvas[80:115, 70:115] = (30, 60, 180)
    canvas[abs(yy - xx) < 3] = (20, 20, 20)

    path = tmp_path / "synthetic.png"
    Image.fromarray(canvas, "RGB").save(path)
    return str(path)


@pytest.fixture
def genb_audio():
    """A Generation B transmission and the activation that produced it."""
    import numpy as np

    from voip import _tel, framing

    fsk = _tel.fsk()
    image_fsk = _tel.image_fsk()
    rng = np.random.default_rng(11)
    activation = np.round(rng.random((16, 16)) * 3) / 3
    bits = image_fsk.activation_to_bits(activation, 4)
    # resync=0 mirrors voip/encode.py: this package reads the wire with its
    # own voip.framing, which counts symbols itself and knows nothing about
    # fsk_codec's re-sync markers. The markers belong to the app's Track 2.
    audio, info = fsk.modulate(bits, fec=True, resync=0,
                               header=framing.build_genb_header(16, 16, 4))
    return audio, activation, info
