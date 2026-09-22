"""Placing a real call with pjsua, driven from the app.

The liblinphone Python bindings are not on PyPI for any platform and have to
be compiled from the SDK source, which is why `voip/call/session.py` has never
been run against a real SDK. None of that is necessary: Linphone is a SIP
client, `sip.linphone.org` is an ordinary SIP registrar, and the Linphone app
on a phone answers a call from any SIP client at all.

pjsua is already a dependency here -- `spectral/tel/run_local_call.sh` uses two
instances of it on loopback for the rehearsal. This module points one instance
at a real account instead, plays tx.wav into the call, and hangs up.

    macOS    brew install pjproject
    Linux    the pjproject / pjsua2 package
    Windows  **not packaged**, by MSYS2 or winget. Use `voip/audio_out.py`
             instead: a virtual audio cable plus a softphone reaches the same
             channel with no SIP stack here at all, and only the dialling is
             done by hand.

Credentials come from the environment, never from a request body:

    export VOIP_SIP_IDENTITY=sip:you-laptop@sip.linphone.org
    export VOIP_SIP_PASSWORD=...        # read -s, do not put it in a file
    export VOIP_SIP_REGISTRAR=sip:sip.linphone.org      # optional

You need two accounts: one here and one signed in to the app on the phone.
Calling the account you are calling *from* does not work.

What this cannot do is bring the audio back. pjsua can record its inbound leg,
but that is the phone's microphone -- which you will have muted -- not the
tones the phone received. To measure what the codec did on the way down, the
recording has to be made on the phone with Linphone's in-call Record button,
then uploaded. No app can capture another app's call audio: Android blocks it
and iOS does not allow it at all.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from voip.audio_io import write_int16_wav
from voip.config import SAMPLE_RATE, VoipDependencyError, VoipError

# One dial at a time. A second pjsua would fight the first for the account
# registration, and the machine has one voice line either way.
_LOCK = threading.Lock()
_CALLS = {}


def _missing_pjsua():
    """Said differently per platform, because the answer differs a lot.

    Windows has no pjsua package on MSYS2 or winget; building pjproject from
    source is the only way to get one, so the honest advice there is to use
    the playback route instead, which needs no SIP stack at all.
    """
    if sys.platform.startswith("win"):
        return (
            "pjsua is not available on Windows - neither MSYS2 nor winget "
            "packages it, and building pjproject from source is a project in "
            "itself. Use the playback route instead: install VB-CABLE and a "
            "softphone, place the call by hand, and press Play. It reaches "
            "exactly the same channel; only the dialling is manual."
        )
    if sys.platform == "darwin":
        return (
            "pjsua is not installed, so no call can be placed from here. "
            "brew install pjproject. If `which pjsua` is still empty "
            "afterwards, the formula shipped it under a versioned name - see "
            "the header of spectral/tel/run_local_call.sh."
        )
    return (
        "pjsua is not installed, so no call can be placed from here. It is "
        "packaged as pjproject or pjsua2 on most distributions."
    )


MISSING_PJSUA = _missing_pjsua()

MISSING_CREDENTIALS = (
    "No SIP account is configured on this server. Set VOIP_SIP_IDENTITY and "
    "VOIP_SIP_PASSWORD in the backend's environment and restart it. Get a free "
    "account at https://subscribe.linphone.org - you need two, one here and "
    "one signed in to the Linphone app on your phone."
)


def available():
    return shutil.which("pjsua") is not None


def credentials():
    """(identity, password, registrar, realm) from the environment, or None.

    Never from a request: a password that arrives over HTTP ends up in access
    logs, in the browser's network panel and in anything that proxies /api.
    """
    identity = os.environ.get("VOIP_SIP_IDENTITY", "").strip()
    password = os.environ.get("VOIP_SIP_PASSWORD", "")
    if not identity or not password:
        return None

    registrar = os.environ.get("VOIP_SIP_REGISTRAR", "").strip()
    if not registrar:
        # sip:you@sip.linphone.org -> sip:sip.linphone.org
        host = identity.split("@")[-1]
        registrar = f"sip:{host}"

    realm = os.environ.get("VOIP_SIP_REALM", "*")
    username = os.environ.get("VOIP_SIP_USERNAME", "").strip()
    if not username:
        username = identity.split(":", 1)[-1].split("@")[0]

    return {"identity": identity, "password": password, "registrar": registrar,
            "realm": realm, "username": username}


def status():
    """Whether this machine can dial, and if not, what to do about it.

    `playback_instead` is the important field on Windows: there is no pjsua to
    install, so the UI should offer the other route rather than a dead end.
    """
    creds = credentials()
    have_pjsua = available()
    return {
        "pjsua": have_pjsua,
        "configured": creds is not None,
        "platform": sys.platform,
        "identity": creds["identity"] if creds else None,
        "registrar": creds["registrar"] if creds else None,
        "playback_instead": not have_pjsua,
        "message": (None if have_pjsua and creds else
                    _missing_pjsua() if not have_pjsua else MISSING_CREDENTIALS),
    }


def _argv(creds, target, wav, duration, codec):
    """pjsua's flags, in the order run_local_call.sh already proved works."""
    argv = [
        "pjsua",
        "--null-audio",                 # no microphone; the file is the source
        "--no-vad",                     # silence suppression would eat the tones
        "--auto-play",
        f"--play-file={wav}",
        f"--id={creds['identity']}",
        f"--registrar={creds['registrar']}",
        f"--realm={creds['realm']}",
        f"--username={creds['username']}",
        f"--password={creds['password']}",
        "--app-log-level=3",
    ]
    if codec:
        # Restrict to one codec so the log cannot quietly negotiate something
        # transparent and make a clean decode look meaningful.
        argv += ["--dis-codec=*", f"--add-codec={codec}"]
    argv.append(target)
    return argv


