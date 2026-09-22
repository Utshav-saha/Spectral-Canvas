"""Read a recording in whatever container the phone produced; write 8 kHz WAV.

``app/services/tel_pipeline.read_any_wav`` handles WAV via scipy, which is
enough for the simulated call because the simulator hands back an array. A real
Linphone recording is **Matroska** (``.mka``), and iOS shares may arrive as
``.m4a`` or ``.caf``. Those need ffmpeg.

The WAV path here deliberately mirrors ``read_any_wav``'s dtype ladder and
``resample_poly`` call so that a file decoding one way decodes the same way the
other.
"""

import os
import shutil
import subprocess
import tempfile
from math import gcd

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from voip.config import (
    NATIVE_EXTENSIONS,
    SAMPLE_RATE,
    VoipDependencyError,
    VoipError,
)

FFMPEG_HINT = "Install it with: brew install ffmpeg"


# --------------------------------------------------------------------------
# Tool discovery
# --------------------------------------------------------------------------

def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def ffprobe_available():
    return shutil.which("ffprobe") is not None


def ffmpeg_has_libgsm():
    """Homebrew's ffmpeg is built without libgsm; channel_sim falls back to toast."""
    if not ffmpeg_available():
        return False
    try:
        probe = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                               capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return "libgsm" in probe.stdout


def probe(path):
    """Container and codec of a recording, via ffprobe. {} if unavailable."""
    if not ffprobe_available():
        return {}
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=format_name,duration:stream=codec_name,sample_rate,channels",
           "-of", "default=noprint_wrappers=1", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}

    info = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            info[key.strip()] = value.strip()

    result = {}
    if "format_name" in info:
        result["container"] = info["format_name"]
    if "codec_name" in info:
        result["codec"] = info["codec_name"]
    for key, cast in (("sample_rate", int), ("channels", int), ("duration", float)):
        if info.get(key) not in (None, "", "N/A"):
            try:
                result[key if key != "duration" else "duration_seconds"] = cast(info[key])
            except ValueError:
                pass
    return result


# --------------------------------------------------------------------------
# Conversion helpers
# --------------------------------------------------------------------------

def _to_float(data):
    """scipy gives whatever the file held; normalise to float64 in [-1, 1]."""
    if data.dtype == np.int16:
        return data.astype(np.float64) / 32768.0
    if data.dtype == np.int32:
        return data.astype(np.float64) / 2147483648.0
    if data.dtype == np.uint8:
        return (data.astype(np.float64) - 128.0) / 128.0
    return data.astype(np.float64)


def to_mono(audio):
    return audio.mean(axis=1) if audio.ndim > 1 else audio


