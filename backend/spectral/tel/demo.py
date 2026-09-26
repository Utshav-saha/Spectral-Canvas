
# Full demonstration:  image -> FSK audio -> simulated call -> image.

import numpy as np

import fsk_codec as fsk
import image_fsk
import channel_sim
from patterns import test_image


def pixels(activation):
    return np.rint((1.0 - np.clip(activation, 0, 1)) * 255.0)


def show(activation, title):
    """Crude ASCII preview so you can eyeball it without an image viewer."""
    ramp = " .:-=+*#%@"
    print(f"  {title}")
    for row in activation:
        print("    " + "".join(ramp[int(v * (len(ramp) - 1))] for v in row))


def main(size=24, levels=4, loss=0.02, seed=5):
    source = test_image(size, size, levels)

    payload_bits, seconds = image_fsk.budget(size, size, 1, levels)
    print(f"{size}x{size} image, {levels} gray levels")
    print(f"  payload {payload_bits} bits -> {seconds:.1f} s on the wire\n")

    header = np.array([int(b) for b in format(payload_bits, "016b")], np.uint8)
    audio, info = fsk.modulate(
        image_fsk.activation_to_bits(source, levels), header=header
    )

    fsk.__dict__.setdefault("_", None)
    from scipy.io.wavfile import write
    write("tx.wav", fsk.SAMPLE_RATE,
          (np.clip(audio, -1, 1) * 32767).astype(np.int16))

    received = channel_sim.channel(audio, loss_rate=loss, seed=seed)

    length, offset = fsk.read_header(received)
    n_bits = int("".join(map(str, length)), 2)
    info_rx = dict(info, n_payload_bits=n_bits)
    bits = fsk.demodulate(received, info_rx, offset=offset)
    recovered = image_fsk.bits_to_activation(bits, (size, size), levels)

    mae = float(np.mean(np.abs(pixels(recovered) - pixels(source))))
    exact = float(np.mean(np.isclose(pixels(recovered), pixels(source))))

    print(f"  header recovered: {n_bits} bits (sent {payload_bits})")
    print(f"  MAE {mae:.2f}   pixels exactly right {exact:.1%}\n")

    show(source, "sent")
    print()
    show(recovered, "received")
    print("\n  wrote tx.wav (8 kHz 16-bit mono)")


if __name__ == "__main__":
    main()
