"""Pictures, text and the PIN lock, through the Reed-Solomon packet format."""

import numpy as np
import pytest

from voip import _tel, payload
from voip.config import TEXT_MAGIC, VoipError

OPEN = (None, None, None)
LOCKED = ("01712345678", "01787654321", "4321")


# --------------------------------------------------------------------------
# Sniffing
# --------------------------------------------------------------------------

def test_sniff_recognises_text_and_webp(synthetic_image):
    from PIL import Image

    _, webp = _tel.image_webp().compress(Image.open(synthetic_image), 64, 50)
    assert payload.sniff(webp) == "image"
    assert payload.sniff(TEXT_MAGIC + b"hello") == "text"
    assert payload.sniff(b"\x00\x01\x02\x03nonsense") == "unknown"
    assert payload.sniff(b"") == "unknown"


def test_a_webp_is_never_mistaken_for_text(synthetic_image):
    """Images carry no prefix, so the format stays backward compatible."""
    from PIL import Image

    _, webp = _tel.image_webp().compress(Image.open(synthetic_image), 96, 50)
    assert webp[:4] != TEXT_MAGIC
    assert payload.open_payload(webp)["kind"] == "image"


def test_unknown_payload_is_refused_with_an_explanation():
    with pytest.raises(VoipError, match="neither a WebP"):
        payload.open_payload(b"\x91\x22\x00\xff" * 40)


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "hello",
    "Spectral Canvas over a real call",
    "multibyte: ünïcödé, 日本語, emoji ✓🎵",
    "x" * 500,
    "line one\nline two\ttabbed",
])
def test_text_round_trips_exactly(message):
    key = payload.transmission_key(*OPEN)
    packet, meta = payload.build_text_packet(message, key)
    assert meta["kind"] == "text"

    raw, repaired = payload.unprotect(packet, key)
    assert repaired == 0
    opened = payload.open_payload(raw)
    assert opened["kind"] == "text"
    assert opened["text"] == message


def test_empty_text_is_refused():
    with pytest.raises(VoipError, match="no text"):
        payload.build_text_packet("   ", payload.transmission_key(*OPEN))


# --------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------

def test_image_packet_round_trips(synthetic_image):
    from PIL import Image

    key = payload.transmission_key(*OPEN)
    packet, on_wire, meta = payload.build_image_packet(
        Image.open(synthetic_image), 96, 50, key)

    assert meta["kind"] == "image"
    assert meta["packet_bytes"] > meta["webp_bytes"]

    raw, _ = payload.unprotect(packet, key)
    opened = payload.open_payload(raw)
    assert np.array_equal(np.asarray(opened["image"]), np.asarray(on_wire))


# --------------------------------------------------------------------------
# The lock
# --------------------------------------------------------------------------

def test_the_right_key_opens_it(synthetic_image):
    from PIL import Image

    key = payload.transmission_key(*LOCKED)
    packet, on_wire, _ = payload.build_image_packet(
        Image.open(synthetic_image), 64, 50, key)
    raw, _ = payload.unprotect(packet, key)
    assert np.array_equal(
        np.asarray(payload.open_payload(raw)["image"]), np.asarray(on_wire))


def test_the_wrong_pin_gives_static_not_an_error(synthetic_image):
    """A wrong PIN is meant to decode to noise. That is the behaviour, not a bug."""
    from PIL import Image

    packet, _, _ = payload.build_image_packet(
        Image.open(synthetic_image), 64, 50, payload.transmission_key(*LOCKED))

    wrong = payload.transmission_key("01712345678", "01787654321", "9999")
    with pytest.raises(Exception):
        payload.unprotect(packet, wrong)

    static = payload.static_image(packet, wrong)
    assert static.mode == "RGB" and static.width > 0


def test_an_open_transmission_still_gets_shuffled():
    """The shuffle doubles as a byte interleaver, so it runs even with no PIN."""
    webp = _tel.image_webp()
    plain = bytes(range(256)) * 4
    key = payload.transmission_key(*OPEN)
    assert webp.protect(plain, key) != webp.protect(plain, key, 0)[:len(plain)]
    assert payload.unprotect(webp.protect(plain, key), key)[0] == plain


def test_open_and_locked_keys_differ():
    assert payload.transmission_key(*OPEN) != payload.transmission_key(*LOCKED)


# --------------------------------------------------------------------------
# Reed-Solomon accounting
# --------------------------------------------------------------------------

def test_rs_blocks_reports_the_repair_budget():
    info = payload.rs_blocks(942, 32)
    assert info["rs_parity"] == 32
    assert info["rs_limit_per_block"] == 16
    assert info["blocks"] >= 1
    assert info["rs_limit_total"] == info["blocks"] * 16


# --------------------------------------------------------------------------
# Generation B
# --------------------------------------------------------------------------

def test_genb_bits_round_trip():
    activation = np.round(np.random.default_rng(4).random((16, 16)) * 3) / 3
    bits, meta = payload.build_genb_bits(activation, 4)
    assert meta["payload_bits"] == 16 * 16 * 2
    assert np.array_equal(
        payload.genb_bits_to_activation(bits, 16, 16, 4), activation)


def test_genb_refuses_colour():
    with pytest.raises(VoipError, match="grayscale only"):
        payload.build_genb_bits(np.zeros((8, 8, 3)), 4)
