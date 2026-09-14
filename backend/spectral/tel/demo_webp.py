"""
RGB image -> WebP -> Reed-Solomon -> scramble -> 16-FSK -> simulated call -> image.

    python3 demo_webp.py                          # built-in test picture, 96px, open
    python3 demo_webp.py photo.jpg --size 128     # your own picture
    python3 demo_webp.py --lock                   # locked with the demo numbers + PIN
    python3 demo_webp.py --lock --wrong-pin       # what a wrong PIN gets you
    python3 demo_webp.py --loss 0.05 --noise -25  # a worse call

Writes into --out (default: current directory):
    tx.wav        8 kHz 16-bit mono, ready for run_local_call.sh
    sent.png      the resized picture that went on the wire
    received.png  what came back, or static.png if it could not be decoded
"""

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw
from scipy.io.wavfile import write

import channel_sim
import fsk_codec as fsk
import image_webp

CALLER, RECEIVER, PIN = "12345678901", "10987654321", "1234"


def sample_image(size=256):
    """A colourful fallback picture: gradients, shapes and a little text."""
    x = np.linspace(0, 1, size)
    r = np.tile(x, (size, 1))
    g = r.T
    b = 1 - (r + g) / 2
    img = Image.fromarray((np.dstack([r, g, b]) * 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    s = size
    draw.ellipse([s * .1, s * .1, s * .45, s * .45], fill=(230, 60, 50))
    draw.rectangle([s * .55, s * .15, s * .9, s * .5], fill=(40, 90, 200))
    draw.polygon([(s * .2, s * .9), (s * .5, s * .55), (s * .8, s * .9)], fill=(250, 210, 40))
    draw.text((s * .05, s * .02), "Spectral Canvas", fill=(255, 255, 255))
    return img


def default_image():
    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.join(here, "..", "..", "..", "tests", "fixtures", "pepsi.jpg")
    return Image.open(fixture) if os.path.exists(fixture) else sample_image()


def psnr(a, b):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if a.shape != b.shape:
        return None
    mse = np.mean((a - b) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", nargs="?", help="picture to send (default: test fixture)")
    p.add_argument("--size", type=int, default=image_webp.DEFAULT_SIZE, help="longest side in pixels")
    p.add_argument("--quality", type=int, default=image_webp.DEFAULT_QUALITY, help="WebP quality 0-100")
    p.add_argument("--loss", type=float, default=0.02, help="packet loss rate")
    p.add_argument("--noise", type=float, default=-45.0, help="noise floor in dBFS")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--lock", action="store_true", help="scramble with the demo numbers and PIN")
    p.add_argument("--wrong-pin", action="store_true", help="receive with PIN 9999")
    p.add_argument("--out", default=".")
    args = p.parse_args()

    source = Image.open(args.image) if args.image else default_image()
    creds = dict(caller=CALLER, receiver=RECEIVER, pin=PIN) if args.lock else {}

    audio, sent, report = image_webp.send_image(source, args.size, args.quality, **creds)
    print(f"sent {report['width']}x{report['height']} RGB, WebP q{args.quality}: "
          f"{report['webp_bytes']} bytes -> {report['packet_bytes']} with parity")
    print(f"  compression alone: PSNR {report['compression_psnr']:.1f} dB against the resized original")
    print(f"  {report['seconds']:.1f} s on the wire, {'locked' if report['locked'] else 'open'}")

    os.makedirs(args.out, exist_ok=True)
    write(os.path.join(args.out, "tx.wav"), fsk.SAMPLE_RATE,
          (np.clip(audio, -1, 1) * 32767).astype(np.int16))
    sent.save(os.path.join(args.out, "sent.png"))

    received = channel_sim.channel(audio, loss_rate=args.loss, noise_db=args.noise, seed=args.seed)
    print(f"\nsimulated call: GSM 06.10, {args.loss:.0%} packet loss, noise {args.noise:.0f} dB")

    rx_creds = dict(creds, pin="9999") if (args.lock and args.wrong_pin) else creds
    image, rx = image_webp.receive_image(received, **rx_creds)

    if image is None:
        path = os.path.join(args.out, "static.png")
        rx["static"].save(path)
        print(f"  could not rebuild: {rx['reason']}")
        print(f"  wrote {path}")
        return

    quality = psnr(image, sent)
    print(f"  rebuilt {rx['width']}x{rx['height']}, Reed-Solomon repaired {rx['repaired_bytes']} bytes")
    print(f"  identical to what was sent: {quality == float('inf')}"
          + ("" if quality in (None, float("inf")) else f" (PSNR {quality:.1f} dB vs sent)"))
    image.save(os.path.join(args.out, "received.png"))
    print(f"  wrote {os.path.join(args.out, 'received.png')}")


if __name__ == "__main__":
    main()
