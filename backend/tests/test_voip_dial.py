"""Placing a real SIP call with pjsua.

Nothing here dials anything. What is worth protecting without a phone on the
other end is the argument building and the refusals, because those are what
stand between a user and a silently wrong call: the wrong account, a password
in a request body, or pjsua quietly negotiating a transparent codec.
"""

import inspect
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voip import dial
from voip.config import VoipDependencyError, VoipError

IDENTITY = "sip:laptop@sip.linphone.org"
PHONE = "sip:phone@sip.linphone.org"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("VOIP_SIP_IDENTITY", IDENTITY)
    monkeypatch.setenv("VOIP_SIP_PASSWORD", "hunter2")
    monkeypatch.delenv("VOIP_SIP_REGISTRAR", raising=False)
    monkeypatch.delenv("VOIP_SIP_USERNAME", raising=False)
    return dial.credentials()


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def test_no_environment_means_no_credentials(monkeypatch):
    monkeypatch.delenv("VOIP_SIP_IDENTITY", raising=False)
    monkeypatch.delenv("VOIP_SIP_PASSWORD", raising=False)
    assert dial.credentials() is None


def test_the_registrar_is_derived_from_the_identity(configured):
    """TLS on 443, not plain SIP on 5060.

    Measured on a network that blocks SIP's own ports: a REGISTER to UDP 5060
    draws no reply at all, which pjsua reports as a 503 after ~18 s and reads
    like the server is down. linphone.org publishes `_sips._tcp ... 443` for
    this, and 443 gets through where 5060 does not.
    """
    assert configured["registrar"] == "sip:sip.linphone.org:443;transport=tls"
    assert configured["username"] == "laptop"


def test_the_transport_exists_and_everything_routes_through_it(configured):
    """A `transport=tls` URI with no TLS transport fails with "Socket is not
    connected", and an INVITE left to its own DNS lookup goes back to the
    blocked port even when the REGISTER did not."""
    argv = dial._argv(configured, "/tmp/tx.wav", 20.0, None)
    assert "--use-tls" in argv
    assert f"--outbound={configured['registrar']}" in argv


def test_an_explicit_registrar_wins(monkeypatch, configured):
    monkeypatch.setenv("VOIP_SIP_REGISTRAR", "sip:example.net")
    assert dial.credentials()["registrar"] == "sip:example.net"


def test_status_explains_what_is_missing(monkeypatch):
    monkeypatch.delenv("VOIP_SIP_IDENTITY", raising=False)
    monkeypatch.delenv("VOIP_SIP_PASSWORD", raising=False)
    state = dial.status()
    assert state["configured"] is False
    assert state["message"]


# --------------------------------------------------------------------------
# The pjsua command line
# --------------------------------------------------------------------------

def test_the_argv_carries_the_account_and_the_file(configured):
    argv = dial._argv(configured, "/tmp/tx.wav", 20.0, None)

    assert argv[0] == "pjsua"
    assert "--play-file=/tmp/tx.wav" in argv
    assert f"--id={IDENTITY}" in argv
    assert "--password=hunter2" in argv
    # no microphone, and no silence suppression to eat the tones
    assert "--null-audio" in argv
    assert "--no-vad" in argv


def test_the_target_is_not_on_the_command_line(configured):
    """The call is placed from the console, once REGISTER has succeeded.

    As a positional argument pjsua dials the instant it starts, which can beat
    its own registration and reach nobody.
    """
    argv = dial._argv(configured, "/tmp/tx.wav", 20.0, None)
    assert PHONE not in argv


def test_auto_play_is_not_used(configured):
    """pjsua documents --auto-play as feeding *incoming* calls only.

    An outgoing call needs the player wired to the call's conference port by
    hand; relying on this flag sent a perfectly connected call full of silence.
    """
    argv = dial._argv(configured, "/tmp/tx.wav", 20.0, None)
    assert "--auto-play" not in argv


def test_forcing_a_codec_disables_the_rest(configured):
    argv = dial._argv(configured, "/tmp/tx.wav", 20.0, "GSM")
    assert "--dis-codec=*" in argv
    assert "--add-codec=GSM" in argv


# Copied out of /tmp/pjsua_*.log after a real GSM call, not paraphrased. The
# previous version of this test invented a line containing the word "codec",
# the implementation searched for that word, and the two agreed with each other
# for months while the feature returned None for every call ever placed --
# pjsua does not use the word "codec" on any line that names one.
REAL_PJSUA_LOG = """\
13:27:20.961          pjsua_call.c  .Media updates
m=audio 4000 RTP/AVP 3 120
a=rtpmap:3 GSM/8000
a=rtpmap:120 telephone-event/8000
13:27:20.963         pjsua_media.c  ......audio updated, stream #0: GSM (sendrecv)
"""


def test_the_negotiated_codec_is_read_back_out():
    """Worth surfacing every call: if GSM was not compiled into pjsua it falls
    back to PCMU, which is nearly transparent, and a clean decode over that
    proves almost nothing.

    Asserted against pjsua's real output, because that is the thing that has to
    be parsed and the thing the last version of this test got wrong.
    """
    assert dial._negotiated_codec(REAL_PJSUA_LOG) == "GSM"
    assert dial._negotiated_codec(
        "..audio updated, stream #0: PCMU (sendrecv)") == "PCMU"
    assert dial._negotiated_codec("nothing of interest here") is None


