"""prepare -> rehearse a call -> decode, for every path and every failure mode.

Everything here runs with no phone, no SDK and no ffmpeg: the codec-free
rehearsal exists so this suite is green on a bare machine. The GSM and SIP
rungs live in test_voip_channel.py.

The lead-in in these tests is 30 seconds on purpose. That is the case the
modem's own 3-second preamble search cannot reach, and the reason this package
exists at all.
"""

import numpy as np
import pytest

from voip import decode, encode, simulate

LEAD = 30.0
LOCK = {"caller": "01712345678", "receiver": "01787654321", "pin": "4321"}


def rehearse(prepared, seed=3, **kwargs):
    received, _ = simulate.simulate(prepared.audio, lead_seconds=LEAD,
                                    gsm=False, seed=seed)
    return decode.decode(received, **kwargs)


def assert_starts_after(result, lead, prepared):
    """The preamble sits behind the simulated lead plus tx.wav's own lead-in.

    prepare() pads the transmission with DEFAULT_LEAD_IN_S of digital silence,
    and the simulator adds up to 0.8 s of extra jitter on top, so the offset is
    lead + lead_in + jitter -- not lead.
    """
    lead_in = prepared.manifest["wire"]["lead_in_seconds"]
    offset = result.report["summary"]["offset_s"]
    assert lead + lead_in <= offset <= lead + lead_in + 1.0, (
        f"expected the preamble between {lead + lead_in:.1f}s and "
        f"{lead + lead_in + 1.0:.1f}s, found it at {offset:.2f}s")


# --------------------------------------------------------------------------
# The happy paths
# --------------------------------------------------------------------------

def test_colour_picture_survives(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="C",
                              size=96, quality=50)
    result = rehearse(prepared)

    assert result.verdict == "ok"
    assert result.report["payload"]["kind"] == "image"
    assert np.array_equal(np.asarray(result.image),
                          np.asarray(prepared.sent_image)), \
        "Reed-Solomon is all-or-nothing, so a success must be byte-exact"
    assert_starts_after(result, LEAD, prepared)


def test_raw_pixel_picture_survives(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="B",
                              grid=16, levels=4)
    result = rehearse(prepared)

    assert result.verdict == "ok"
    assert result.report["frame"]["generation"] == "B"
    assert (result.report["frame"]["rows"], result.report["frame"]["cols"]) == (16, 16)


@pytest.mark.parametrize("message", [
    "hello from a phone call",
    "ünïcödé and emoji ✓🎵 survive Reed-Solomon",
])
def test_text_survives_byte_exact(message):
    prepared = encode.prepare(text=message, generation="C")
    result = rehearse(prepared)
    assert result.verdict == "ok"
    assert result.text == message


def test_text_rendered_as_a_picture(synthetic_image):
    prepared = encode.prepare(text="RENDERED", generation="C", as_image=True,
                              size=96, quality=50)
    result = rehearse(prepared)
    assert result.verdict == "ok"
    assert result.report["payload"]["kind"] == "image"


# --------------------------------------------------------------------------
# The lock
# --------------------------------------------------------------------------

def test_the_right_pin_rebuilds_the_picture(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="C", size=64,
                              quality=50, locked=True, **LOCK)
    result = rehearse(prepared, locked=True, **LOCK)
    assert result.verdict == "ok"
    assert result.image is not None


def test_the_wrong_pin_gives_static_not_an_error(synthetic_image):
    """Intended behaviour, and the report has to name it as such."""
    prepared = encode.prepare(source=synthetic_image, generation="C", size=64,
                              quality=50, locked=True, **LOCK)
    wrong = dict(LOCK, pin="9999")
    result = rehearse(prepared, locked=True, **wrong)

    assert result.verdict == "wrong-pin"
    assert result.image is None
    assert result.static is not None
    assert any("PIN" in hint for hint in result.report["hints"])


def test_a_locked_transmission_will_not_open_without_the_key(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="C", size=64,
                              quality=50, locked=True, **LOCK)
    result = rehearse(prepared)
    assert result.verdict == "rs-failed"
    assert result.image is None


# --------------------------------------------------------------------------
# The failure ladder
# --------------------------------------------------------------------------

def test_noise_is_reported_as_no_transmission():
    noise = np.random.default_rng(9).normal(0.0, 0.1, 25 * 8000)
    result = decode.decode(noise)
    assert result.verdict == "no-sync"
    assert not result.report["sync"]["found"]
    assert result.report["hints"]


