"""Placing the call.

Two routes to the same channel, because the first one may not build:

``session.py`` drives liblinphone's Python wrapper directly -- register, dial,
play the WAV into the call, collect RTP statistics, hang up. This is the real
integration.

``fallback.py`` needs no SDK at all. The call is placed by hand in the Linphone
desktop app with its microphone set to a virtual audio device, and this plays
``tx.wav`` into that device. Same codec, same RTP, same jitter buffer, same
recording on the phone -- only the dialling is manual.

Keep the fallback working. The Python bindings are not on PyPI for macOS on
Apple silicon and have to be built from source, which is the single largest
schedule risk in this branch.

Nothing here is imported at package import time; ``import voip`` must stay
usable on a machine with no SDK.
"""

__all__ = ["sdk", "session", "fallback"]
