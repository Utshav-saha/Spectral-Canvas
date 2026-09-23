"""Surviving a jitter-buffer slip on a long call.

These exist because of one real 860 s Linphone call. The tones arrived fine -
92-98% of symbols were correct once realigned - but the handset's jitter
buffer slipped 700 samples, 2.2 whole symbols, in discrete jumps, and the
picture came back as noise: 8.4% of pixels right, which for that image is
chance.

Nothing downstream could have repaired it. A grid shifted by a whole symbol is
still aligned to *a* symbol boundary, so every alignment score computed from
the waveform peaks identically at every multiple of SYMBOL_SAMPLES and none of
them can see the slip. The stream had nothing to realign to. Now it does.

`slipped()` replays the shift profile actually measured on that recording.
"""

import numpy as np
import pytest

import image_fsk
import fsk_codec as fsk


# (seconds into the transmission, cumulative sample shift) as measured
MEASURED_SLIPS = [(7.6, 20), (79, -180), (151, -200), (222, -260), (294, -100),
                  (366, -180), (437, -700), (509, -760), (581, -660),
                  (652, -580), (724, -620), (796, -600)]


def slipped(audio, slips=MEASURED_SLIPS):
    """Insert or drop samples at each step change, the way a jitter buffer does."""
    out, prev_shift, cursor = [], 0, 0
    for seconds, shift in slips:
        at = min(int(seconds * fsk.SAMPLE_RATE), len(audio))
        out.append(audio[cursor:at])
        delta = shift - prev_shift
        if delta > 0:
            out.append(audio[at:at + delta] if at + delta <= len(audio)
                       else np.zeros(delta))
            cursor = at
        else:
            cursor = max(at - delta, 0)
        prev_shift = shift
    out.append(audio[cursor:])
    return np.concatenate(out)


def picture(rows, cols, channels, levels, seed=0):
    shape = (rows, cols, channels) if channels > 1 else (rows, cols)
    rng = np.random.default_rng(seed)
    return rng.integers(0, levels, shape).astype(np.float64) / (levels - 1)


def decoded(audio, info):
    return image_fsk.decode_image(audio, info)


def test_markers_are_not_periodic():
    """PREAMBLE alternates with a period of two symbols, so it scores the same
    shifted by two - which is the very ambiguity the marker has to resolve."""
    assert len(set(fsk.RESYNC.tolist())) == len(fsk.RESYNC)
    for shift in range(1, len(fsk.RESYNC)):
        overlap = fsk.RESYNC[shift:] == fsk.RESYNC[:-shift]
        assert not overlap.any(), f"RESYNC lines up with itself at shift {shift}"


def test_a_whole_symbol_slip_is_survived():
    """The regression this file exists for: 8.4% before, near-exact after."""
    act = picture(64, 64, 3, 16)
    audio, info = image_fsk.encode_image(act, levels=16)
    assert info["resync_interval"], "markers must be on by default"

    assert np.mean(decoded(slipped(audio), info) == act) > 0.98


def test_the_same_slip_destroys_a_stream_without_markers():
    """Without this, the test above could pass because the slip does nothing."""
    act = picture(64, 64, 3, 16)
    bits = image_fsk.activation_to_bits(act, 16)
    audio, info = fsk.modulate(bits, resync=0)
    info.update(shape=list(act.shape), gray_levels=16)

    assert np.mean(decoded(audio, info) == act) == 1.0          # clean: exact
    assert np.mean(decoded(slipped(audio), info) == act) < 0.30  # slipped: gone


def test_a_clean_transmission_is_still_exact():
    """Markers must not cost anything when there is nothing to correct."""
    for channels, levels in ((1, 4), (3, 16)):
        act = picture(32, 32, channels, levels)
        audio, info = image_fsk.encode_image(act, levels=levels)
        assert np.mean(decoded(audio, info) == act) == 1.0


def test_old_recordings_without_markers_still_decode():
    """Files sent before this change carry no resync_interval, and the reader
    has to keep treating them as one uninterrupted run of symbols."""
    act = picture(24, 24, 1, 4)
    bits = image_fsk.activation_to_bits(act, 4)
    audio, info = fsk.modulate(bits, resync=0)
    info.update(shape=list(act.shape), gray_levels=4)
    assert info["resync_interval"] == 0
    assert np.mean(decoded(audio, info) == act) == 1.0


def test_the_markers_are_counted_in_the_airtime():
    """budget() drives the page's 'how long will this take', and the markers
    really are on the wire."""
    _, seconds = image_fsk.budget(64, 64, channels=3, levels=16)
    act = picture(64, 64, 3, 16)
    _, info = image_fsk.encode_image(act, levels=16)
    assert seconds == pytest.approx(info["duration_seconds"], abs=0.05)
    assert info["wire_symbols"] > info["n_symbols"]
    # 8 symbols every 512 is 1.6%; anything near 10% means a wrong interval
    assert info["wire_symbols"] / info["n_symbols"] < 1.05
