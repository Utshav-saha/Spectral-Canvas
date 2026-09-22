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
    # Generation A carries no header on the wire; its geometry lives in the
    # manifest, so every rehearsal hands that back the way `decode --run` does.
    kwargs.setdefault("manifest", prepared.manifest)
    return decode.decode(received, **kwargs)


def assert_starts_after(result, lead, prepared):
    """The preamble sits behind the simulated lead plus tx.wav's own lead-in.

    prepare() pads the transmission with DEFAULT_LEAD_IN_S of digital silence,
    and the simulator adds up to 0.8 s of extra jitter on top, so the offset is
    lead + lead_in + jitter -- not lead.
    """
    lead_in = prepared.manifest["wire"]["lead_in_seconds"]
    summary = result.report["summary"]
    offset = summary.get("offset_s") or result.report["sync"]["offset_seconds"]
    assert lead + lead_in <= offset <= lead + lead_in + 1.0, (
        f"expected the preamble between {lead + lead_in:.1f}s and "
        f"{lead + lead_in + 1.0:.1f}s, found it at {offset:.2f}s")


# --------------------------------------------------------------------------
# The happy paths
# --------------------------------------------------------------------------

def test_colour_picture_survives(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="A", grid=32,
                              colour=True)
    result = rehearse(prepared)

    assert result.verdict == "ok"
    assert result.report["payload"]["kind"] == "image"
    assert result.report["payload"]["generation"] == "A"
    # No codec in this rehearsal, so the amplitudes arrive intact and the
    # picture is exact. What GSM does to them is test_voip_channel's job -
    # that loss is Generation A's whole reason for being kept.
    # Generation A carries the pixel in a tone's amplitude, so it loses
    # accuracy to *any* level disturbance -- the rehearsal's AGC and noise
    # floor are enough, with no codec involved. Measured around 0.83 here.
    # That sensitivity is the whole reason it is the model's training target.
    quality = result.report["quality"]
    assert quality["image_compared"] is True
    assert 0.7 < quality["exact_fraction"] < 1.0
    assert_starts_after(result, LEAD, prepared)


def test_raw_pixel_picture_survives(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="B",
                              grid=16, levels=4)
    result = rehearse(prepared)

    assert result.verdict == "ok"
    assert result.report["frame"]["generation"] == "B"
    assert (result.report["frame"]["rows"], result.report["frame"]["cols"]) == (16, 16)


def test_text_is_rendered_as_a_picture():
    """Generation C carried UTF-8 bytes and gave back the string. With it cut,
    text over a call is drawn into the grid and arrives as pixels, so what
    comes back is a picture of the message, not the message."""
    prepared = encode.prepare(text="HELLO", generation="A", grid=32)

    assert any("rendered as a picture" in w for w in prepared.warnings)
    assert prepared.manifest["payload"]["kind"] == "image"

    result = rehearse(prepared)
    assert result.verdict == "ok"
    assert result.text is None
    assert result.image is not None


def test_text_rendered_as_a_picture(synthetic_image):
    prepared = encode.prepare(text="RENDERED", generation="A", as_image=True,
                              grid=32)
    result = rehearse(prepared)
    assert result.verdict == "ok"
    assert result.report["payload"]["kind"] == "image"


# --------------------------------------------------------------------------
# The lock
# --------------------------------------------------------------------------

def test_the_right_pin_rebuilds_the_picture(synthetic_image):
    """Generation B, where the lock is the clean case: bits are exact, so the
    permutation is the only thing between the sender and the picture."""
    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16,
                              levels=4, locked=True, **LOCK)
    result = rehearse(prepared, locked=True, **LOCK)

    assert result.verdict == "ok"
    assert result.image is not None
    assert result.report["quality"]["exact_fraction"] == 1.0


def test_the_wrong_pin_gives_static_not_an_error(synthetic_image):
    """Intended behaviour, though its shape changed when Generation C went.

    Reed-Solomon could *detect* a wrong key -- parity failed, so the decoder
    knew to paint noise and say so in the verdict. A permutation cannot: every
    PIN unshuffles to a real picture, and the wrong one unshuffles to the wrong
    picture. So the verdict stays "ok" and the evidence is in the pixels.
    """
    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16,
                              levels=4, locked=True, **LOCK)
    result = rehearse(prepared, locked=True, **dict(LOCK, pin="9999"))

    assert result.image is not None            # it decodes; that is the point
    assert result.report["quality"]["exact_fraction"] < 0.6
    assert result.report["quality"]["mae"] > 20