def resample_to(audio, rate, target_rate=SAMPLE_RATE):
    if rate == target_rate:
        return np.asarray(audio, dtype=np.float64)
    divisor = gcd(int(rate), int(target_rate))
    return resample_poly(audio, target_rate // divisor, int(rate) // divisor)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _load_wav(path, target_rate):
    rate, data = wavfile.read(path)
    channels = 1 if data.ndim == 1 else data.shape[1]
    audio = to_mono(_to_float(data))
    if len(audio) == 0:
        raise VoipError(f"{os.path.basename(path)} has no audio in it.")
    meta = {
        "loader": "scipy",
        "sample_rate_in": int(rate),
        "channels": int(channels),
        "resampled": int(rate) != target_rate,
        "duration_seconds": len(audio) / float(rate),
    }
    return resample_to(audio, rate, target_rate), meta


def _load_ffmpeg(path, target_rate):
    """Decode anything ffmpeg understands straight to raw mono s16 at 8 kHz."""
    if not ffmpeg_available():
        ext = os.path.splitext(path)[1] or "this format"
        raise VoipDependencyError(
            f"This recording is a {ext} file, which needs ffmpeg to read. "
            f"{FFMPEG_HINT}"
        )
    meta = probe(path)
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-f", "s16le", "-acodec", "pcm_s16le",
           "-ac", "1", "-ar", str(target_rate), "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=600)
    except OSError as exc:
        raise VoipDependencyError(f"Could not run ffmpeg: {exc}. {FFMPEG_HINT}") from exc

    if out.returncode != 0:
        detail = out.stderr.decode("utf-8", "replace").strip().splitlines()
        reason = detail[-1] if detail else f"exit code {out.returncode}"
        raise VoipError(f"ffmpeg could not read {os.path.basename(path)}: {reason}")

    audio = np.frombuffer(out.stdout, dtype="<i2").astype(np.float64) / 32768.0
    if len(audio) == 0:
        raise VoipError(f"{os.path.basename(path)} decoded to no audio at all.")

    meta.update({
        "loader": "ffmpeg",
        "sample_rate_in": meta.get("sample_rate", target_rate),
        "channels": meta.get("channels", 1),
        "resampled": meta.get("sample_rate", target_rate) != target_rate,
    })
    meta.setdefault("duration_seconds", len(audio) / float(target_rate))
    meta.pop("sample_rate", None)
    return audio, meta


def load_audio(path, target_rate=SAMPLE_RATE):
    """Any recording -> (float64 mono at target_rate, metadata dict).

    WAV goes through scipy; everything else through ffmpeg. A WAV that scipy
    rejects -- 24-bit, WAVE_FORMAT_EXTENSIBLE, ADPCM, all of which real
    recorders emit -- falls through to ffmpeg rather than failing.
    """
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        raise VoipError(f"No such recording: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext in NATIVE_EXTENSIONS:
        try:
            audio, meta = _load_wav(path, target_rate)
        except VoipError:
            raise
        except Exception:
            audio, meta = _load_ffmpeg(path, target_rate)
    else:
        audio, meta = _load_ffmpeg(path, target_rate)

    meta["path"] = path
    meta["name"] = os.path.basename(path)
    return np.asarray(audio, dtype=np.float64), meta


def load_audio_bytes(raw, filename=None, target_rate=SAMPLE_RATE):
    """Same, for an upload held in memory.

    Written to a real temp file rather than piped to ffmpeg's stdin: Matroska
    demuxing from a non-seekable pipe is unreliable, and .mka is the format we
    exist to support.
    """
    if not raw:
        raise VoipError("That upload was empty.")
    ext = os.path.splitext(filename or "")[1].lower() or ".bin"
    handle = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        handle.write(raw)
        handle.close()
        audio, meta = load_audio(handle.name, target_rate)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
    meta["path"] = None
    meta["name"] = filename or "upload"
    return audio, meta


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def write_int16_wav(path, audio, sample_rate=SAMPLE_RATE):
    """16-bit mono PCM.

    Not negotiable for tx.wav: pjsua refuses to open a float64 WAV outright,
    and Linphone's file player is happiest with plain PCM.
    """
    pcm = (np.clip(np.asarray(audio, dtype=np.float64), -1.0, 1.0) * 32767.0)
    wavfile.write(path, int(sample_rate), pcm.astype(np.int16))
    return path


def to_wav_bytes(audio, sample_rate=SAMPLE_RATE):
    import io
    buf = io.BytesIO()
    pcm = (np.clip(np.asarray(audio, dtype=np.float64), -1.0, 1.0) * 32767.0)
    wavfile.write(buf, int(sample_rate), pcm.astype(np.int16))
    return buf.getvalue()


def pad_audio(audio, lead_in_s=0.0, lead_out_s=0.0, sample_rate=SAMPLE_RATE):
    """Digital silence before and after the transmission."""
    lead = np.zeros(int(round(lead_in_s * sample_rate)), dtype=np.float64)
    tail = np.zeros(int(round(lead_out_s * sample_rate)), dtype=np.float64)
    return np.concatenate([lead, np.asarray(audio, dtype=np.float64), tail])


def normalize_peak(audio, peak=0.95):
    """Scale to a known peak. Safe on silence.

    Harmless for decoding -- argmax over tone bins ignores gain entirely -- but
    it keeps the recorded and transmitted waveforms visually comparable.
    """
    audio = np.asarray(audio, dtype=np.float64)
    high = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return audio * (peak / high) if high > 0 else audio


def duration_seconds(audio, sample_rate=SAMPLE_RATE):
    return len(audio) / float(sample_rate)
