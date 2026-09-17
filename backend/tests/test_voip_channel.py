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
def test_colour_survives_real_gsm(synthetic_image):
    """Reed-Solomon is all-or-nothing, so this is exact or it is nothing."""
    prepared = encode.prepare(source=synthetic_image, generation="C",
                              size=64, quality=50)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=15.0,
                                    loss=0.02, seed=2, gsm=True)
    result = decode.decode(received)
    assert result.verdict == "ok"
    assert np.array_equal(np.asarray(result.image),
                          np.asarray(prepared.sent_image))


@needs_gsm
def test_text_survives_real_gsm():
    prepared = encode.prepare(text="through GSM 06.10", generation="C")
    received, _ = simulate.simulate(prepared.audio, lead_seconds=12.0,
                                    loss=0.02, seed=3, gsm=True)
    assert decode.decode(received).text == "through GSM 06.10"


@needs_gsm
def test_the_report_records_reed_solomon_headroom(synthetic_image):
    """How close the call came to failing is the interesting number."""
    prepared = encode.prepare(source=synthetic_image, generation="C", size=64)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=10.0,
                                    loss=0.02, seed=4, gsm=True)
    payload = decode.decode(received).report["payload"]

    assert payload["opened"]
    assert payload["repaired_bytes"] <= payload["rs_limit_total"]
    assert payload["rs_headroom"] >= 0


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
