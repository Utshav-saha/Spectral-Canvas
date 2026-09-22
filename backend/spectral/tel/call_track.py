"""Track 2 - an image over an 8 kHz voice call.

Track 1 (spectral/encoder/audio_encoder.py) puts a pixel's value in the
AMPLITUDE of a tone. A speech codec fits an 8-pole LPC envelope to every 20 ms
frame, which keeps *which* frequency is present and throws away *how loud* it
is, so Track 1 does not survive a call - see tel/rejected/README.md for the
measurement. Track 2 therefore puts the pixel in WHICH tone is present: the
activation matrix becomes a bitstream, and fsk_codec sends it one tone at a
time out of sixteen. The decoder only ever takes an argmax over tone bins.

The two tracks meet at the same three seams, so the app can treat them alike:

    encode(source, ...) -> (audio, metadata, activation)
    decode(audio, metadata, ...) -> uint8 image array

Locking is the permutation half of the scheme only. scramble/unscramble
reorder the activation matrix, which is upstream of the modem, so they survive
a call untouched. The additive noise mask does not come along: cancelling it
needs sample-exact alignment, and an RTP path with a jitter buffer can never
promise that.
"""

import os
import sys

import numpy as np

_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from spectral.input.image_preprocessor import process_image
from spectral.common.security import derive_key, scramble
from spectral.decoder.decrypter import unscramble
from spectral.decoder.image_reconstructor import activation_to_pixels

import fsk_codec as fsk
import image_fsk

SAMPLE_RATE = fsk.SAMPLE_RATE           # 8000, and not negotiable: the call is


def _permute(activation, caller, receiver, pin, forward):
    """scramble (forward) or unscramble the matrix, a colour channel at a time."""
    if caller is None or receiver is None or pin is None:
        raise ValueError("Caller, receiver and PIN are required to lock or open a file.")

    key = derive_key(caller, receiver, pin)
    apply = scramble if forward else unscramble

    if activation.ndim == 3:
        return np.stack([apply(key, activation[:, :, c])
                         for c in range(activation.shape[2])], axis=-1)
    return apply(key, activation)


def budget_seconds(target_width, target_height, gray_levels=4, mode="L", fec=True):
    """How long the call runs, without doing the encode. Used to refuse a
    request that would take an hour before the user waits for it."""
    channels = 3 if mode == "RGB" else 1
    _, seconds = image_fsk.budget(target_height, target_width,
                                  channels=channels, levels=gray_levels, fec=fec)
    return seconds


def encode(source, target_width=32, target_height=32, gray_levels=4, mode="L",
           security_enabled=False, caller=None, receiver=None, pin=None,
           fec=True):
    activation = process_image(source, target_width=target_width,
                               target_height=target_height,
                               gray_levels=gray_levels, mode=mode)

    to_send = activation
    if security_enabled:
        to_send = _permute(activation, caller, receiver, pin, forward=True)

    audio, info = image_fsk.encode_image(to_send, levels=gray_levels, fec=fec)

    rows, columns = activation.shape[0], activation.shape[1]
    channels = 3 if mode == "RGB" else 1
    payload_bits = int(np.log2(gray_levels)) * rows * columns * channels

    metadata = {
        "kind": "image",
        "track": "call",
        "scheme": "16-fsk",
        "sample_rate": SAMPLE_RATE,
        "rows": rows,
        "columns": columns,
        "mode": mode,
        "channels": channels,
        "encoding_mode": "rgb" if mode == "RGB" else "grayscale",
        "gray_levels": gray_levels,

        "tones": fsk.M,
        "bits_per_symbol": fsk.BITS_PER_SYMBOL,
        "symbol_ms": fsk.SYMBOL_MS,
        "frame_duration": fsk.SYMBOL_MS / 1000.0,
        "frame_samples": fsk.SYMBOL_SAMPLES,
        "f_min": float(fsk.TONES[0]),
        "f_max": float(fsk.TONES[-1]),
        "fec": "hamming(7,4) + interleave" if fec else "none",
        "payload_bits": payload_bits,
        "bit_rate": round(payload_bits / (len(audio) / SAMPLE_RATE), 1),

        "security_enabled": bool(security_enabled),
        # permutation only - see the module docstring
        "security_scheme": "permutation" if security_enabled else None,

        # everything fsk_codec.demodulate() needs, so the WAV decodes alone
        "fsk": info,
        "duration_seconds": round(len(audio) / SAMPLE_RATE, 3),
    }

    return audio, metadata, activation


def decode(audio, metadata, caller=None, receiver=None, pin=None,
           decrypt_enabled=False):
    info = dict(metadata.get("fsk") or {})
    if "shape" not in info:
        raise ValueError("This file is missing its modem header, so there is "
                         "nothing to rebuild from it.")

    security_enabled = bool(metadata.get("security_enabled", False))
    if security_enabled and decrypt_enabled and (caller is None or receiver is None or pin is None):
        raise ValueError("Caller, receiver and PIN are required to decode a secured file.")

    activation = image_fsk.decode_image(audio, info)

    if security_enabled and decrypt_enabled:
        activation = _permute(activation, caller, receiver, pin, forward=False)

    return activation_to_pixels(activation, metadata.get("gray_levels", 4))