def test_silence_is_reported_as_no_transmission():
    assert decode.decode(np.zeros(20 * 8000)).verdict == "no-sync"


def test_a_recording_stopped_early_is_reported_as_truncated(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="B",
                              grid=16, levels=4)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=LEAD,
                                    gsm=False, seed=3)
    result = decode.decode(received[:len(received) - int(5.0 * 8000)])

    assert result.verdict == "truncated"
    assert result.report["frame"]["truncated_symbols"] > 0
    assert any("stops" in hint for hint in result.report["hints"])


def test_the_verdict_ladder_covers_every_outcome(synthetic_image):
    """Each rung must be reachable, or the ladder is decoration."""
    seen = set()

    seen.add(decode.decode(np.zeros(20 * 8000)).verdict)

    prepared = encode.prepare(source=synthetic_image, generation="C", size=64)
    seen.add(rehearse(prepared).verdict)

    received, _ = simulate.simulate(prepared.audio, lead_seconds=LEAD, gsm=False, seed=3)
    seen.add(decode.decode(received[:len(received) - int(8.0 * 8000)]).verdict)

    locked = encode.prepare(source=synthetic_image, generation="C", size=64,
                            locked=True, **LOCK)
    seen.add(rehearse(locked, locked=True, **dict(LOCK, pin="9999")).verdict)

    assert {"no-sync", "ok", "truncated", "wrong-pin"} <= seen


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------

@pytest.mark.parametrize("gain", [0.01, 0.2, 1.0, 3.0])
def test_decoding_ignores_the_call_volume(synthetic_image, gain):
    """argmax is gain-blind; that is the whole basis of the scheme."""
    prepared = encode.prepare(source=synthetic_image, generation="B",
                              grid=16, levels=4)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=10.0,
                                    gsm=False, seed=4)
    assert decode.decode(received * gain).verdict == "ok"


@pytest.mark.parametrize("lead", [0.0, 5.0, 30.0, 90.0])
def test_any_lead_in_works(synthetic_image, lead):
    prepared = encode.prepare(source=synthetic_image, generation="B",
                              grid=16, levels=4)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=lead,
                                    gsm=False, seed=5)
    result = decode.decode(received)
    assert result.verdict == "ok"
    assert_starts_after(result, lead, prepared)


def test_packet_loss_is_survivable(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="B",
                              grid=16, levels=4)
    received, _ = simulate.simulate(prepared.audio, loss=0.02, lead_seconds=10.0,
                                    gsm=False, seed=6)
    assert decode.decode(received).verdict == "ok"


# --------------------------------------------------------------------------
# Run folders
# --------------------------------------------------------------------------

def test_a_run_folder_holds_everything_needed_to_score_the_call(tmp_path, synthetic_image):
    import os

    from voip import report

    run_dir = str(tmp_path / "run")
    prepared = encode.prepare(source=synthetic_image, generation="C", size=64)
    encode.write_run(prepared, run_dir)

    assert os.path.isfile(os.path.join(run_dir, "tx.wav"))
    assert os.path.isfile(os.path.join(run_dir, "sent.png"))

    manifest = report.load_manifest(run_dir)
    manifest["_run_dir"] = run_dir

    received, _ = simulate.simulate(prepared.audio, lead_seconds=LEAD, gsm=False, seed=3)
    result = decode.decode(received, manifest=manifest)
    decode.write_run(result, run_dir, sent_image=prepared.sent_image)

    assert result.report["quality"]["compare_to"] == "sent.png"
    assert result.report["quality"]["identical"] is True
    assert os.path.isfile(os.path.join(run_dir, "report.json"))
    assert os.path.isfile(os.path.join(run_dir, "received.png"))


def test_plan_predicts_the_real_airtime(synthetic_image):
    predicted = encode.plan(source=synthetic_image, generation="C", size=96, quality=50)
    actual = encode.prepare(source=synthetic_image, generation="C", size=96, quality=50)
    assert predicted["airtime_seconds"] == pytest.approx(
        actual.manifest["wire"]["airtime_seconds"], abs=0.1)


def test_genb_plan_predicts_the_real_airtime(synthetic_image):
    predicted = encode.plan(generation="B", grid=16, levels=4)
    actual = encode.prepare(source=synthetic_image, generation="B", grid=16, levels=4)

    assert predicted["payload_bits"] == 512
    assert predicted["airtime_seconds"] == pytest.approx(
        actual.manifest["wire"]["airtime_seconds"], abs=0.05)
