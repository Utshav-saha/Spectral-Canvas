"""Rehearse the call without making one.

Two rungs sit between "it decoded in memory" and "it decoded off a phone".

``channel_sim.channel`` is the teammate's GSM 06.10 simulator and the harsher,
more realistic of the two -- but it shells out to ffmpeg-with-libgsm or to
``toast``, so it cannot run on a machine without them, which includes CI.

So there is also a codec-free path here: long unknown lead-in, telephone
band-pass, gain wander and noise. It is gentler than GSM, and that is fine,
because its job is not to prove the modem survives a codec -- ``channel_sim``
does that -- but to prove *this package's* sync, framing and bounds checking
survive a recording that starts at an unknown place and has been through an
analogue-ish path. That is what actually broke on a real call.

The third rung, ``run_local_call.sh``, is a genuine SIP loopback with real RTP
and a real jitter buffer, and is reachable from here too.
"""

import os
import subprocess

import numpy as np
from scipy.signal import butter, sosfilt

from voip import _tel
from voip.config import SAMPLE_RATE, TEL_DIR, VoipDependencyError, VoipError

PRESETS = {
    "clean":     {"loss": 0.0,  "noise_db": -60.0, "gain_db": -3.0},
    "good_wifi": {"loss": 0.01, "noise_db": -50.0, "gain_db": -6.0},
    "wifi":      {"loss": 0.02, "noise_db": -45.0, "gain_db": -6.0},
    "bad_wifi":  {"loss": 0.05, "noise_db": -35.0, "gain_db": -9.0},
}


def gsm_available():
    """True when channel_sim can find a GSM 06.10 codec to shell out to."""
    import shutil
    try:
        return bool(shutil.which("toast") or _tel.channel_sim()._ffmpeg_has_libgsm())
    except VoipDependencyError:
        return False


def simulate(audio, loss=0.02, noise_db=-45.0, gain_db=-6.0, lead_seconds=30.0,
             seed=0, gsm=True):
    """Put a transmission through a fake call. -> (audio, meta)."""
    audio = np.asarray(audio, dtype=np.float64)

    if gsm:
        if not gsm_available():
            raise VoipDependencyError(
                "No GSM 06.10 codec is installed, so the codec path cannot run. "
                "Install one with: brew install libgsm   (or use --no-gsm for the "
                "codec-free rehearsal)."
            )
        lead_ms = (int(lead_seconds * 1000), int(lead_seconds * 1000) + 800)
        out = _tel.channel_sim().channel(
            audio, sample_rate=SAMPLE_RATE, loss_rate=loss,
            gain_db=gain_db, noise_db=noise_db, lead_ms=lead_ms, seed=int(seed))
        meta = {"path": "channel_sim", "codec": "gsm0610", "loss": loss,
                "noise_db": noise_db, "gain_db": gain_db,
                "lead_seconds": lead_seconds, "seed": int(seed)}
        return np.asarray(out, dtype=np.float64), meta

    return _codec_free(audio, loss, noise_db, gain_db, lead_seconds, seed)


def _codec_free(audio, loss, noise_db, gain_db, lead_seconds, seed):
    """Lead-in, telephone band, AGC wander, packet loss and noise. No ffmpeg."""
    rng = np.random.default_rng(int(seed))
    gain = 10.0 ** (gain_db / 20.0)
    noise = 10.0 ** (noise_db / 20.0)

    lead = int(round(lead_seconds * SAMPLE_RATE))
    jitter = int(rng.integers(0, int(0.8 * SAMPLE_RATE) + 1))
    head = rng.normal(0.0, noise, lead + jitter)
    tail = rng.normal(0.0, noise, int(1.0 * SAMPLE_RATE))

    sos = butter(4, [300.0, 3400.0], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    body = sosfilt(sos, audio) * gain

    # slow gain wander, the way an AGC behaves
    control = rng.normal(0.0, 1.0, max(2, len(body) // 4000 + 2))
    wobble = np.interp(np.arange(len(body)),
                       np.linspace(0, len(body), len(control)), control)
    body = body * (10.0 ** (2.0 * wobble / 20.0))

    out = np.concatenate([head, body, tail])
    out += rng.normal(0.0, noise, len(out))

    if loss > 0:
        frame = 160                      # 20 ms at 8 kHz, one RTP packet
        n_frames = len(out) // frame
        victims = rng.random(n_frames) < loss
        for index in np.nonzero(victims)[0]:
            if index == 0:
                continue
            # packet-loss concealment repeats the previous frame
            out[index * frame:(index + 1) * frame] = \
                out[(index - 1) * frame:index * frame]

    meta = {"path": "codec-free", "codec": None, "loss": loss,
            "noise_db": noise_db, "gain_db": gain_db,
            "lead_seconds": lead_seconds + jitter / SAMPLE_RATE, "seed": int(seed)}
    return out, meta


# --------------------------------------------------------------------------
# Real SIP loopback
# --------------------------------------------------------------------------

def pjsua_available():
    import shutil
    return shutil.which("pjsua") is not None


def local_call(tx_wav, rx_wav, codec="GSM", timeout=900):
    """Place a real SIP call to ourselves via run_local_call.sh.

    Real signalling, real RTP packetisation, a real jitter buffer and real
    packet-loss concealment, with no account, no phone and no SDK. It is the
    rung that catches the things the offline simulator cannot: a WAV that is
    not 8 kHz 16-bit mono PCM (pjsua simply refuses to open one), an off-by-one
    in the lead-in, and a codec silently falling back to PCMU.
    """
    script = os.path.join(TEL_DIR, "run_local_call.sh")
    if not os.path.isfile(script):
        raise VoipError(f"Missing {script}")
    if not pjsua_available():
        raise VoipDependencyError(
            "pjsua is not installed, so the SIP loopback cannot run. "
            "Install it with: brew install pjproject"
        )

    env = dict(os.environ, CODEC=codec)
    try:
        proc = subprocess.run(["bash", script, os.path.abspath(tx_wav),
                               os.path.abspath(rx_wav)],
                              cwd=TEL_DIR, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VoipError(f"The SIP loopback did not finish within {timeout} s.") from exc

    if not os.path.isfile(rx_wav):
        raise VoipError(
            f"The SIP loopback produced no recording.\n{proc.stdout[-2000:]}\n"
            f"{proc.stderr[-2000:]}"
        )

    negotiated = _negotiated_codec(proc.stdout)
    if negotiated and codec.upper() not in negotiated.upper():
        note = (f"Asked for {codec} but the call negotiated {negotiated}. "
                f"G.711 is nearly transparent, so a clean decode over it proves "
                f"much less than one over GSM.")
    else:
        note = None

    return {"recording": rx_wav, "requested_codec": codec,
            "negotiated": negotiated, "warning": note, "log": proc.stdout[-4000:]}


def _negotiated_codec(stdout):
    for line in stdout.splitlines():
        upper = line.upper()
        for name in ("GSM", "PCMU", "PCMA", "ILBC", "SPEEX", "OPUS"):
            if name in upper and "CODEC" in upper:
                return name
    return None
