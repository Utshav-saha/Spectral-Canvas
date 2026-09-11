"""Browser-playable 16-bit PCM WAV that carries its own decode metadata.

Why this exists: the Receive page uploads ONE file. Decoding needs
row_frequencies, frame_samples, columns, channels, gray_levels and
normalization_gain. Rather than making the user carry a second .json around,
we append a custom 'SpCv' RIFF chunk holding that JSON. Any player that
doesn't recognise the chunk simply skips it, so the file still plays in a
browser, in VLC, anywhere.

Also note the format is int16, not float64. scipy.io.wavfile.write() with a
float64 array produces a 64-bit float WAV, which Python reads back fine but
no browser will play. int16 is the price of admission for <audio> playback.
"""

import struct
import json
import numpy as np

MAGIC = b"SpCv"


def write_wav_bytes(sample_rate: int, audio_float, metadata: dict) -> bytes:
    audio = np.clip(np.asarray(audio_float, dtype=np.float64), -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2").tobytes()

    fmt_payload = struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload

    data_chunk = b"data" + struct.pack("<I", len(pcm)) + pcm
    if len(pcm) % 2:
        data_chunk += b"\x00"

    blob = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    meta_chunk = MAGIC + struct.pack("<I", len(blob)) + blob
    if len(blob) % 2:
        meta_chunk += b"\x00"

    body = b"WAVE" + fmt_chunk + data_chunk + meta_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def read_wav_bytes(raw: bytes):
    """-> (sample_rate, mono float64 audio in +-1.0, metadata dict or None)"""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("Not a WAV file.")

    pos = 12
    sample_rate = None
    channels = 1
    bits = 16
    audio = None
    metadata = None

    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        payload = raw[pos + 8:pos + 8 + size]

        if chunk_id == b"fmt ":
            _, channels, sample_rate, _, _, bits = struct.unpack("<HHIIHH", payload[:16])
        elif chunk_id == b"data":
            if bits == 16:
                audio = np.frombuffer(payload, dtype="<i2").astype(np.float64) / 32767.0
            elif bits == 32:
                audio = np.frombuffer(payload, dtype="<f4").astype(np.float64)
            elif bits == 64:
                audio = np.frombuffer(payload, dtype="<f8").astype(np.float64)
            else:
                raise ValueError(f"Unsupported bit depth: {bits}")
        elif chunk_id == MAGIC:
            metadata = json.loads(payload.decode("utf-8"))

        pos += 8 + size + (size % 2)

    if audio is None:
        raise ValueError("WAV file has no audio data.")

    # a stereo recording comes back interleaved - average down to mono
    if channels > 1:
        usable = (len(audio) // channels) * channels
        audio = audio[:usable].reshape(-1, channels).mean(axis=1)

    return sample_rate, audio, metadata
