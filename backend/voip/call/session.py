"""Register, dial, play the transmission into the call, hang up.

The shape of this follows liblinphone's own model: create a Core, attach a
listener, and pump ``core.iterate()` every 20 ms so the engine can run. There
is no blocking wait anywhere -- a call that is not iterated is a call that does
nothing.

Four details turn a voice call into a data call, and all four matter:

*Echo cancellation, AGC and noise suppression go off.* An echo canceller is
built to remove a steady tone that resembles something it has already heard,
which is an exact description of our preamble. A noise suppressor classifies a
pure tone as a fan. AGC rewrites amplitudes, which we do not depend on but
which does move the signal around. These are not optimisations; leaving them on
is the single most likely reason a real call decodes to nothing.

*The microphone is never opened.* ``use_files`` makes liblinphone read from a
file instead of a capture device, so no CoreAudio permission prompt appears and
no room noise lands on top of the tones.

*One codec is enabled.* If several are offered, the far end picks, and a
transmission that decodes perfectly over Opus at 48 kHz proves almost nothing
because that channel is nearly transparent. Forcing PCMU means you know which
channel you measured. The negotiated codec is logged loudly for the same reason
``run_local_call.sh`` shouts about it.

*The file is played only once the phone is recording*, which is why there is a
ready gate before the player starts.

Every attribute name here is unverified -- see sdk.py. Run
``voip.cli check-env`` on the real machine first.
"""

import os
import select
import sys
import time
import wave
from dataclasses import dataclass, field

from voip.call import sdk
from voip.config import (
    CALL_SCHEMA,
    DEFAULT_CODEC,
    DEFAULT_SIP_DOMAIN,
    DEFAULT_STUN_SERVER,
    ENV_SIP_DIAL,
    ENV_SIP_DOMAIN,
    ENV_SIP_IDENTITY,
    ENV_SIP_PASSWORD,
    CallError,
    VoipError,
)

ITERATE_INTERVAL = 0.02          # 20 ms, one RTP packet


@dataclass
class CallConfig:
    wav_path: str
    dial: str
    identity: str
    password: str
    domain: str = DEFAULT_SIP_DOMAIN
    codec: str = DEFAULT_CODEC
    stun: str | None = DEFAULT_STUN_SERVER
    ice: bool = True
    dsp_off: bool = True
    use_player: bool = True
    ready: str = "enter"
    register_timeout: float = 30.0
    answer_timeout: float = 60.0

    @classmethod
    def from_args(cls, args, wav_path):
        identity = args.identity or os.environ.get(ENV_SIP_IDENTITY)
        password = args.password or os.environ.get(ENV_SIP_PASSWORD)
        dial = args.dial or os.environ.get(ENV_SIP_DIAL)
        domain = args.domain or os.environ.get(ENV_SIP_DOMAIN) or DEFAULT_SIP_DOMAIN

        missing = [name for name, value in
                   (("--identity / " + ENV_SIP_IDENTITY, identity),
                    ("--password / " + ENV_SIP_PASSWORD, password),
                    ("--dial / " + ENV_SIP_DIAL, dial)) if not value]
        if missing:
            raise VoipError(
                "Missing SIP settings: " + ", ".join(missing) +
                ".\nKeep the password out of your shell history:\n"
                f"  read -s {ENV_SIP_PASSWORD} && export {ENV_SIP_PASSWORD}"
            )

        if not str(identity).startswith("sip:"):
            identity = f"sip:{identity}@{domain}"
        if not str(dial).startswith("sip:"):
            dial = f"sip:{dial}@{domain}"

        return cls(
            wav_path=wav_path, dial=dial, identity=identity, password=password,
            domain=domain, codec=args.codec,
            stun=None if args.no_stun else (args.stun or DEFAULT_STUN_SERVER),
            ice=not args.no_ice, dsp_off=not args.keep_dsp,
            use_player=not args.play_file, ready=args.ready,
            answer_timeout=args.answer_timeout,
        )

    @property
    def username(self):
        return self.identity.split(":", 1)[1].split("@", 1)[0]

    def redacted(self):
        return {"identity": self.identity, "dial": self.dial, "domain": self.domain,
                "codec_requested": self.codec, "stun": self.stun, "ice": self.ice,
                "dsp_off": self.dsp_off,
                "strategy": "player" if self.use_player else "play_file",
                "ready": self.ready}


