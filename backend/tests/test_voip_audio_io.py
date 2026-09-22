"""Reading whatever container the phone produced.

Linphone records Matroska. The existing read_any_wav is scipy-only, so without
these paths a real recording cannot be opened at all.
"""

import os
import subprocess

import numpy as np
import pytest
from scipy.io import wavfile

from voip import audio_io
from voip.config import SAMPLE_RATE, VoipDependencyError, VoipError

from conftest import needs_ffmpeg


def tone(seconds=1.0, freq=1000.0, rate=SAMPLE_RATE, amplitude=0.5):
    t = np.arange(int(seconds * rate)) / rate
    return amplitude * np.sin(2 * np.pi * freq * t)


# --------------------------------------------------------------------------
# WAV
# --------------------------------------------------------------------------

def test_write_then_read_round_trips(tmp_path):
    original = tone()
    path = str(tmp_path / "t.wav")
    audio_io.write_int16_wav(path, original)

    loaded, meta = audio_io.load_audio(path)
    assert meta["loader"] == "scipy"
    assert meta["sample_rate_in"] == SAMPLE_RATE
    assert not meta["resampled"]
    assert np.sqrt(np.mean((loaded - original) ** 2)) < 1e-4


@pytest.mark.parametrize("dtype,scale", [
    (np.int16, 32767), (np.int32, 2147483647), (np.uint8, None),
])
def test_every_pcm_depth_normalises_to_the_same_range(tmp_path, dtype, scale):
    original = tone()
    path = str(tmp_path / f"{np.dtype(dtype).name}.wav")
    if dtype == np.uint8:
        data = (original * 127 + 128).astype(np.uint8)
    else:
        data = (original * scale).astype(dtype)
    wavfile.write(path, SAMPLE_RATE, data)

    loaded, _ = audio_io.load_audio(path)
    assert np.max(np.abs(loaded)) <= 1.0
    assert np.sqrt(np.mean((loaded - original) ** 2)) < 0.02


def test_float_wav_is_read(tmp_path):
    path = str(tmp_path / "f32.wav")
    wavfile.write(path, SAMPLE_RATE, tone().astype(np.float32))
    loaded, _ = audio_io.load_audio(path)
    assert np.max(np.abs(loaded)) <= 1.0


def test_stereo_is_mixed_to_mono(tmp_path):
    left, right = tone(freq=800), tone(freq=1600)
    path = str(tmp_path / "stereo.wav")
    wavfile.write(path, SAMPLE_RATE,
                  (np.stack([left, right], axis=1) * 32767).astype(np.int16))

    loaded, meta = audio_io.load_audio(path)
    assert meta["channels"] == 2
    assert loaded.ndim == 1
    assert np.sqrt(np.mean((loaded - (left + right) / 2) ** 2)) < 1e-3


@pytest.mark.parametrize("rate", [44100, 48000, 16000])
def test_other_sample_rates_are_resampled(tmp_path, rate):
    """Phones record at 48 kHz; everything has to arrive at 8 kHz."""
    t = np.arange(int(2.0 * rate)) / rate
    path = str(tmp_path / f"{rate}.wav")
    wavfile.write(path, rate, (0.5 * np.sin(2 * np.pi * 1000 * t) * 32767).astype(np.int16))

    loaded, meta = audio_io.load_audio(path)
    assert meta["sample_rate_in"] == rate
    assert meta["resampled"]
    assert abs(len(loaded) - 2 * SAMPLE_RATE) < 100

    # the 1 kHz tone must still be the strongest bin
    spectrum = np.abs(np.fft.rfft(loaded * np.hanning(len(loaded))))
    peak_hz = np.argmax(spectrum) * SAMPLE_RATE / len(loaded)
    assert abs(peak_hz - 1000) < 20


def test_missing_file_says_so(tmp_path):
    with pytest.raises(VoipError, match="No such recording"):
        audio_io.load_audio(str(tmp_path / "nope.wav"))


# --------------------------------------------------------------------------
# Containers that need ffmpeg
# --------------------------------------------------------------------------

def _transcode(src, dst, *args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, *args, dst],
                   check=True, capture_output=True)


