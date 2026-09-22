"""What the modem carries, now that Generation C is cut.

Generation A goes through spectral/tel/call_track.py; Generation B is raw
quantized pixels. Both share one lock: a permutation of the activation matrix,
applied upstream of the modem so it survives a call.
"""

import numpy as np
import pytest

from voip import payload
from voip.config import VoipError

CALLER, RECEIVER, PIN = "12345678901", "10987654321", "1234"


@pytest.fixture
def activation():
    """A 16x16 gradient quantized to 4 levels, as either generation sends it."""
    grid = np.add.outer(np.linspace(0, 1, 16), np.linspace(0, 1, 16)) / 2
    return np.round(grid * 3) / 3


# --------------------------------------------------------------------------
# Generation B: bits
# --------------------------------------------------------------------------

def test_genb_bits_round_trip(activation):
    bits, meta = payload.build_genb_bits(activation, 4)
    assert meta["rows"] == 16 and meta["cols"] == 16
    assert len(bits) == 16 * 16 * 2          # 4 levels = 2 bits a pixel

    back = payload.genb_bits_to_activation(bits, 16, 16, 4)
    assert np.allclose(back, activation)


def test_genb_refuses_colour(activation):
    colour = np.stack([activation] * 3, axis=-1)
    with pytest.raises(VoipError, match="grayscale only"):
        payload.build_genb_bits(colour, 4)


def test_genb_bit_count_tracks_the_level_count(activation):
    two, _ = payload.build_genb_bits(np.round(activation), 2)
    four, _ = payload.build_genb_bits(activation, 4)
    assert len(four) == 2 * len(two)


# --------------------------------------------------------------------------
# Generation A: the call_track bridge
# --------------------------------------------------------------------------

def test_gen_a_loads_and_offers_both_generations():
    call_track = payload.gen_a()
    assert call_track.GENERATIONS == ("A", "B")
    assert call_track.SAMPLE_RATE == 8000


def test_gen_a_budget_is_cheaper_per_pixel_than_gen_b():
    """The whole reason Generation A is worth keeping: one frame per column,
    whatever the content, against Generation B's serial symbol stream."""
    call_track = payload.gen_a()
    a = call_track.budget_seconds(24, 24, 4, "L", "A")
    b = call_track.budget_seconds(24, 24, 4, "L", "B")
    assert a < b / 4


def test_gen_a_refuses_an_unknown_generation():
    with pytest.raises(ValueError, match="Generation must be"):
        payload.gen_a().budget_seconds(24, 24, 4, "L", "C")


# --------------------------------------------------------------------------
# The lock, which both generations share
# --------------------------------------------------------------------------

def test_the_right_key_puts_every_pixel_back(activation):
    permute = payload.gen_a()._permute
    scrambled = permute(activation, CALLER, RECEIVER, PIN, forward=True)
    restored = permute(scrambled, CALLER, RECEIVER, PIN, forward=False)
    assert np.allclose(restored, activation)


def test_scrambling_actually_moves_something(activation):
    scrambled = payload.gen_a()._permute(activation, CALLER, RECEIVER, PIN,
                                         forward=True)
    assert not np.allclose(scrambled, activation)


def test_the_wrong_pin_gives_static_not_an_error(activation):
    """The behaviour the whole project is built around: a wrong PIN decodes,
    it just decodes to noise."""
    permute = payload.gen_a()._permute
    scrambled = permute(activation, CALLER, RECEIVER, PIN, forward=True)
    wrong = permute(scrambled, CALLER, RECEIVER, "9999", forward=False)

    assert wrong.shape == activation.shape          # no exception, real output
    assert not np.allclose(wrong, activation)
    # and it is not merely off by a little
    assert np.mean(np.abs(wrong - activation)) > 0.1


def test_colour_is_permuted_channel_by_channel(activation):
    permute = payload.gen_a()._permute
    colour = np.stack([activation, activation * 0.5, activation * 0.25], axis=-1)
    scrambled = permute(colour, CALLER, RECEIVER, PIN, forward=True)
    assert scrambled.shape == colour.shape
    assert np.allclose(permute(scrambled, CALLER, RECEIVER, PIN, forward=False),
                       colour)


def test_a_missing_credential_is_refused(activation):
    with pytest.raises(ValueError, match="required"):
        payload.gen_a()._permute(activation, CALLER, None, PIN, forward=True)
