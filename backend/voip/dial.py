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
import pty
import socket
import re
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

# Every state that means the call has not finished yet.
RUNNING_STATES = frozenset(
    {"dialling", "registering", "ringing", "waiting", "answered", "playing"})


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

# linphone.org answers TLS here, and 443 survives networks that block 5060.
DEFAULT_TLS_PORT = 443

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
        # sip:you@sip.linphone.org -> sips on 443, not sip on 5060.
        #
        # Measured, not assumed: plenty of networks (campus, office, some home
        # ISPs) drop SIP's own ports. A REGISTER to UDP 5060 then gets no reply
        # at all, and pjsua reports that as a 503 after ~18 s, which reads like
        # the server is down when it is the network. linphone.org publishes
        # `_sips._tcp ... 443` for exactly this case, and 443 is open nearly
        # everywhere. Override with VOIP_SIP_REGISTRAR if your network is kinder.
        host = identity.split("@")[-1].split(":")[0]
        registrar = f"sip:{host}:{DEFAULT_TLS_PORT};transport=tls"

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


# How long REGISTER is given before the call is placed. Dialling before the
# account is registered is how this used to reach nobody at all.
REGISTER_TIMEOUT = 25.0

# How long the phone is allowed to ring.
ANSWER_TIMEOUT = 60.0

# After it is answered, the person still has to mute the microphone and press
# Record. Not a tone goes out until this has passed.
ANSWER_GRACE = 8.0

# How long to sit waiting for the phone to call us, in answer mode. Long enough
# to pick the phone up, find the app and dial.
WAIT_TIMEOUT = 180.0


