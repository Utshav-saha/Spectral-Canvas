"""The rungs above the codec-free rehearsal: real GSM, and a real SIP call.

These are slower and need tools the plain test run does not, so they skip
cleanly when those are absent. They are also the ones that matter most before
picking up a phone.

The SIP rung is the important one. `run_local_call.sh` puts two pjsua instances
on loopback, so there is real SIP signalling, real RTP packetisation, a real
jitter buffer and real packet-loss concealment -- with no account, no phone and
no SDK. It catches things the offline simulator cannot: a tx.wav that is not
8 kHz 16-bit mono PCM (pjsua refuses to open one at all), and a codec quietly
falling back to PCMU, which would make a clean decode prove nothing.
"""

import numpy as np
import pytest

from voip import decode, encode, simulate

from conftest import needs_gsm, needs_pjsua


@needs_gsm
def test_a_picture_survives_real_gsm(synthetic_image):
    """GSM 06.10 is the codec the whole 16-FSK design exists to survive."""
    prepared = encode.prepare(source=synthetic_image, generation="B",
                              grid=16, levels=4)
    received, meta = simulate.simulate(prepared.audio, lead_seconds=20.0,
                                       loss=0.02, seed=1, gsm=True)
    assert meta["codec"] == "gsm0610"

    result = decode.decode(received)
    assert result.verdict == "ok"
    assert result.report["summary"]["offset_s"] > 20.0


@needs_gsm
def test_generation_b_survives_real_gsm_exactly(synthetic_image):
    """The claim Generation B exists to make. The decoder takes an argmax over
    sixteen tones and never compares magnitudes, so a codec that flattens
    loudness cannot reach it."""
    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16,
                              levels=4)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=15.0,
                                    loss=0.02, seed=2, gsm=True)
    result = decode.decode(received, manifest=prepared.manifest)

    assert result.verdict == "ok"
    assert result.report["quality"]["exact_fraction"] == 1.0


@needs_gsm
def test_generation_a_is_damaged_by_real_gsm(synthetic_image):
    """The other half of the same claim, and the reason the model exists.

    Generation A puts the pixel in a tone's amplitude. GSM 06.10 models each
    20 ms frame with an 8-pole LPC envelope, which keeps where the peaks are
    and loses how tall they are. The picture still arrives -- it just arrives
    wrong, by a margin that is stable enough to learn from.
    """
    prepared = encode.prepare(source=synthetic_image, generation="A", grid=24)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=15.0,
                                    loss=0.02, seed=2, gsm=True)
    result = decode.decode(received, manifest=prepared.manifest)

    assert result.verdict == "ok"
    assert result.image is not None
    exact = result.report["quality"]["exact_fraction"]
    assert 0.4 < exact < 0.98, (
        "Generation A should be damaged but not destroyed; a perfect score "
        "usually means GSM was not actually negotiated.")


@needs_gsm
def test_the_two_generations_disagree_over_the_same_channel(synthetic_image):
    """A side-by-side, which is the measurement the report wants."""
    results = {}
    for generation, grid in (("A", 24), ("B", 16)):
        prepared = encode.prepare(source=synthetic_image, generation=generation,
                                  grid=grid, levels=4)
        received, _ = simulate.simulate(prepared.audio, lead_seconds=10.0,
                                        loss=0.02, seed=4, gsm=True)
        results[generation] = decode.decode(
            received, manifest=prepared.manifest).report["quality"]["exact_fraction"]

    assert results["B"] > results["A"]
    assert results["B"] == 1.0


# --------------------------------------------------------------------------
# A real SIP call
# --------------------------------------------------------------------------

@needs_pjsua
@pytest.mark.parametrize("generation,kwargs", [
    ("B", {"grid": 16, "levels": 4}),
])
def test_a_picture_survives_a_real_sip_call(tmp_path, synthetic_image,
                                            generation, kwargs):
    """Real signalling, real RTP, real jitter buffer. No phone required."""
    import os

    prepared = encode.prepare(source=synthetic_image, generation=generation, **kwargs)
    run_dir = str(tmp_path / "run")
    encode.write_run(prepared, run_dir)

    tx = os.path.join(run_dir, "tx.wav")
    rx = os.path.join(run_dir, "rx_sip.wav")
    outcome = simulate.local_call(tx, rx, codec="GSM")

    assert os.path.isfile(rx)
    # If libgsm was not compiled in, pjsua silently falls back to G.711, which
    # is nearly transparent -- a pass over that proves much less.
    assert outcome["negotiated"] in (None, "GSM"), outcome["warning"]

    result = decode.decode(rx)
    assert result.verdict == "ok", result.report["hints"]
    assert result.report["sync"]["preamble_score"] > 0.5


@needs_pjsua
def test_pjsua_would_reject_a_float_wav(tmp_path):
    """Why write_int16_wav exists. A float64 WAV is silently useless here."""
    import wave

    from voip.audio_io import write_int16_wav

    path = str(tmp_path / "tx.wav")
    write_int16_wav(path, np.zeros(8000))
    with wave.open(path) as handle:
        assert handle.getsampwidth() == 2
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 8000


def test_the_codec_free_rehearsal_needs_no_tools(synthetic_image):
    """CI has no ffmpeg and no libgsm; this path must still exercise everything."""
    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16)
    received, meta = simulate.simulate(prepared.audio, lead_seconds=30.0,
                                       gsm=False, seed=7)
    assert meta["path"] == "codec-free"
    assert meta["codec"] is None
    assert decode.decode(received).verdict == "ok"


def test_asking_for_gsm_without_a_codec_explains_itself(synthetic_image):
    if simulate.gsm_available():
        pytest.skip("a GSM codec is installed here")
    from voip.config import VoipDependencyError

    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16)
    with pytest.raises(VoipDependencyError, match="brew install libgsm"):
        simulate.simulate(prepared.audio, gsm=True)
