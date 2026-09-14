"""
Simulate what a SIP call does to your audio, offline.

Stages, in the order the real thing applies them:

  1. random leading silence        (you never know when recording started)
  2. GSM 06.10 encode/decode       (the codec pjsua will negotiate)
  3. packet loss + concealment     (lost 20 ms frame -> previous one repeated)
  4. AGC / level change            (unknown, slowly varying gain)
  5. background noise floor        (comfort noise, room, ADC)

Run your encoder -> this -> your decoder and you get realistic numbers in
about a second per trial, instead of placing a call for every experiment.
"""

import os
import subprocess
import tempfile

import numpy as np
from scipy.io.wavfile import read, write

GSM_FRAME = 160          # 20 ms at 8 kHz


def gsm_roundtrip(audio, sample_rate=8000):
    """Encode to GSM 06.10 and back using ffmpeg's libgsm."""
    tmp = tempfile.mkdtemp()
    raw = os.path.join(tmp, "in.wav")
    enc = os.path.join(tmp, "enc.gsm")
    dec = os.path.join(tmp, "out.wav")

    pcm = np.clip(audio, -1.0, 1.0)
    write(raw, sample_rate, (pcm * 32767.0).astype(np.int16))

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
         "-ar", "8000", "-ac", "1", "-c:a", "libgsm", "-f", "gsm", enc],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "gsm", "-ar", "8000",
         "-i", enc, "-c:a", "pcm_s16le", dec],
        check=True,
    )

    _, out = read(dec)
    return out.astype(np.float64) / 32767.0


def packet_loss(audio, loss_rate=0.02, seed=None, frame=GSM_FRAME):
    """Drop 20 ms frames and conceal by repeating the previous one."""
    if loss_rate <= 0:
        return audio
    rng = np.random.default_rng(seed)
    out = audio.copy()
    n = len(audio) // frame
    for i in range(1, n):
        if rng.random() < loss_rate:
            out[i * frame:(i + 1) * frame] = out[(i - 1) * frame:i * frame]
    return out


def agc(audio, gain_db=-6.0, wobble_db=2.0, seed=None):
    """Flat gain change plus a slow wander, the way a handset AGC behaves."""
    rng = np.random.default_rng(seed)
    base = 10 ** (gain_db / 20.0)
    n = len(audio)
    control = rng.standard_normal(max(4, n // 4000))
    control = np.interp(np.linspace(0, 1, n),
                        np.linspace(0, 1, len(control)), control)
    wobble = 10 ** (wobble_db * control / 20.0)
    return audio * base * wobble


def channel(audio, sample_rate=8000, loss_rate=0.02, gain_db=-6.0,
            noise_db=-45.0, lead_ms=(120, 900), seed=0):
    rng = np.random.default_rng(seed)

    lead = rng.integers(int(lead_ms[0] * sample_rate / 1000),
                        int(lead_ms[1] * sample_rate / 1000))
    audio = np.concatenate([np.zeros(lead), audio, np.zeros(sample_rate // 2)])

    audio = gsm_roundtrip(audio, sample_rate)
    audio = packet_loss(audio, loss_rate, seed=seed)
    audio = agc(audio, gain_db=gain_db, seed=seed)

    noise = 10 ** (noise_db / 20.0)
    audio = audio + noise * rng.standard_normal(len(audio))
    return audio