def _free_port():
    """A SIP port nothing else holds.

    pjsua defaults to 5060, and so does every other SIP application - Linphone
    Desktop on the same machine binds it, and so does the loopback rehearsal.
    Sharing it means one of them silently fails to start its transport.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("", 0))
        return probe.getsockname()[1]


def _argv(creds, wav, duration, codec, answering=False, local_port=None):
    """pjsua's flags.

    Two things are deliberately absent. There is no positional target, because
    the call is placed from the console only once REGISTER has succeeded. And
    there is no `--auto-play`, because pjsua documents it as feeding *incoming*
    calls only -- an outgoing one needs the player wired into the conference
    bridge by hand, which `_run` does with `cc`. Both omissions are the reason
    this module used to ring nobody.
    """
    argv = [
        "pjsua",
        "--null-audio",                 # no microphone; the file is the source
        "--no-vad",                     # silence suppression would eat the tones
        f"--play-file={wav}",           # registers it as a conference port
        f"--id={creds['identity']}",
        f"--registrar={creds['registrar']}",
        # Without --use-tls there is no TLS transport to send over, and a
        # `transport=tls` URI fails with "Socket is not connected".
        "--use-tls",
        # The INVITE has to take the same road as the REGISTER. Left to its own
        # DNS lookup it would try UDP 5060 for the callee and reach nobody on a
        # network that blocks it.
        f"--outbound={creds['registrar']}",
        f"--realm={creds['realm']}",
        f"--username={creds['username']}",
        f"--password={creds['password']}",
        "--app-log-level=4",            # 3 is too quiet to see the state changes
        f"--local-port={local_port or _free_port()}",
        # a call that hangs should not hold the line open indefinitely
        f"--duration={int(duration + ANSWER_TIMEOUT + ANSWER_GRACE + 30)}",
    ]
    if answering:
        # The phone calls us. An outgoing call from a handset always works;
        # an incoming one depends on push notifications the free service does
        # not reliably deliver, which is the whole reason this mode exists.
        argv.append("--auto-answer=200")
    if codec:
        # Restrict to one codec so the log cannot quietly negotiate something
        # transparent and make a clean decode look meaningful.
        argv += ["--dis-codec=*", f"--add-codec={codec}"]
    return argv


class _Console:
    """pjsua driven over a pty.

    Not a pipe: with its output redirected to a pipe or a file, pjsua's libc
    block-buffers it, so the line we are waiting on can sit unflushed in its
    buffer for longer than the timeout. A pty makes it line-buffered, which is
    the difference between seeing `CONFIRMED` when it happens and concluding
    nobody answered while the call is already up.
    """

    def __init__(self, argv, log):
        master, slave = pty.openpty()
        self._master = master
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=slave, stderr=slave,
            text=True, bufsize=1, close_fds=True)
        os.close(slave)
        self.text = ""
        self._log = log
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            try:
                chunk = os.read(self._master, 4096)
            except OSError:
                break
            if not chunk:
                break
            piece = chunk.decode("utf-8", "replace")
            with self._lock:
                self.text += piece
            try:
                self._log.write(piece)
            except ValueError:
                break

    def snapshot(self):
        with self._lock:
            return self.text

    def say(self, command):
        """pjsua quits the moment its stdin closes, so the pipe is held open
        for the whole call and every instruction goes down it."""
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def wait(self, pattern, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if re.search(pattern, self.snapshot(), re.I):
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.2)
        return False

    def close(self):
        try:
            os.close(self._master)
        except OSError:
            pass


def _ports(log_text, wav):
    """(player port, call port) out of a `cl` listing.

    The call's port only exists once it is answered, and it is never #2 -- that
    is the ringback tone, which is what a fixed guess would have played.
    """
    player = call = None
    for line in log_text.splitlines():
        found = re.match(r"\s*Port #(\d+)\[", line)
        if not found:
            continue
        number = int(found.group(1))
        if wav in line:
            player = number
        elif "sip:" in line.lower():
            call = number
    return player, call


def _run(call_id, creds, target, wav, duration, codec):
    started = time.time()
    log = tempfile.NamedTemporaryFile(prefix="pjsua-", suffix=".log",
                                      delete=False, mode="w+", encoding="utf-8",
                                      errors="replace")
    console = None
    try:
        local_port = _free_port()
        _CALLS[call_id]["local_port"] = local_port
        console = _Console(
            _argv(creds, wav, duration, codec, answering=target is None,
                  local_port=local_port), log)
        _CALLS[call_id]["pid"] = console.proc.pid

        _CALLS[call_id]["state"] = "registering"
        if not console.wait(r"registration success", REGISTER_TIMEOUT):
            log = console.snapshot()
            if re.search(r"\b(401|403|Forbidden|Unauthorized)\b", log, re.I):
                raise VoipError(
                    "The registrar rejected the account: the password is "
                    "wrong. Retype it with `read -s VOIP_SIP_PASSWORD` and "
                    "restart the backend - a stray space or newline counts.")
            if re.search(r"Socket is not connected|503|timed? ?out", log, re.I):
                raise VoipError(
                    f"No reply from {creds['registrar']}. That is almost "
                    "always the network blocking SIP rather than the account: "
                    "check that TCP 443 is reachable from here. Override the "
                    "route with VOIP_SIP_REGISTRAR if your provider uses a "
                    "different port.")
            raise VoipError(
                "The SIP account never registered. Check VOIP_SIP_IDENTITY and "
                f"VOIP_SIP_PASSWORD, and that this machine can reach "
                f"{creds['registrar']}.")

        if target is None:
            _CALLS[call_id]["state"] = "waiting"
            if not console.wait(r"state changed to CONFIRMED", WAIT_TIMEOUT):
                raise VoipError(
                    f"No call arrived within {int(WAIT_TIMEOUT)}s. Dial "
                    f"{creds['identity']} from the Linphone app on the phone "
                    "while this is waiting.")
        else:
            _CALLS[call_id]["state"] = "ringing"
            console.say("m")
            console.say(target)
            if not console.wait(r"state changed to CONFIRMED", ANSWER_TIMEOUT):
                raise VoipError(_why_it_never_connected(console.snapshot(), target))

        # Answered. Time to mute the microphone and press Record.
        _CALLS[call_id]["state"] = "answered"
        _CALLS[call_id]["grace_seconds"] = ANSWER_GRACE
        time.sleep(ANSWER_GRACE)

        console.say("cl")
        time.sleep(1.0)
        player, port = _ports(console.snapshot(), wav)
        if player is None or port is None:
            raise VoipError("The call connected, but its audio port could not "
                            "be found in the conference bridge, so nothing was "
                            "played.")
        console.say(f"cc {player} {port}")
        _CALLS[call_id]["state"] = "playing"

        time.sleep(duration + 2.0)
        console.say("h")
        time.sleep(1.0)
        console.say("q")
        try:
            console.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            console.proc.kill()
            console.proc.wait(timeout=5)

        text = console.snapshot()
        _CALLS[call_id].update(
            state="done",
            returncode=console.proc.returncode,
            seconds=round(time.time() - started, 1),
            negotiated=_negotiated_codec(text),
            log_tail=text[-4000:],
        )
    except Exception as exc:
        if console is not None and console.proc.poll() is None:
            console.proc.kill()
        _CALLS[call_id].update(
            state="failed", error=str(exc),
            seconds=round(time.time() - started, 1),
            log_tail=(console.snapshot()[-4000:] if console else ""))
    finally:
        if console is not None:
            console.close()
        log.close()
        for path in (log.name, wav):
            try:
                os.unlink(path)
            except OSError:
                pass


def _responses(log_text):
    """Every SIP response the far end sent back, in order."""
    return re.findall(r"^SIP/2\.0 (\d{3}) ([^\r\n]*)", log_text, re.M)


def _why_it_never_connected(log_text, target):
    """Turn a failed INVITE into the one sentence that identifies the cause.

    "Nobody answered" covers three completely different problems and sends
    people to fix the wrong one. The response codes tell them apart: whether
    the phone was ever alerted at all (180), whether it was reached and said no
    (486/603), and whether it was reached but could not agree on the media
    (488), which is not a ringing problem in the slightest.
    """
    codes = [code for code, _ in _responses(log_text)]
    rang = "180" in codes or "183" in codes
    last = next((f"{c} {r.strip()}" for c, r in reversed(_responses(log_text))
                 if c not in ("100", "180", "183")), None)

    if "488" in codes:
        return (f"{target} was reached but refused the audio (488 Not "
                "Acceptable Here). That is a media mismatch, not a ringing "
                "problem: in the phone's Linphone settings set Media "
                "encryption to None, and enable the PCMU codec.")
    if "486" in codes:
        return f"{target} is busy (486). Clear the call on the phone and retry."
    if "603" in codes or "decline" in log_text.lower():
        return f"The call to {target} was declined (603) from the phone."
    if "404" in codes or "480" in codes:
        return (f"{target} is registered nowhere the server can see "
                f"({last or '404/480'}). Check the phone's Linphone app is "
                "signed in to that exact account, and that the address is "
                "spelled the way the app shows it.")
    if rang:
        return (f"{target} was alerted - the phone's SIP stack answered 180 "
                "Ringing - but the call was never picked up within "
                f"{int(ANSWER_TIMEOUT)}s. If the handset stayed silent, that is "
                "the ringer: Linphone on a phone only rings reliably while the "
                "app is open in the foreground, and needs push notifications "
                "otherwise. Try the answer mode instead, where the phone calls "
                "this machine.")
    return (f"Nobody answered {target}"
            + (f" (last response: {last})" if last else
               " - and the phone's SIP stack never even sent 180 Ringing, so "
               "the call was not presented on the handset at all")
            + ". Linphone on a phone only rings reliably while the app is open "
              "in the foreground. The answer mode, where the phone calls this "
              "machine instead, does not depend on that.")


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

    return _start(creds, target, audio, duration, codec)


def answer(audio, duration=None, codec=None):
    """Wait for the phone to call *us*, then play the transmission into it.

    The reliable direction. A call placed *to* a handset only alerts it if the
    app is in the foreground or push notifications get through, and on the free
    service they often do not - the call lands in the history having never
    rung. A call placed *from* the handset has neither problem, and reaches the
    identical channel: same account, same registrar, same codec, and the phone
    still makes the recording with its own Record button.
    """
    if not available():
        raise VoipDependencyError(MISSING_PJSUA)
    creds = credentials()
    if creds is None:
        raise VoipDependencyError(MISSING_CREDENTIALS)
    return _start(creds, None, audio, duration, codec)


def _start(creds, target, audio, duration, codec):
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
            "state": "dialling" if target else "waiting",
            "target": target or f"waiting for a call to {creds['identity']}",
            "dial_this": None if target else creds["identity"],
            "started": time.time(),
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
    if out["state"] in RUNNING_STATES:
        out["elapsed"] = round(time.time() - out["started"], 1)
    out.pop("started", None)
    return out