@needs_ffmpeg
@pytest.mark.parametrize("name,args", [
    ("rec.mka", ["-c:a", "libopus", "-b:a", "32k"]),   # what Linphone actually writes
    ("rec.m4a", ["-c:a", "aac", "-b:a", "64k"]),       # an iOS share
    ("rec.flac", []),
])
def test_compressed_containers_load(tmp_path, name, args):
    source = str(tmp_path / "src.wav")
    audio_io.write_int16_wav(source, tone(seconds=2.0))
    target = str(tmp_path / name)
    _transcode(source, target, *args)

    loaded, meta = audio_io.load_audio(target)
    assert meta["loader"] == "ffmpeg"
    assert len(loaded) > SAMPLE_RATE
    spectrum = np.abs(np.fft.rfft(loaded * np.hanning(len(loaded))))
    peak_hz = np.argmax(spectrum) * SAMPLE_RATE / len(loaded)
    assert abs(peak_hz - 1000) < 30


@needs_ffmpeg
def test_a_matroska_recording_decodes_the_same_as_its_wav(tmp_path):
    """The point of the whole module: .mka must be as good as .wav."""
    from voip import decode, encode, simulate

    prepared = encode.prepare(text="over a real call", generation="C")
    received, _ = simulate.simulate(prepared.audio, lead_seconds=12.0,
                                    gsm=False, seed=2)

    wav = str(tmp_path / "rx.wav")
    audio_io.write_int16_wav(wav, received)
    mka = str(tmp_path / "rx.mka")
    _transcode(wav, mka, "-c:a", "libopus", "-b:a", "64k")

    assert decode.decode(wav).text == "over a real call"
    assert decode.decode(mka).text == "over a real call"


@needs_ffmpeg
def test_bytes_upload_is_loaded_and_the_temp_file_cleaned(tmp_path):
    source = str(tmp_path / "src.wav")
    audio_io.write_int16_wav(source, tone())
    target = str(tmp_path / "up.mka")
    _transcode(source, target, "-c:a", "libopus")

    before = len(os.listdir(tempdir()))
    with open(target, "rb") as handle:
        loaded, meta = audio_io.load_audio_bytes(handle.read(), "up.mka")
    assert meta["loader"] == "ffmpeg"
    assert len(loaded) > 0
    assert len(os.listdir(tempdir())) == before, "a temp file was left behind"


def tempdir():
    import tempfile
    return tempfile.gettempdir()


@pytest.mark.skipif(audio_io.ffmpeg_available(), reason="ffmpeg is installed")
def test_without_ffmpeg_the_error_says_how_to_get_it(tmp_path):
    path = str(tmp_path / "rec.mka")
    open(path, "wb").write(b"\x1a\x45\xdf\xa3")
    with pytest.raises(VoipDependencyError, match="brew install ffmpeg"):
        audio_io.load_audio(path)


def test_empty_upload_is_refused():
    with pytest.raises(VoipError, match="empty"):
        audio_io.load_audio_bytes(b"", "x.wav")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def test_padding_adds_exactly_the_silence_asked_for():
    padded = audio_io.pad_audio(tone(seconds=1.0), 2.0, 0.5)
    assert len(padded) == int(3.5 * SAMPLE_RATE)
    assert np.all(padded[:2 * SAMPLE_RATE] == 0)
    assert np.all(padded[-int(0.5 * SAMPLE_RATE):] == 0)


def test_normalize_peak_is_safe_on_silence():
    assert np.all(audio_io.normalize_peak(np.zeros(100)) == 0)
    assert np.max(np.abs(audio_io.normalize_peak(tone() * 0.01))) == pytest.approx(0.95)


def test_written_wav_is_16_bit_mono_at_8k(tmp_path):
    """pjsua refuses anything else, and so does Linphone's player."""
    path = str(tmp_path / "tx.wav")
    audio_io.write_int16_wav(path, tone())
    rate, data = wavfile.read(path)
    assert rate == SAMPLE_RATE
    assert data.dtype == np.int16
    assert data.ndim == 1


def test_clipping_is_bounded_not_wrapped(tmp_path):
    path = str(tmp_path / "hot.wav")
    audio_io.write_int16_wav(path, tone(amplitude=4.0))
    _, data = wavfile.read(path)
    assert data.max() <= 32767 and data.min() >= -32767
