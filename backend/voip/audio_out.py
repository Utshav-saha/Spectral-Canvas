"""Playing the transmission into a call that a softphone placed.

There are two ways to get tx.wav onto a real call.

**Dialling it ourselves** (`voip/dial.py`) needs `pjsua`, which exists on
macOS (`brew install pjproject`) and on most Linux distributions, but is
packaged for Windows by neither MSYS2 nor winget - it would have to be built
from the pjproject source tree.

**Playing it into a softphone's microphone** works everywhere. Install a
virtual audio cable, point the softphone's microphone at it, and play the WAV
into that device instead of the speakers. The softphone then sends our tones
as if someone were speaking them. The SIP signalling, the RTP packets, the
codec, the jitter buffer and the phone's recording are all real; only the
dialling is done by hand, in the softphone's own window.

    Windows   VB-CABLE            https://vb-audio.com/Cable/   (free)
              or Voicemeeter, which installs the same driver
    macOS     brew install blackhole-2ch
    Linux     pactl load-module module-null-sink

This module is the second route, made to work the same way on all three.
`voip/call/fallback.py` is the original macOS-only version and the CLI still
uses it; this one is what the app calls.
"""

import os
import sys
import threading
import time
import uuid

import numpy as np

from voip.config import SAMPLE_RATE, VoipDependencyError, VoipError

# Substrings to look for, best first. A virtual cable presents an *output*
# that some other application reads as an input, so these are output names.
VIRTUAL_DEVICES = {
    "win32": ["CABLE Input", "VB-Audio", "Voicemeeter Input", "Voicemeeter Aux"],
    "darwin": ["BlackHole", "Soundflower", "Loopback Audio"],
    "linux": ["Null Output", "Monitor of Null", "pulse", "default"],
}

INSTALL_HINT = {
    "win32": ("Install VB-CABLE from https://vb-audio.com/Cable/ - it is free, "
              "and it adds a 'CABLE Input' output plus a 'CABLE Output' input. "
              "Then set the softphone's microphone to 'CABLE Output'."),
    "darwin": ("Install it with: brew install blackhole-2ch. Then set the "
               "softphone's microphone to 'BlackHole 2ch'."),
    "linux": ("Create one with: pactl load-module module-null-sink "
              "sink_name=voip. Then point the softphone's microphone at its "
              "monitor source."),
}

CHECKLIST = [
    "Install a virtual audio cable and a softphone (Linphone Desktop).",
    "In the softphone: set the microphone to the cable, and turn echo "
    "cancellation and noise suppression OFF - both are built to remove "
    "steady tones.",
    "Restrict the softphone's codec list, so you know what you measured.",
    "Call your phone from the softphone, by hand.",
    "On the phone: answer, MUTE its microphone, press Record.",
    "Press Play below, and leave both apps alone until it finishes.",
    "Stop recording on the phone, send the file to yourself, and load it on "
    "the Receive tab.",
]

_LOCK = threading.Lock()
_PLAYS = {}


def platform_key():
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _sounddevice():
    try:
        import sounddevice
    except ImportError as exc:
        raise VoipDependencyError(
            "Playing into a call needs the sounddevice package: "
            "pip install sounddevice"
        ) from exc
    return sounddevice


def available():
    try:
        _sounddevice()
        return True
    except VoipDependencyError:
        return False


def list_devices():
    """Every output device, with the likely virtual cables marked."""
    sd = _sounddevice()
    wanted = VIRTUAL_DEVICES[platform_key()]

    out = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] <= 0:
            continue
        name = device["name"]
        match = next((w for w in wanted if w.lower() in name.lower()), None)
        out.append({
            "index": index,
            "name": name,
            "channels": int(device["max_output_channels"]),
            "sample_rate": int(device["default_samplerate"]),
            # a cable is what you want; the speakers are almost never right,
            # because the phone would then be recording the room
            "virtual": match is not None,
            "rank": wanted.index(match) if match else len(wanted),
        })
    out.sort(key=lambda d: (d["rank"], d["index"]))
    return out


