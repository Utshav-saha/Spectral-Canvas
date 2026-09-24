"""
Bridge: your activation matrix <-> a bitstream <-> FSK audio.

This slots into your existing pipeline without touching image_preprocessor.py
or security.py at all:

    process_image(...)          -> activation          (unchanged, yours)
    encrypt(...)                -> scrambled           (unchanged, yours)
    activation_to_bits(...)     -> bits                (new)
    fsk.modulate(...)           -> audio               (new)
        ... the call ...
    fsk.demodulate(...)         -> bits                (new)
    bits_to_activation(...)     -> activation          (new)
    decrypt(...)                -> activation          (unchanged, yours)
    activation_to_pixels(...)   -> image               (unchanged, yours)

Note the scramble still works: it permutes the activation matrix, which is
upstream of the modem. Only the ADDITIVE mask in generate_mask/remove_mask
has to be dropped - that one needs sample-exact cancellation, which an RTP
path with a jitter buffer can never give you.
"""

import numpy as np

import fsk_codec as fsk


def _bits_per_pixel(levels):
    bits = int(np.log2(levels))
    if 2 ** bits != levels:
        raise ValueError(f"gray_levels must be a power of two, got {levels}")
    return bits


def activation_to_bits(activation, levels=4):
    """activation in 0..1 -> flat bit array, MSB first per pixel."""
    bpp = _bits_per_pixel(levels)
    values = np.round(np.clip(activation, 0, 1) * (levels - 1)).astype(np.uint16)
    flat = values.ravel()
    shifts = np.arange(bpp - 1, -1, -1)
    return ((flat[:, None] >> shifts) & 1).astype(np.uint8).ravel()


def bits_to_activation(bits, shape, levels=4):
    bpp = _bits_per_pixel(levels)
    n = int(np.prod(shape))
    bits = np.asarray(bits, dtype=np.uint8)
    need = n * bpp
    if len(bits) < need:
        bits = np.concatenate([bits, np.zeros(need - len(bits), dtype=np.uint8)])
    words = bits[:need].reshape(n, bpp)
    weights = 2 ** np.arange(bpp - 1, -1, -1)
    values = (words * weights).sum(axis=1)
    return (values / (levels - 1)).reshape(shape)


def encode_image(activation, levels=4, fec=True, header=None):
    bits = activation_to_bits(activation, levels)
    audio, info = fsk.modulate(bits, fec=fec, header=header)
    info["shape"] = list(activation.shape)
    info["gray_levels"] = levels
    return audio, info


def decode_image(audio, info):
    bits = fsk.demodulate(audio, info)
    return bits_to_activation(bits, tuple(info["shape"]), info["gray_levels"])


def budget(rows, columns, channels=1, levels=4, fec=True, header_bits=0):
    """How long will this take on the wire?

    The re-sync markers ride on the wire too - one leading every segment,
    which is what keeps a long call decodable at all - and fsk.frame_info
    counts them the same way modulate() lays them down.
    """
    bpp = _bits_per_pixel(levels)
    payload = rows * columns * channels * bpp
    info = fsk.frame_info(payload, fec=fec, header_bits=header_bits)
    return payload, float(info["duration_seconds"])
