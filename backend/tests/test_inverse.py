"""The LTI inverse: what division by H(f) gets back, and what it does not.

Measured end to end - encode a picture, damage the audio, invert it, decode -
because an inverse that looks right on a sine wave and fails on the real
pipeline is worth nothing. The numbers in the assertions are deliberately
loose; what is being pinned is the *ordering*, which is the argument:

    LTI effects          invert, and echo inverts almost exactly
    band-stop            improves but stays broken
    clipping, noise      are not touched at all
"""

import sys

import numpy as np
import pytest
from PIL import Image

from spectral.channel import effects, inverse

# the decoder uses bare sibling imports
sys.path.insert(0, "spectral/decoder")

SAMPLE_RATE = 44100


@pytest.fixture(scope="module")
def transmission():
    """One encoded picture, reused: encoding is the slow part."""
    from spectral.encoder.audio_encoder import encode

    grid = (np.add.outer(np.arange(64), np.arange(64)) * 4 % 256).astype("uint8")
    image = Image.fromarray(grid, "L").convert("RGB")
    audio, metadata, clean = encode(
        image, target_width=64, target_height=64, sample_rate=SAMPLE_RATE,
        frame_duration=0.1, gray_levels=16, mode="RGB", security_enabled=False)
    return audio, metadata, np.asarray(clean, dtype=np.float32)


def activation_of(audio, metadata):
    from spectral.decoder.stft_decoder import decode
    from spectral.decoder.image_reconstructor import recover_activation

    per_channel = metadata["columns"] * metadata["frame_samples"]
    needed = 3 * per_channel
    audio = np.concatenate([audio, np.zeros(max(0, needed - len(audio)))])[:needed]
    return np.stack([
        recover_activation(decode(audio[i * per_channel:(i + 1) * per_channel],
                                  metadata), metadata)
        for i in range(3)], axis=-1).astype(np.float32)


def error_of(audio, metadata, clean):
    return float(np.abs(activation_of(audio, metadata) - clean).mean())


def run(transmission, chain):
    """(error while damaged, error after the inverse, what it did)"""
    audio, metadata, clean = transmission
    damaged = effects.apply_chain(audio, SAMPLE_RATE, chain)
    repaired, what = inverse.undo_chain(damaged, SAMPLE_RATE, chain)
    return error_of(damaged, metadata, clean), error_of(repaired, metadata, clean), what


# --------------------------------------------------------------------------
# The ones with an inverse
# --------------------------------------------------------------------------

def test_an_echo_is_undone_almost_exactly(transmission):
    """The strongest case in the project: an echo is a filter, and running it
    backwards is a two-line IIR."""
    before, after, what = run(transmission, [{"type": "echo", "delay": 0.08,
                                              "decay": 0.4}])
    assert what["undone"] == ["echo"]
    assert after < before / 4
    assert after < 0.05


def test_a_low_pass_is_mostly_undone(transmission):
    before, after, what = run(transmission, [{"type": "lowpass", "cutoff": 5000}])
    assert what["undone"] == ["lowpass"]
    assert after < before / 2


def test_a_high_pass_is_mostly_undone(transmission):
    before, after, _ = run(transmission, [{"type": "highpass", "cutoff": 2000}])
    assert after < before


def test_a_chain_is_undone_in_reverse(transmission):
    """Last on, first off. Undoing them in the order they were applied would
    try to divide the echo out of audio that is still filtered."""
    chain = [{"type": "echo", "delay": 0.05, "decay": 0.35},
             {"type": "lowpass", "cutoff": 6000}]
    before, after, what = run(transmission, chain)
    assert what["undone"] == ["lowpass", "echo"]     # reverse of the chain
    assert after < before / 2


def test_an_unknown_gain_is_just_a_number(transmission):
    audio, metadata, clean = transmission
    quiet = np.asarray(audio) * 0.15
    before = error_of(quiet, metadata, clean)
    after = error_of(inverse.undo_gain(quiet), metadata, clean)
    assert before > 0.3          # amplitude carries the pixel, so this is bad
    assert after < 0.05          # and scaling back fixes it


# --------------------------------------------------------------------------
# The ones without
# --------------------------------------------------------------------------

def test_clipping_is_left_alone(transmission):
    """Not LTI: no h[n], no H(f), nothing to divide by. The inverse must not
    pretend otherwise - the audio comes back untouched."""
    before, after, what = run(transmission, [{"type": "clip", "threshold": 0.3}])
    assert what == {"undone": [], "attempted": [], "skipped": ["clip"]}
    assert after == pytest.approx(before, abs=1e-9)


def test_noise_is_left_alone(transmission):
    _, _, what = run(transmission, [{"type": "noise", "snr_db": 20}])
    assert what["skipped"] == ["noise"]


def test_a_band_stop_improves_but_stays_broken(transmission):
    """The shoulders were attenuated and come back; the rows inside the stop
    band were annihilated and do not. This is the case the model exists for."""
    before, after, what = run(transmission, [{"type": "bandstop", "low": 3000,
                                              "high": 5000}])
    assert what["attempted"] == ["bandstop"]
    assert after < before            # better
    assert after > 0.05              # and still visibly wrong


def test_resampling_has_no_inverse():
    """Stated in code, not just in prose: the function is deliberately a
    no-op, because nothing can unfold two rows summed into one bin."""
    audio = np.sin(np.linspace(0, 100, 4000))
    same = inverse.undo_resample(audio, SAMPLE_RATE, 8000, anti_alias=False)
    assert np.array_equal(same, audio)


# --------------------------------------------------------------------------
# The table the UI builds itself from
# --------------------------------------------------------------------------

def test_the_table_and_the_code_agree():
    """If someone adds an inverse without updating the table, or the other way
    round, this fails rather than the page quietly lying."""
    table = {row["id"]: row["invertible"] for row in inverse.report()}
    assert table == inverse.INVERTIBLE
    assert all(row["why"] for row in inverse.report())

    # everything the bench offers has a verdict
    from app.services.channel_lab import CATALOGUE
    for entry in CATALOGUE:
        assert entry["id"] in table, f"{entry['id']} has no entry in the table"
