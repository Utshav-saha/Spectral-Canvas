"""Finding the transmission in a recording of unknown length.

The point of these is the lead-in. fsk_codec.find_preamble searches the first
three seconds, which is fine for the simulator and useless for a real call,
where the gap between pressing Record and the audio starting is however long it
takes to walk back to the laptop.
"""

import time

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

from voip import _tel, sync
from voip.config import SAMPLE_RATE, SYMBOL_SAMPLES


@pytest.fixture(scope="module")
def transmission():
    fsk = _tel.fsk()
    rng = np.random.default_rng(0)
    bits = rng.integers(0, 2, 512).astype(np.uint8)
    audio, _ = fsk.modulate(bits, header=np.zeros(16, np.uint8))
    return audio


def recording(audio, lead_seconds, noise=1e-4, seed=1):
    rng = np.random.default_rng(seed)
    lead = rng.normal(0.0, noise, int(lead_seconds * SAMPLE_RATE))
    return np.concatenate([lead, audio, np.zeros(SAMPLE_RATE)]), len(lead)


@pytest.mark.parametrize("lead", [0.0, 0.5, 1.0, 2.5])
def test_agrees_with_the_modem_inside_its_own_window(transmission, lead):
    """Where fsk_codec can still see, both must land on the same sample."""
    audio, truth = recording(transmission, lead)
    assert sync.find_preamble(audio).offset == _tel.fsk().find_preamble(audio) == truth


@pytest.mark.parametrize("lead", [5.0, 12.0, 45.0, 90.0])
def test_finds_leads_the_modem_cannot(transmission, lead):
    """The whole reason this module exists."""
    audio, truth = recording(transmission, lead)

    found = sync.find_preamble(audio)
    assert found.found
    assert found.offset == truth, f"off by {found.offset - truth} samples"
    assert found.score > 0.9

    beyond_reach = abs(_tel.fsk().find_preamble(audio) - truth) > SYMBOL_SAMPLES
    assert beyond_reach, "the modem unexpectedly coped; this test has lost its point"


def test_reports_no_transmission_in_noise():
    """A score has to come back with the offset, or 'not found' is unsayable."""
    noise = np.random.default_rng(3).normal(0.0, 0.1, 30 * SAMPLE_RATE)
    found = sync.find_preamble(noise)
    assert not found.found
    assert found.score < 0.2
    assert found.warnings


def test_reports_no_transmission_in_silence():
    found = sync.find_preamble(np.zeros(20 * SAMPLE_RATE))
    assert not found.found


def test_too_short_to_hold_a_preamble():
    found = sync.find_preamble(np.zeros(SYMBOL_SAMPLES * 3))
    assert not found.found
    assert "at least" in " ".join(found.warnings)


def test_whole_file_scan_is_quick(transmission):
    """Scanning 120 s must stay interactive, or nobody will use the whole-file default."""
    audio, _ = recording(transmission, 120.0)
    started = time.perf_counter()
    sync.find_preamble(audio)
    assert time.perf_counter() - started < 2.0


def test_gain_does_not_move_the_answer(transmission):
    """The score is a ratio, so AGC must not shift it."""
    audio, truth = recording(transmission, 8.0)
    quiet = sync.find_preamble(audio * 0.02)
    loud = sync.find_preamble(audio * 3.0)
    assert quiet.offset == loud.offset == truth
    assert abs(quiet.score - loud.score) < 0.05


@pytest.mark.parametrize("snr_db,expect_found", [(20, True), (10, True), (5, True), (0, True)])
def test_survives_a_band_limited_noisy_channel(transmission, snr_db, expect_found):
    """Offset error must stay inside one symbol, and the score must track quality."""
    sos = butter(4, [300.0, 3400.0], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    body = sosfilt(sos, transmission) * 0.35
    power = float(np.mean(body ** 2))
    noise = np.sqrt(power / (10 ** (snr_db / 10.0)))

    rng = np.random.default_rng(5)
    lead = int(30 * SAMPLE_RATE)
    audio = np.concatenate([
        rng.normal(0.0, noise, lead),
        body + rng.normal(0.0, noise, len(body)),
        rng.normal(0.0, noise, SAMPLE_RATE),
    ])

    found = sync.find_preamble(audio)
    assert found.found is expect_found
    if expect_found:
        assert abs(found.offset - lead) < SYMBOL_SAMPLES


def test_score_falls_as_the_channel_worsens(transmission):
    sos = butter(4, [300.0, 3400.0], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    body = sosfilt(sos, transmission) * 0.35
    power = float(np.mean(body ** 2))

    scores = []
    for snr_db in (25, 15, 5, -5):
        noise = np.sqrt(power / (10 ** (snr_db / 10.0)))
        rng = np.random.default_rng(6)
        audio = np.concatenate([rng.normal(0.0, noise, 8 * SAMPLE_RATE),
                                body + rng.normal(0.0, noise, len(body))])
        scores.append(sync.find_preamble(audio, refine=False).score)

    assert scores == sorted(scores, reverse=True), scores


def test_preamble_score_matches_the_modems_own_measure(transmission):
    """Same number fsk_codec computes internally, so the two cannot drift apart."""
    fsk = _tel.fsk()
    audio, truth = recording(transmission, 1.0)
    mags = fsk._symbol_magnitudes(audio, truth, len(fsk.PREAMBLE))
    expected = float(np.mean(
        mags[np.arange(len(fsk.PREAMBLE)), fsk.PREAMBLE] / (mags.sum(axis=1) + 1e-12)))
    assert sync.preamble_score(audio, truth) == pytest.approx(expected)


def test_search_seconds_still_limits_the_scan(transmission):
    audio, _ = recording(transmission, 40.0)
    assert not sync.find_preamble(audio, search_seconds=5.0).found
    assert sync.find_preamble(audio).found


def test_coarse_scan_peaks_at_the_transmission(transmission):
    audio, truth = recording(transmission, 6.0)
    scores, stride = sync.coarse_scan(audio)
    assert abs(int(np.argmax(scores)) * stride - truth) <= stride
