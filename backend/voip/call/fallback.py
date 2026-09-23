"""Placing the call by hand, when the SDK will not build.

The Python bindings are not on PyPI for macOS on Apple silicon and have to be
compiled from the liblinphone source tree. That may not work on the first day,
and the project should not stall waiting for it.

This route reaches exactly the same channel:

    brew install blackhole-2ch
    open the Linphone desktop app
    Preferences -> Audio -> input device: BlackHole 2ch
    Preferences -> Audio -> echo cancellation OFF
    Preferences -> Audio -> enable PCMU only
    place the call to the phone by hand
    python -m voip.cli call --run latest --fallback

Linphone then treats whatever this plays into BlackHole as its microphone, so
the SIP signalling, the RTP packets, the codec, the jitter buffer and the
phone's recording are all real. Only the dialling is manual, which means no
``call.json`` statistics -- there is no Core to ask.

Worth saying plainly in the write-up which route produced which result: this
one is a stopgap, not the API integration the branch is for.
"""

import os
import time
import wave

import numpy as np

from voip.config import CALL_SCHEMA, VoipDependencyError, VoipError

CHECKLIST = """
Before playing, on the phone and in the Linphone desktop app:

  [ ] desktop: microphone input set to the virtual device (BlackHole 2ch)
  [ ] desktop: echo cancellation OFF
  [ ] desktop: only PCMU (and PCMA) enabled in the codec list
  [ ] phone:   answer the call
  [ ] phone:   MUTE the phone's microphone
  [ ] phone:   press the in-call Record button
  [ ] both:    leave the apps in the foreground for the whole transmission
"""


def _sounddevice():
    try:
        import sounddevice
    except ImportError as exc:
        raise VoipDependencyError(
            "The fallback route plays audio through a virtual device, which "
            "needs the sounddevice package:\n  pip install sounddevice\n"
            "and the virtual device itself:\n  brew install blackhole-2ch"
        ) from exc
    return sounddevice


def list_devices():
    """Every output device, so the right one can be named."""
    sd = _sounddevice()
    return [
        {"index": i, "name": d["name"],
         "output_channels": d["max_output_channels"],
         "default_samplerate": d["default_samplerate"]}
        for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0
    ]


def find_output_device(name_contains="BlackHole"):
    """The first output device whose name matches. Raises with the list if none."""
    devices = list_devices()
    for device in devices:
        if name_contains.lower() in device["name"].lower():
            return device
    names = ", ".join(d["name"] for d in devices) or "(none found)"
    raise VoipError(
        f"No output device matching '{name_contains}'. Install the virtual "
        f"device with: brew install blackhole-2ch\nAvailable outputs: {names}"
    )


def play_to_device(wav_path, device="BlackHole", countdown=5.0):
    """Play tx.wav into the virtual device so Linphone sends it as the mic.

    Resampled up to the device rate, because virtual devices generally run at
    48 kHz and will not open an 8 kHz stream. That is a plain interpolation and
    costs the signal nothing: Linphone resamples back down for the codec, and
    the decoder only ever compares tone bins.
    """
    sd = _sounddevice()
    from scipy.signal import resample_poly

    wav_path = os.path.abspath(os.path.expanduser(wav_path))
    if not os.path.isfile(wav_path):
        raise VoipError(f"No such file: {wav_path}")

    target = find_output_device(device)
    with wave.open(wav_path) as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    duration = frames / float(rate)

    device_rate = int(target["default_samplerate"])
    if device_rate != rate:
        divisor = np.gcd(device_rate, rate)
        audio = resample_poly(audio, device_rate // divisor, rate // divisor)

    channels = min(2, target["output_channels"])
    block = np.tile(audio[:, None], (1, channels)).astype(np.float32)

    print(CHECKLIST)
    print(f"device    {target['name']} (index {target['index']}) @ {device_rate} Hz")
    print(f"playing   {os.path.basename(wav_path)}  {duration:.1f} s")

    for remaining in range(int(countdown), 0, -1):
        print(f"  starting in {remaining}... ", end="\r", flush=True)
        time.sleep(1.0)
    print(" " * 30, end="\r")

    started = time.time()
    sd.play(block, samplerate=device_rate, device=target["index"], blocking=True)
    elapsed = time.time() - started

    print(f"done      {elapsed:.1f} s elapsed. Stop recording on the phone now.")

    return {
        "schema": CALL_SCHEMA,
        "mode": "fallback",
        "ok": True,
        "device": target["name"],
        "device_index": target["index"],
        "device_samplerate": device_rate,
        "wav": wav_path,
        "duration_seconds": round(duration, 3),
        "elapsed_seconds": round(elapsed, 3),
        "negotiated": {"mime_type": "unknown", "clock_rate": None},
        "warnings": [
            "Placed by hand through the Linphone desktop app, so there are no "
            "RTP statistics and the negotiated codec must be read off the app "
            "itself. This is the stopgap route, not the SDK integration."
        ],
    }


def instructions(wav_path):
    return CHECKLIST + f"\nThen: python -m voip.cli call --fallback --wav {wav_path}\n"
