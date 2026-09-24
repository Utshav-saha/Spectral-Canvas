"""A real SIP call, placed by dial.py, with a stand-in for the phone.

`test_voip_dial.py` covers the argument building and the refusals without ever
dialling. This file does the opposite: it runs a second pjsua on loopback that
answers and records, exactly as the phone does when you press Record, and then
decodes the picture back out of that recording.

It exists because two bugs got all the way to a real handset without a single
unit test noticing, and neither was visible from the arguments:

  1. the dialler closed pjsua's stdin, and pjsua quits when stdin closes -- so
     the call was torn down about a second in, before the tones ever played
     (and usually before REGISTER even finished);
  2. it relied on `--auto-play`, which pjsua documents as feeding *incoming*
     calls only, so an outgoing call would have played nothing regardless.

The assertion that catches the first is `seconds >= the transmission length`;
the one that catches the second is that the recording decodes at all.
"""

import os
import socket
import subprocess
import time

import pytest

from conftest import needs_pjsua

from voip import decode as decoder
from voip import encode as encoder


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _StandInPhone:
    """pjsua answering on loopback: auto-answers, records, registers nobody.

    It doubles as the registrar, because pjsua answers 200 to the REGISTER it
    is sent, which is all dial.py waits for.
    """

    def __init__(self, port, recording):
        self.port = port
        self.recording = recording
        self.proc = subprocess.Popen(
            ["pjsua", "--null-audio", "--no-vad", "--clock-rate=8000",
             "--no-tcp", "--dis-codec=*", "--add-codec=GSM",
             f"--local-port={port}", "--auto-answer=200",
             f"--rec-file={recording}", "--auto-rec", "--app-log-level=4"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT, text=True, bufsize=1)
        time.sleep(2)

    def stop(self):
        try:
            self.proc.stdin.write("q\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=8)
        except Exception:
            self.proc.kill()


@pytest.fixture
def placed_call(tmp_path, monkeypatch, synthetic_image):
    """Prepare a short transmission, dial it, and hand back what was recorded."""
    from voip import dial

    port = _free_port()
    recording = str(tmp_path / "phone.wav")

    monkeypatch.setenv("VOIP_SIP_IDENTITY", "sip:tester@127.0.0.1")
    monkeypatch.setenv("VOIP_SIP_PASSWORD", "notused")
    monkeypatch.setenv("VOIP_SIP_REGISTRAR", f"sip:127.0.0.1:{port}")
    # the human needs this gap to mute and press Record; a test does not
    # raising=False so this still runs against a dialler that has no grace
    # period at all - otherwise the regression it guards would error out here
    # instead of failing the assertion that names it.
    monkeypatch.setattr(dial, "ANSWER_GRACE", 1.0, raising=False)

    prepared = encoder.prepare(source=synthetic_image, generation="B",
                               grid=16, levels=4)
    audio = prepared.audio

    phone = _StandInPhone(port, recording)
    try:
        call_id = dial.place(audio, f"sip:phone@127.0.0.1:{port}", codec="GSM")
        deadline = time.time() + 180
        while time.time() < deadline:
            state = dial.progress(call_id)
            if state["state"] in ("done", "failed"):
                break
            time.sleep(0.5)
    finally:
        phone.stop()

    return {"state": dial.progress(call_id), "recording": recording,
            "audio": audio, "prepared": prepared}


@needs_pjsua
def test_the_call_runs_for_as_long_as_the_transmission(placed_call):
    """The regression guard for the closed-stdin bug.

    pjsua quits the moment its stdin closes. When it did, the call lasted about
    a second whatever the transmission length, and the browser still cheerfully
    reported `done`. Anything much shorter than the audio means it hung up
    early again.
    """
    state = placed_call["state"]
    assert state["state"] == "done", state.get("error")

    expected = len(placed_call["audio"]) / 8000.0
    assert state["seconds"] >= expected, (
        f"the call lasted {state['seconds']}s but the transmission is "
        f"{expected:.1f}s long - pjsua hung up before it finished playing")


@needs_pjsua
def test_it_walks_the_states_the_page_shows(placed_call):
    """`answered` is the one the person acts on: it is when they press Record."""
    assert placed_call["state"]["state"] == "done"
    assert placed_call["state"]["returncode"] == 0


@needs_pjsua
def test_the_picture_survives_the_call(placed_call):
    """The regression guard for `--auto-play`, which feeds incoming calls only.

    With it, an outgoing call carried silence: the WAV sat in the conference
    bridge connected to nothing. The player has to be wired to the call's own
    port, and the only proof of that is a picture at the far end.
    """
    recorded = os.path.getsize(placed_call["recording"])
    assert recorded > 10000, "the phone recorded essentially nothing"

    result = decoder.decode(placed_call["recording"],
                            manifest=placed_call["prepared"].manifest)
    assert result.ok, f"verdict {result.verdict}"
    assert result.report["summary"]["preamble_score"] > 0.5


# --------------------------------------------------------------------------
# Answer mode: the phone calls us
# --------------------------------------------------------------------------

@needs_pjsua
def test_the_phone_can_call_us_instead(tmp_path, monkeypatch, synthetic_image):
    """The direction that does not depend on the handset ringing.

    A call placed *to* a phone only alerts it when the app is in the foreground
    or a push notification gets through; on the free service it often lands in
    the call history having never rung, which is unfixable from this end. A
    call placed *from* the phone has neither problem and reaches the identical
    channel, so this path has to work as well as dialling does.
    """
    from voip import dial

    monkeypatch.setenv("VOIP_SIP_IDENTITY", "sip:mac@127.0.0.1")
    monkeypatch.setenv("VOIP_SIP_PASSWORD", "notused")
    monkeypatch.setenv("VOIP_SIP_REGISTRAR", f"sip:127.0.0.1:{_free_port()}")
    monkeypatch.setattr(dial, "ANSWER_GRACE", 1.0, raising=False)
    monkeypatch.setattr(dial, "WAIT_TIMEOUT", 45.0, raising=False)
    # there is no registrar on loopback, and waiting for one is not the subject
    real_wait = dial._Console.wait
    monkeypatch.setattr(dial._Console, "wait", lambda self, pat, t: (
        True if "registration" in pat else real_wait(self, pat, t)))

    prepared = encoder.prepare(source=synthetic_image, generation="B",
                               grid=16, levels=4)
    recording = str(tmp_path / "phone.wav")

    call_id = dial.answer(prepared.audio, codec="GSM")
    time.sleep(6)                      # let it bind and settle
    # it binds a free port rather than fighting everything else over 5060
    our_port = dial.progress(call_id)["local_port"]

    phone = subprocess.Popen(
        ["pjsua", "--null-audio", "--no-vad", "--clock-rate=8000", "--no-tcp",
         "--dis-codec=*", "--add-codec=GSM", f"--local-port={_free_port()}",
         f"--rec-file={recording}", "--auto-rec", "--app-log-level=4",
         f"sip:mac@127.0.0.1:{our_port}"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            state = dial.progress(call_id)
            if state["state"] in ("done", "failed"):
                break
            time.sleep(0.5)
    finally:
        try:
            phone.stdin.write("h\nq\n")
            phone.stdin.flush()
            phone.wait(timeout=8)
        except Exception:
            phone.kill()

    assert state["state"] == "done", state.get("error")
    result = decoder.decode(recording, manifest=prepared.manifest)
    assert result.ok, f"verdict {result.verdict}"


@needs_pjsua
def test_the_recording_holds_the_transmission_only_once(placed_call):
    """The regression guard for pjsua's looping file player.

    It restarts the WAV on reaching the end, and the hangup comes two seconds
    after the last tone, so the far end used to record the head of a second
    transmission. That second preamble scores exactly as well as the first, and
    as soon as Record is pressed a moment late it is the only one left: sync
    lands a few seconds from the end of the file, finds no transmission behind
    it, and rebuilds a blank white picture that reports itself as found.

    Measured on a real 48x48 call before the fix: Record 0.25 s late took the
    rebuild from 100% of pixels exact to a blank frame.
    """
    from voip import sync
    from voip.audio_io import load_audio

    recorded, _ = load_audio(placed_call["recording"])
    sent = len(placed_call["audio"])

    located = sync.find_preamble(recorded)
    assert located.found

    # nothing preamble-like may follow the end of the one transmission
    tail = recorded[located.offset + sent:]
    if len(tail) > 4000:
        after = sync.find_preamble(tail)
        assert not after.found, (
            f"a second preamble {after.offset / 8000:.1f}s into the tail "
            f"(score {after.score:.3f}): the player looped back into the call")


@needs_pjsua
def test_the_codec_that_was_negotiated_is_reported(placed_call):
    """The one safeguard the whole telephony argument rests on.

    If GSM was not compiled into this pjsua it falls back to PCMU, which is
    nearly transparent, and a clean decode over G.711 says nothing about
    surviving a voice codec. This is the assertion that the page can actually
    tell you which one you got - it used to search pjsua's log for the word
    "codec", which pjsua never writes on a line that names one, so it reported
    nothing for every call ever placed.
    """
    assert placed_call["state"].get("negotiated") == "GSM"