def test_dtmf_is_not_mistaken_for_the_voice_codec():
    """telephone-event rides along in every SDP there is. Reading it as the
    negotiated codec would report DTMF on a perfectly ordinary GSM call."""
    assert dial._negotiated_codec("a=rtpmap:120 telephone-event/8000") is None
    assert dial._negotiated_codec(
        "a=rtpmap:120 telephone-event/8000\na=rtpmap:3 GSM/8000") == "GSM/8000"


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_dialling_without_pjsua_says_how_to_install_it(monkeypatch, configured):
    monkeypatch.setattr(dial.shutil, "which", lambda name: None)
    with pytest.raises(VoipDependencyError, match="pjproject"):
        dial.place([0.0] * 100, PHONE)


def test_dialling_without_an_account_says_so(monkeypatch):
    monkeypatch.setattr(dial.shutil, "which", lambda name: "/usr/bin/pjsua")
    monkeypatch.delenv("VOIP_SIP_IDENTITY", raising=False)
    monkeypatch.delenv("VOIP_SIP_PASSWORD", raising=False)
    with pytest.raises(VoipDependencyError, match="subscribe.linphone.org"):
        dial.place([0.0] * 100, PHONE)


@pytest.mark.parametrize("target,match", [
    ("", "SIP address"),
    ("justaname", "needs an @"),
    (IDENTITY, "second account"),
])
def test_a_bad_target_is_refused(monkeypatch, configured, target, match):
    monkeypatch.setattr(dial.shutil, "which", lambda name: "/usr/bin/pjsua")
    with pytest.raises(VoipError, match=match):
        dial.place([0.0] * 100, target)


def test_calling_your_own_account_is_refused(monkeypatch, configured):
    """Linphone will not connect an account to itself, and the failure looks
    like a network problem rather than a mistake."""
    monkeypatch.setattr(dial.shutil, "which", lambda name: "/usr/bin/pjsua")
    with pytest.raises(VoipError, match="second account"):
        dial.place([0.0] * 100, IDENTITY)


def test_progress_of_an_unknown_call_is_none():
    assert dial.progress("nope") is None


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------

def test_the_status_endpoint_answers_either_way(client):
    body = client.get("/api/tel/dial/status").json()
    assert set(body) >= {"pjsua", "configured", "message"}
    if not (body["pjsua"] and body["configured"]):
        assert body["message"]


def test_dialling_an_expired_session_is_a_404(client):
    response = client.post("/api/tel/dial",
                           json={"session_id": "nope", "target": PHONE})
    assert response.status_code == 404


def test_an_unknown_call_id_is_a_404(client):
    assert client.get("/api/tel/dial/nope").status_code == 404


# --------------------------------------------------------------------------
# The looping player
# --------------------------------------------------------------------------

def test_the_transmission_is_padded_so_the_player_cannot_loop_into_the_call():
    """pjsua's file player restarts the WAV when it reaches the end.

    There is no way to turn that off from the command line - `--play-file` has
    no no-loop option and `--auto-play-hangup` only applies to `--auto-play`,
    which feeds incoming calls. The hangup comes two seconds after the last
    tone, so every call this placed recorded the head of a second transmission,
    and a recording holding the preamble twice is how a late Record turns into
    a blank picture (see voip.sync._best_with_room).

    Silence past the end is the fix: the player loops into nothing.
    """
    audio = np.zeros(8000, dtype=np.float64) + 0.5
    padded = dial._with_loop_guard(audio)

    assert len(padded) == len(audio) + int(dial.LOOP_GUARD_SECONDS * 8000)
    assert np.array_equal(padded[:len(audio)], audio)
    assert not padded[len(audio):].any()
    # long enough to cover the two seconds of margin before the hangup and the
    # time pjsua takes to act on it
    assert dial.LOOP_GUARD_SECONDS > 2.0


def test_the_padding_does_not_change_how_long_the_page_is_told_to_wait(
        monkeypatch, configured, tmp_path):
    """The silence is in the file only. `expected_seconds` is what the page
    counts down and what the sleep before the hangup is measured against, so
    padding it would leave the call up for an extra twenty seconds of nothing.
    """
    monkeypatch.setattr(dial, "available", lambda: True)
    started = {}
    monkeypatch.setattr(dial.threading, "Thread",
                        lambda target, args, daemon: type(
                            "T", (), {"start": lambda self: started.update(
                                wav=args[3], seconds=args[4])})())

    audio = np.zeros(8000 * 5, dtype=np.float64)
    call_id = dial.place(audio, "sip:phone@example.org", codec="GSM")
    try:
        assert dial.progress(call_id)["expected_seconds"] == 5.0
        assert started["seconds"] == 5.0

        import wave
        with wave.open(started["wav"]) as written:
            on_disk = written.getnframes() / written.getframerate()
        assert on_disk == pytest.approx(5.0 + dial.LOOP_GUARD_SECONDS, abs=0.01)
    finally:
        try:
            os.unlink(started["wav"])
        except OSError:
            pass
        dial._LOCK.release()


def test_hanging_up_does_not_depend_on_which_call_pjsua_thinks_is_current():
    """`h` hangs up the current call, and which one that is comes from pjsua's
    own cursor. `ha` hangs up all of them, which is the same thing here with
    one assumption fewer."""
    source = inspect.getsource(dial._run)
    assert 'console.say("ha")' in source
    assert 'console.say("h")' not in source