def _run(call_id, creds, target, wav, duration, codec):
    started = time.time()
    log = tempfile.NamedTemporaryFile(prefix="pjsua-", suffix=".log",
                                      delete=False, mode="w+", encoding="utf-8",
                                      errors="replace")
    try:
        proc = subprocess.Popen(
            _argv(creds, target, wav, duration, codec),
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
            text=True,
        )
        _CALLS[call_id]["pid"] = proc.pid

        # pjsua quits when its stdin closes, so drive it the way the loopback
        # script does: wait out the transmission, hang up, then quit.
        try:
            proc.communicate(input="\n", timeout=duration + 20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()

        log.seek(0)
        text = log.read()
        _CALLS[call_id].update(
            state="done",
            returncode=proc.returncode,
            seconds=round(time.time() - started, 1),
            negotiated=_negotiated_codec(text),
            log_tail=text[-4000:],
        )
    except Exception as exc:
        _CALLS[call_id].update(state="failed", error=str(exc),
                               seconds=round(time.time() - started, 1))
    finally:
        log.close()
        for path in (log.name, wav):
            try:
                os.unlink(path)
            except OSError:
                pass


def _negotiated_codec(log_text):
    """Which codec actually got used.

    Worth surfacing every time: if GSM was not compiled into pjsua it falls
    back to PCMU, which is nearly transparent, and a clean decode over that
    proves almost nothing about surviving a real call.
    """
    for line in log_text.splitlines():
        low = line.lower()
        if "codec" in low and ("gsm" in low or "pcmu" in low or "pcma" in low
                               or "ilbc" in low or "opus" in low or "speex" in low):
            return line.strip()[:200]
    return None


def place(audio, target, duration=None, codec=None):
    """Dial `target` and play `audio` into it. Returns immediately.

    The call runs on a worker thread because it lasts as long as the
    transmission does; the page polls `progress(call_id)`.
    """
    if not available():
        raise VoipDependencyError(MISSING_PJSUA)
    creds = credentials()
    if creds is None:
        raise VoipDependencyError(MISSING_CREDENTIALS)

    target = (target or "").strip()
    if not target:
        raise VoipError("Enter the SIP address of the phone to call, for "
                        "example sip:yourphone@sip.linphone.org.")
    if not target.startswith("sip:"):
        target = f"sip:{target}"
    if "@" not in target:
        raise VoipError("A SIP address needs an @, for example "
                        "sip:yourphone@sip.linphone.org.")
    if target.rstrip("/") == creds["identity"].rstrip("/"):
        raise VoipError("That is the account this server calls from. Use the "
                        "second account, the one signed in on your phone.")

    if not _LOCK.acquire(blocking=False):
        raise VoipError("A call is already in progress. Wait for it to finish, "
                        "or hang up on the phone.")
    try:
        handle, path = tempfile.mkstemp(prefix="tx-", suffix=".wav")
        os.close(handle)
        write_int16_wav(path, audio, SAMPLE_RATE)

        seconds = duration or (len(audio) / float(SAMPLE_RATE))
        call_id = uuid.uuid4().hex[:16]
        _CALLS[call_id] = {
            "state": "dialling", "target": target, "started": time.time(),
            "expected_seconds": round(seconds, 1), "codec": codec,
        }

        worker = threading.Thread(
            target=_finish, args=(call_id, creds, target, path, seconds, codec),
            daemon=True)
        worker.start()
        return call_id
    except Exception:
        _LOCK.release()
        raise


def _finish(call_id, creds, target, wav, seconds, codec):
    try:
        _run(call_id, creds, target, wav, seconds, codec)
    finally:
        _LOCK.release()


def progress(call_id):
    call = _CALLS.get(call_id)
    if call is None:
        return None
    out = dict(call)
    out.pop("pid", None)
    if out["state"] == "dialling":
        out["elapsed"] = round(time.time() - out["started"], 1)
    out.pop("started", None)
    return out