@dataclass
class CallEvents:
    registration: str = "none"
    call_state: str = "none"
    streams_running: bool = False
    ended: bool = False
    failed: bool = False
    message: str = ""
    timeline: list = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def note(self, event, detail=""):
        self.timeline.append({
            "t": round(time.monotonic() - self.started, 3),
            "event": event, "detail": detail,
        })
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {event}{(' ' + detail) if detail else ''}", flush=True)


class CallSession:
    """One outgoing call, guaranteed to hang up and stop the core."""

    def __init__(self, config, run_dir=None):
        self.config = config
        self.run_dir = run_dir
        self.warnings = []
        self.events = CallEvents()
        self.linphone = None
        self.factory = None
        self.core = None
        self.listener = None          # must outlive the call: the SDK holds a weak ref
        self.account = None
        self.call = None
        self._enabled_codecs = []

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            if self.call is not None:
                sdk.try_call(self.call, "terminate", warnings=self.warnings)
                self.pump(3.0, until=lambda: self.events.ended)
        finally:
            if self.core is not None:
                sdk.try_call(self.core, "stop", warnings=self.warnings)
        return False

    def pump(self, seconds, until=None):
        """Run liblinphone's loop. Returns True if `until` came true."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.core.iterate()
            if until is not None and until():
                return True
            time.sleep(ITERATE_INTERVAL)
        return until() if until is not None else False

    # -- setup -------------------------------------------------------------

    def start(self):
        self.linphone = sdk.load()
        self.factory = self.linphone.Factory.get()
        self.core = self.factory.create_core("", "", None)

        self.listener = self.factory.create_core_listener()
        sdk.try_set(self.listener, "on_call_state_changed", self._on_call_state,
                    self.warnings)
        sdk.try_set(self.listener, "on_account_registration_state_changed",
                    self._on_registration, self.warnings)
        sdk.try_call(self.core, "add_listener", self.listener, warnings=self.warnings)

        if self.config.dsp_off:
            self._silence_the_speech_helpers()
        self._never_open_the_microphone()
        self._force_codec()
        self._configure_nat()

        sdk.try_call(self.core, "start", warnings=self.warnings)
        self.events.note("core_started")
        return self

    def _silence_the_speech_helpers(self):
        for name in ("echo_cancellation_enabled", "agc_enabled",
                     "noise_suppression_enabled", "adaptive_rate_control_enabled"):
            sdk.try_set(self.core, name, False, self.warnings)

    def _never_open_the_microphone(self):
        sdk.try_set(self.core, "use_files", True, self.warnings)
        sdk.try_set(self.core, "play_file", "", self.warnings)
        sdk.try_set(self.core, "video_capture_enabled", False, self.warnings)
        sdk.try_set(self.core, "video_display_enabled", False, self.warnings)

    def _force_codec(self):
        wanted = self.config.codec.upper()
        payload_types = sdk.try_get(self.core, "audio_payload_types", [], self.warnings)
        if not payload_types:
            self.warnings.append("Could not read audio_payload_types; codec left as-is.")
            return

        for pt in payload_types:
            mime = str(sdk.try_get(pt, "mime_type", "", None) or "")
            enable = True if wanted == "ANY" else (mime.upper() == wanted)
            sdk.try_call(pt, "enable", enable, warnings=self.warnings)

        self._enabled_codecs = [
            str(sdk.try_get(pt, "mime_type", "?", None)) for pt in payload_types
            if sdk.try_call(pt, "enabled", warnings=None, default=False)
        ]
        self.events.note("codecs_enabled", ", ".join(self._enabled_codecs) or "(none)")
        if wanted != "ANY" and not self._enabled_codecs:
            raise CallError(
                f"No codec called {self.config.codec} is available on this build. "
                f"Offered: {[str(sdk.try_get(p, 'mime_type', '?')) for p in payload_types]}"
            )

    def _configure_nat(self):
        if not self.config.stun and not self.config.ice:
            return
        policy = sdk.try_call(self.core, "create_nat_policy", warnings=self.warnings)
        if policy is None:
            self.warnings.append("No NAT policy available; relying on a direct route.")
            return
        if self.config.stun:
            sdk.try_set(policy, "stun_server", self.config.stun, self.warnings)
            sdk.try_set(policy, "stun_enabled", True, self.warnings)
        sdk.try_set(policy, "ice_enabled", bool(self.config.ice), self.warnings)
        sdk.try_set(self.core, "nat_policy", policy, self.warnings)

    # -- listener callbacks ------------------------------------------------

    def _on_registration(self, core, account, state, message):
        name = sdk.state_name(state)
        self.events.registration = name
        self.events.note("registration", f"{name} {message or ''}".strip())

    def _on_call_state(self, core, call, state, message):
        name = sdk.state_name(state)
        self.events.call_state = name
        self.events.message = message or ""
        self.events.note("call_state", f"{name} {message or ''}".strip())
        if name == "StreamsRunning":
            self.events.streams_running = True
        if name in ("End", "Released"):
            self.events.ended = True
        if name == "Error":
            self.events.ended = True
            self.events.failed = True

    # -- the call ----------------------------------------------------------

    def register(self):
        params = sdk.try_call(self.core, "create_account_params", warnings=self.warnings)
        if params is None:
            raise CallError("This SDK build has no create_account_params().")

        sdk.try_set(params, "identity_address",
                    self.core.create_address(self.config.identity), self.warnings)
        sdk.try_set(params, "server_address",
                    self.core.create_address(f"sip:{self.config.domain};transport=tls"),
                    self.warnings)
        sdk.try_set(params, "register_enabled", True, self.warnings)

        auth = self.factory.create_auth_info(
            self.config.username, None, self.config.password, None, None,
            self.config.domain)
        sdk.try_call(self.core, "add_auth_info", auth, warnings=self.warnings)

        self.account = self.core.create_account(params)
        sdk.try_call(self.core, "add_account", self.account, warnings=self.warnings)
        sdk.try_set(self.core, "default_account", self.account, self.warnings)

        ok = self.pump(self.config.register_timeout,
                       until=lambda: self.events.registration in ("Ok", "Failed", "Cleared"))
        if not ok or self.events.registration != "Ok":
            raise CallError(
                f"Registration did not succeed (state: {self.events.registration}). "
                f"Check the username and password, that the account is activated, "
                f"and if the network blocks TLS try transport=tcp."
            )
        return {"state": self.events.registration}

    def dial(self):
        params = sdk.try_call(self.core, "create_call_params", None, warnings=self.warnings)
        if params is not None:
            sdk.try_set(params, "video_enabled", False, self.warnings)

        if not self.config.use_player:
            # play_file has to be set before the invite: it starts on connect
            sdk.try_set(self.core, "play_file", os.path.abspath(self.config.wav_path),
                        self.warnings)

        address = self.core.create_address(self.config.dial)
        self.call = self.core.invite_address_with_params(address, params)
        if self.call is None:
            raise CallError(f"Could not place a call to {self.config.dial}.")

        self.events.note("dialling", self.config.dial)
        print("\n  Answer on the phone, MUTE its microphone, then press Record.\n")

        connected = self.pump(self.config.answer_timeout,
                              until=lambda: self.events.streams_running or self.events.ended)
        if not connected or not self.events.streams_running:
            raise CallError(
                f"The call never started streaming (last state: "
                f"{self.events.call_state}). {self.events.message}"
            )
        return self._negotiated()

    def _negotiated(self):
        params = sdk.try_get(self.call, "current_params", None, self.warnings)
        pt = sdk.try_get(params, "used_audio_payload_type", None, self.warnings) if params else None
        mime = str(sdk.try_get(pt, "mime_type", "unknown", None)) if pt else "unknown"
        rate = sdk.try_get(pt, "clock_rate", None, None) if pt else None

        self.events.note("connected", f"codec {mime} @ {rate} Hz")
        if self.config.codec.upper() not in ("ANY", mime.upper()):
            warning = (f"Asked for {self.config.codec} but the call negotiated {mime}. "
                       f"A transparent codec makes a clean decode prove much less.")
            self.warnings.append(warning)
            print(f"\n  WARNING: {warning}\n")
        return {"mime_type": mime, "clock_rate": rate}

    def wait_ready(self):
        """Hold until the phone is recording, still pumping the core."""
        mode = (self.config.ready or "enter").lower()
        if mode.startswith("delay:"):
            seconds = float(mode.split(":", 1)[1])
            self.events.note("waiting", f"{seconds:.0f}s before transmitting")
            self.pump(seconds)
            return

        print("  Press ENTER once the phone is recording... ", end="", flush=True)
        while True:
            self.core.iterate()
            if self.events.ended:
                raise CallError("The call ended before the transmission started.")
            ready, _, _ = select.select([sys.stdin], [], [], ITERATE_INTERVAL)
            if ready:
                sys.stdin.readline()
                print()
                return

    def transmit(self):
        duration = _wav_seconds(self.config.wav_path)
        started = time.monotonic()

        if self.config.use_player:
            player = sdk.try_get(self.call, "player", None, self.warnings)
            if player is None:
                raise CallError(
                    "call.player is not available in this SDK build. "
                    "Re-run with --play-file, and use a longer --lead-in so there "
                    "is time to press Record before the audio starts."
                )
            sdk.try_call(player, "open", os.path.abspath(self.config.wav_path),
                         warnings=self.warnings)
            sdk.try_call(player, "start", warnings=self.warnings)

        self.events.note("transmitting", f"{duration:.1f}s")
        self.pump(duration + 2.0, until=lambda: self.events.ended)
        self.events.note("transmitted",
                         f"{time.monotonic() - started:.1f}s elapsed")
        return {"duration_seconds": round(duration, 3),
                "elapsed_seconds": round(time.monotonic() - started, 3)}

    def audio_stats(self):
        stats = sdk.try_get(self.call, "audio_stats", None, self.warnings)
        if stats is None:
            return {}
        return {name: sdk.try_get(stats, name, None, None) for name in (
            "sender_loss_rate", "receiver_loss_rate", "jitter_buffer_size_ms",
            "round_trip_delay", "upload_bandwidth", "download_bandwidth")}

    def hangup(self):
        if self.call is None:
            return
        sdk.try_call(self.call, "terminate", warnings=self.warnings)
        self.pump(5.0, until=lambda: self.events.ended)
        self.events.note("hung_up")

    # -- the whole sequence ------------------------------------------------

    def run(self, dry_run=False):
        outcome = {
            "schema": CALL_SCHEMA, "mode": "sdk", "ok": False,
            "sdk": {"available": sdk.available(), "version": sdk.version()},
            "config": self.config.redacted(),
        }
        try:
            self.start()
            outcome["registration"] = self.register()

            if dry_run:
                outcome.update(ok=True, dry_run=True, probe=sdk.probe())
                self.events.note("dry_run_complete", "registered, no call placed")
            else:
                outcome["negotiated"] = self.dial()
                self.wait_ready()
                outcome["transmit"] = self.transmit()
                outcome["audio_stats"] = self.audio_stats()
                self.hangup()
                outcome["ok"] = not self.events.failed
        except VoipError as exc:
            outcome["error"] = str(exc)
            print(f"\nerror: {exc}", file=sys.stderr)
        finally:
            outcome["enabled_codecs"] = self._enabled_codecs
            outcome["timeline"] = self.events.timeline
            outcome["warnings"] = self.warnings
        return outcome


def _wav_seconds(path):
    with wave.open(path) as handle:
        return handle.getnframes() / float(handle.getframerate())