def status():
    key = platform_key()
    if not available():
        return {"ready": False, "devices": [], "platform": key,
                "message": "Playing into a call needs the sounddevice package: "
                           "pip install sounddevice"}

    devices = list_devices()
    virtual = [d for d in devices if d["virtual"]]
    return {
        "ready": bool(virtual),
        "platform": key,
        "devices": devices,
        "suggested": virtual[0]["index"] if virtual else None,
        "checklist": CHECKLIST,
        "message": None if virtual else (
            "No virtual audio cable was found, so anything played would go to "
            "the speakers and the phone would record the room. "
            + INSTALL_HINT[key]),
    }


def resolve_device(device=None):
    """An index, a name fragment, or None to pick the best virtual cable."""
    devices = list_devices()
    if not devices:
        raise VoipError("This machine reports no audio output devices at all.")

    if isinstance(device, int) or (isinstance(device, str) and device.isdigit()):
        index = int(device)
        for found in devices:
            if found["index"] == index:
                return found
        raise VoipError(f"There is no output device with index {index}.")

    if device:
        for found in devices:
            if str(device).lower() in found["name"].lower():
                return found
        names = ", ".join(d["name"] for d in devices)
        raise VoipError(f"No output device matching {device!r}. Available: {names}")

    for found in devices:
        if found["virtual"]:
            return found
    raise VoipError(
        "No virtual audio cable was found, and playing to the speakers would "
        "just have the phone record the room. " + INSTALL_HINT[platform_key()])


def _run(play_id, audio, target, lead_in):
    sd = _sounddevice()
    from scipy.signal import resample_poly

    started = time.time()
    try:
        rate = target["sample_rate"]
        block = np.asarray(audio, dtype=np.float64)
        if rate != SAMPLE_RATE:
            # Virtual cables generally run at 44.1 or 48 kHz and will not open
            # an 8 kHz stream. Interpolating up costs the signal nothing: the
            # softphone resamples back down for the codec, and the decoder only
            # ever compares tone bins.
            divisor = int(np.gcd(rate, SAMPLE_RATE))
            block = resample_poly(block, rate // divisor, SAMPLE_RATE // divisor)

        channels = min(2, target["channels"])
        frames = np.tile(block[:, None], (1, channels)).astype(np.float32)

        if lead_in:
            _PLAYS[play_id]["state"] = "counting down"
            time.sleep(float(lead_in))

        _PLAYS[play_id]["state"] = "playing"
        sd.play(frames, samplerate=rate, device=target["index"], blocking=True)

        _PLAYS[play_id].update(
            state="done", seconds=round(time.time() - started, 1),
            message="Stop recording on the phone now, then load the file on "
                    "the Receive tab.")
    except Exception as exc:
        _PLAYS[play_id].update(state="failed", error=str(exc),
                               seconds=round(time.time() - started, 1))
    finally:
        _LOCK.release()


def play(audio, device=None, lead_in=3.0):
    """Start playing into the cable. Returns at once; poll progress()."""
    target = resolve_device(device)

    if not _LOCK.acquire(blocking=False):
        raise VoipError("Something is already playing. Wait for it to finish.")
    try:
        seconds = len(audio) / float(SAMPLE_RATE)
        play_id = uuid.uuid4().hex[:16]
        _PLAYS[play_id] = {
            "state": "starting", "device": target["name"],
            "device_index": target["index"], "device_sample_rate": target["sample_rate"],
            "expected_seconds": round(seconds, 1), "lead_in": float(lead_in),
            "started": time.time(),
        }
        threading.Thread(target=_run, args=(play_id, audio, target, lead_in),
                         daemon=True).start()
        return play_id
    except Exception:
        _LOCK.release()
        raise


def stop():
    """Cut a transmission short. The call itself is not ours to hang up."""
    try:
        _sounddevice().stop()
        return True
    except Exception:
        return False


def progress(play_id):
    found = _PLAYS.get(play_id)
    if found is None:
        return None
    out = dict(found)
    out["elapsed"] = round(time.time() - out.pop("started"), 1)
    return out
