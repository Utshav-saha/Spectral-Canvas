"""Placing a real SIP call with pjsua.

Nothing here dials anything. What is worth protecting without a phone on the
other end is the argument building and the refusals, because those are what
stand between a user and a silently wrong call: the wrong account, a password
in a request body, or pjsua quietly negotiating a transparent codec.
"""

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


def test_the_negotiated_codec_is_read_back_out():
    """Worth surfacing every call: if GSM was not compiled into pjsua it falls
    back to PCMU, which is nearly transparent, and a clean decode over that
    proves almost nothing."""
    log = "13:20:01 pjsua_media.c Media session audio codec GSM @8000Hz\n"
    assert "GSM" in dial._negotiated_codec(log)
    assert dial._negotiated_codec("nothing of interest here") is None


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
