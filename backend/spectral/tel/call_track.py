"""The call path - an image over an 8 kHz voice line, in two generations.

Track 1 (spectral/encoder/audio_encoder.py) puts a pixel's value in the
AMPLITUDE of a tone. A speech codec fits an 8-pole LPC envelope to every 20 ms
frame, which keeps *which* frequency is present and throws away *how loud* it
is. That gives the two generations offered here:

    Generation A   tel_encoder / tel_decoder
                   The same parallel multitone scheme, narrowbanded to
                   700-3000 Hz with two pilot tones for gain correction. The
                   pixel is still in the amplitude, so a codec damages it:
                   about 75% of pixels exact through simulated GSM, against
                   100% with no channel. That damage is the point - it is
                   graded, reproducible, and it is what the restoration model
                   is trained to undo.

    Generation B   image_fsk / fsk_codec
                   One tone per 40 ms symbol out of sixteen, so the pixel is
                   in which tone plays. The decoder only ever takes an argmax
                   and never compares magnitudes, so a codec cannot touch it:
                   100% of pixels exact through the same channel. The cost is
                   resolution - about 32x32 - which is what the upscaler is
                   for.

Both meet the app at the same two seams, so the routes can treat them alike:

    encode(source, ..., generation=) -> (audio, metadata, activation)
    decode(audio, metadata, ...)     -> uint8 image array

Locking is the permutation half of the scheme only, for both generations.
scramble/unscramble reorder the activation matrix, which is upstream of the
modem, so they survive a call untouched. The additive noise mask does not come
along: cancelling it needs sample-exact alignment, and an RTP path with a
jitter buffer can never promise that.

Generation C (WebP + Reed-Solomon) was cut: a byte-exact file transfer
has no graded loss to measure or learn from. It is in the git history.
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
import tel_config as gen_a_cfg
import tel_encoder
import tel_decoder

SAMPLE_RATE = fsk.SAMPLE_RATE           # 8000, and not negotiable: the call is
GENERATIONS = ("A", "B")

# Defaults per generation. Gen A spends one frame per column whatever the
# amplitude, so it is far faster per pixel than Gen B's serial modem - it can
# afford a bigger grid and more levels, and it is the resolution that makes it
# worth repairing.
DEFAULTS = {
    "A": {"size": 24, "gray_levels": 4},
    "B": {"size": 32, "gray_levels": 4},
}


def _check_generation(generation):
    if generation not in GENERATIONS:
        raise ValueError(f"Generation must be one of {', '.join(GENERATIONS)}.")
    return generation


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


def budget_seconds(target_width, target_height, gray_levels=4, mode="L",
                   generation="B", fec=True):
    """How long the call runs, without doing the encode. Used to quote the
    airtime up front and to refuse one nobody would sit through."""
    _check_generation(generation)
    channels = 3 if mode == "RGB" else 1

    if generation == "A":
        # one frame per column per channel, plus the pilot-only preamble
        frames = target_width * channels + gen_a_cfg.PREAMBLE_FRAMES
        return frames * gen_a_cfg.FRAME_SAMPLES / gen_a_cfg.SAMPLE_RATE

    _, seconds = image_fsk.budget(target_height, target_width,
                                  channels=channels, levels=gray_levels, fec=fec)
    return seconds


def encode(source, target_width=None, target_height=None, gray_levels=None,
           mode="L", generation="B", security_enabled=False, caller=None,
           receiver=None, pin=None, fec=True):
    _check_generation(generation)
    defaults = DEFAULTS[generation]
    target_width = int(target_width or defaults["size"])
    target_height = int(target_height or defaults["size"])
    gray_levels = int(gray_levels or defaults["gray_levels"])

    activation = process_image(source, target_width=target_width,
                               target_height=target_height,
                               gray_levels=gray_levels, mode=mode)

    to_send = activation
    if security_enabled:
        to_send = _permute(activation, caller, receiver, pin, forward=True)

    rows, columns = activation.shape[0], activation.shape[1]
    channels = 3 if mode == "RGB" else 1

    if generation == "A":
        audio, gen_meta = tel_encoder.encode(to_send)
        # tel_encoder reports the module default; say what was actually used
        gen_meta["gray_levels"] = gray_levels
        wire = {
            "scheme": "multitone-pilot",
            "band": [gen_a_cfg.F_LOW, gen_a_cfg.F_HIGH],
            "frame_duration": gen_a_cfg.FRAME_DURATION,
            "window": "tukey",
            "pilots": 2,
            "lossy": True,
            "tel": gen_meta,
        }
    else:
        audio, info = image_fsk.encode_image(to_send, levels=gray_levels, fec=fec)
        payload_bits = int(np.log2(gray_levels)) * rows * columns * channels
        wire = {
            "scheme": "16-fsk",
            "band": [float(fsk.TONES[0]), float(fsk.TONES[-1])],
            "frame_duration": fsk.SYMBOL_MS / 1000.0,
            "frame_samples": fsk.SYMBOL_SAMPLES,
            "window": "tukey",
            "tones": fsk.M,
            "bits_per_symbol": fsk.BITS_PER_SYMBOL,
            "symbol_ms": fsk.SYMBOL_MS,
            "fec": "hamming(7,4) + interleave" if fec else "none",
            "payload_bits": payload_bits,
            "lossy": False,
            # everything fsk_codec.demodulate() needs, so the WAV decodes alone
            "fsk": info,
        }

    metadata = {
        "kind": "image",
        "track": "call",
        "generation": generation,
        "sample_rate": SAMPLE_RATE,
        "rows": rows,
        "columns": columns,
        "mode": mode,
        "channels": channels,
        "encoding_mode": "rgb" if mode == "RGB" else "grayscale",
        "gray_levels": gray_levels,
        "security_enabled": bool(security_enabled),
        # permutation only - see the module docstring
        "security_scheme": "permutation" if security_enabled else None,
        "duration_seconds": round(len(audio) / SAMPLE_RATE, 3),
        **wire,
    }

    return audio, metadata, activation


def decode(audio, metadata, caller=None, receiver=None, pin=None,
           decrypt_enabled=False):
    generation = _check_generation(metadata.get("generation", "B"))
    gray_levels = metadata.get("gray_levels", 4)

    security_enabled = bool(metadata.get("security_enabled", False))
    if security_enabled and decrypt_enabled and (caller is None or receiver is None or pin is None):
        raise ValueError("Caller, receiver and PIN are required to decode a secured file.")

    if generation == "A":
        gen_meta = metadata.get("tel")
        if not gen_meta:
            raise ValueError("This file is missing its Generation A header, so "
                             "there is nothing to rebuild from it.")
        activation = tel_decoder.decode(audio, gen_meta)
        # the channel does not preserve level, so the result is continuous;
        # snap it back onto the gray steps that were actually transmitted
        activation = tel_decoder.quantize(activation, gray_levels)
    else:
        info = dict(metadata.get("fsk") or {})
        if "shape" not in info:
            raise ValueError("This file is missing its modem header, so there is "
                             "nothing to rebuild from it.")
        activation = image_fsk.decode_image(audio, info)

    if security_enabled and decrypt_enabled:
        activation = _permute(activation, caller, receiver, pin, forward=False)

    return activation_to_pixels(activation, gray_levels)