def test_not_unlocking_at_all_is_the_same_static(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16,
                              levels=4, locked=True, **LOCK)
    result = rehearse(prepared)

    assert result.image is not None
    assert result.report["quality"]["exact_fraction"] < 0.6


def test_locking_costs_generation_a_accuracy(synthetic_image):
    """A finding worth keeping honest, not a regression.

    Scrambling destroys the spatial correlation between neighbouring pixels.
    A smooth column drives a few tones at similar amplitudes; a scrambled one
    drives every row at an unrelated amplitude, which raises the crest factor,
    and peak normalisation then buys each tone less headroom. So the same
    channel costs a locked Generation A picture far more than an open one --
    measured around 0.66 exact against 0.85 -- and the right PIN is only
    modestly better than the wrong one. Generation B has no such cost.
    """
    locked = encode.prepare(source=synthetic_image, generation="A", grid=24,
                            locked=True, **LOCK)
    opened = encode.prepare(source=synthetic_image, generation="A", grid=24)

    with_key = rehearse(locked, locked=True, **LOCK).report["quality"]
    no_lock = rehearse(opened).report["quality"]

    assert with_key["exact_fraction"] < no_lock["exact_fraction"]
    # still better than the wrong key, just not by much
    wrong = rehearse(locked, locked=True, **dict(LOCK, pin="9999")).report["quality"]
    assert with_key["exact_fraction"] > wrong["exact_fraction"]


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

    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16,
                              levels=4)
    seen.add(rehearse(prepared).verdict)

    received, _ = simulate.simulate(prepared.audio, lead_seconds=LEAD, gsm=False, seed=3)
    seen.add(decode.decode(received[:len(received) - int(8.0 * 8000)]).verdict)

    assert {"no-sync", "ok", "truncated"} <= seen

    # "wrong-pin" left the ladder with Generation C. Reed-Solomon could tell a
    # bad key from a good one; a permutation cannot, so a wrong PIN now decodes
    # successfully to the wrong picture. The evidence moved from the verdict to
    # the pixels -- see test_the_wrong_pin_gives_static_not_an_error.
    locked = encode.prepare(source=synthetic_image, generation="B", grid=16,
                            levels=4, locked=True, **LOCK)
    wrong = rehearse(locked, locked=True, **dict(LOCK, pin="9999"))
    assert wrong.verdict == "ok"
    assert wrong.report["quality"]["exact_fraction"] < 0.6


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
    prepared = encode.prepare(source=synthetic_image, generation="A", grid=24)
    encode.write_run(prepared, run_dir)

    assert os.path.isfile(os.path.join(run_dir, "tx.wav"))
    assert os.path.isfile(os.path.join(run_dir, "sent.png"))

    manifest = report.load_manifest(run_dir)
    manifest["_run_dir"] = run_dir

    received, _ = simulate.simulate(prepared.audio, lead_seconds=LEAD, gsm=False, seed=3)
    result = decode.decode(received, manifest=manifest)
    decode.write_run(result, run_dir, sent_image=prepared.sent_image)

    # The reference is the level indices in the manifest rather than sent.png,
    # so scoring works whether or not a run folder exists on disk.
    assert result.report["quality"]["compare_to"] == "manifest"
    assert result.report["quality"]["image_compared"] is True
    assert os.path.isfile(os.path.join(run_dir, "report.json"))
    assert os.path.isfile(os.path.join(run_dir, "received.png"))


def test_plan_predicts_the_real_airtime(synthetic_image):
    predicted = encode.plan(source=synthetic_image, generation="A", grid=32, colour=True)
    actual = encode.prepare(source=synthetic_image, generation="A", grid=32, colour=True)
    assert predicted["airtime_seconds"] == pytest.approx(
        actual.manifest["wire"]["airtime_seconds"], abs=0.1)


def test_genb_plan_predicts_the_real_airtime(synthetic_image):
    predicted = encode.plan(generation="B", grid=16, levels=4)
    actual = encode.prepare(source=synthetic_image, generation="B", grid=16, levels=4)

    assert predicted["payload_bits"] == 512
    assert predicted["airtime_seconds"] == pytest.approx(
        actual.manifest["wire"]["airtime_seconds"], abs=0.05)
