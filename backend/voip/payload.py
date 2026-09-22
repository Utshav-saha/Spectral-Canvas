"""What the modem carries: the picture, in one of two generations.

**Generation A** -- ``tel_encoder``/``tel_decoder`` through
``spectral/tel/call_track.py``. Parallel multitone with two pilot tones, so
the pixel is in a tone's amplitude. A speech codec models each 20 ms frame
with an 8-pole LPC envelope and cannot hold that many simultaneous levels, so
the picture arrives damaged -- about two thirds of pixels exact through
simulated GSM. That is not a defect here: the damage is graded and
reproducible, and it is the input the restoration model is trained on.

**Generation B** -- ``image_fsk``: raw quantized pixels with Hamming(7,4), one
tone per symbol out of sixteen. The decoder takes an argmax and never compares
magnitudes, so the codec cannot touch it and the picture arrives exact. It
caps out around 32x32, which is what the upscaler is for.

Both carry the same lock: ``scramble``/``unscramble`` permute the activation
matrix upstream of the modem, so the shuffle survives a call. The additive
noise mask does not come along -- cancelling it needs sample-exact alignment
that an RTP path with a jitter buffer cannot give.

Generation C (WebP + Reed-Solomon) was cut: a byte-exact file transfer has no graded loss to measure or learn from. It is in the git history. Text over a call now goes
through ``--as-image``, rendered and sent as a picture, rather than as the raw
bytes Generation C used to carry.
"""

import io

import numpy as np
from PIL import Image

from voip import _tel
from voip.config import VoipError


# --------------------------------------------------------------------------
# Generation B
# --------------------------------------------------------------------------

def build_genb_bits(activation, levels):
    """Quantized activation matrix -> flat bits, MSB first per pixel."""
    activation = np.asarray(activation, dtype=np.float64)
    if activation.ndim != 2:
        raise VoipError(
            "Generation B carries grayscale only. Use --gen A for a colour picture."
        )
    bits = _tel.image_fsk().activation_to_bits(activation, levels)
    rows, cols = activation.shape
    return bits, {
        "kind": "image", "rows": int(rows), "cols": int(cols),
        "levels": int(levels), "payload_bits": int(len(bits)),
    }


def genb_bits_to_activation(bits, rows, cols, levels):
    return _tel.image_fsk().bits_to_activation(bits, (int(rows), int(cols)), int(levels))


def _psnr(a, b):
    mse = float(np.mean((np.asarray(a, float) - np.asarray(b, float)) ** 2))
    return None if mse == 0 else float(10 * np.log10(255.0 ** 2 / mse))


# --------------------------------------------------------------------------
# Generation A
# --------------------------------------------------------------------------

def gen_a():
    """spectral/tel/call_track.py, loaded the same lazy way as the tel modules."""
    _tel.ensure_path()
    from spectral.tel import call_track
    return call_track
