"""Round-trips five ways through the real int16 WAV container.

Run:  cd backend && python -m tests.test_roundtrip
Every line should report err 0.000.
"""

import numpy as np
from PIL import Image

from spectral.encoder.audio_encoder import encode
from spectral.common.wav_container import write_wav_bytes, read_wav_bytes
from spectral.decoder.image_reconstructor import (
    reconstruct, to_image_array, mean_absolute_error, psnr,
)
from spectral.input.image_preprocessor import render_text_image

CALLER, RECEIVER, PIN = "12345678901", "10987654321", "1234"


def sample_image():
    image = Image.new("RGB", (80, 60), (255, 255, 255))
    px = image.load()
    for x in range(80):
        for y in range(60):
            if 15 < x < 65 and 12 < y < 48:
                px[x, y] = (40, 120, 210)
            if 30 < x < 50 and 20 < y < 40:
                px[x, y] = (240, 90, 60)
    return image


def roundtrip(source, mode, secure, label, size=48):
    audio, metadata, activation = encode(
        source, size, size, mode=mode, security_enabled=secure,
        caller=CALLER, receiver=RECEIVER, pin=PIN, alpha=0.15,
    )

    # through the actual container the browser downloads
    wav = write_wav_bytes(metadata["sample_rate"], audio, metadata)
    _, audio_back, metadata_back = read_wav_bytes(wav)

    recovered = reconstruct(audio_back, metadata_back, CALLER, RECEIVER, PIN,
                            decrypt_enabled=secure)
    source_image = to_image_array(activation, metadata["gray_levels"])

    err = mean_absolute_error(recovered, source_image)
    quality = psnr(recovered, source_image)
    peak = float(np.max(np.abs(audio)))
    status = "ok " if err < 0.5 else "FAIL"

    print(f"{status} {label:26s} {str(recovered.shape):15s} "
          f"err {err:6.3f}  psnr {quality:>6}  peak {peak:.3f}")
    return err < 0.5


if __name__ == "__main__":
    image = sample_image()
    results = [
        roundtrip(image.convert("L"), "L", False, "grayscale / open"),
        roundtrip(image.convert("L"), "L", True, "grayscale / locked"),
        roundtrip(image, "RGB", False, "colour / open"),
        roundtrip(image, "RGB", True, "colour / locked"),
        roundtrip(render_text_image("SPECTRAL", 48, 48), "L", True, "text / locked"),
    ]
    print()
    print("all passed" if all(results) else "SOME CHECKS FAILED")
