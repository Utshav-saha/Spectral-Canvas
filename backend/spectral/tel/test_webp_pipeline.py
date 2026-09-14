"""Sweep the WebP path through the simulated call and report how often it survives.

    python3 test_webp_pipeline.py
"""

import numpy as np

import channel_sim
import image_webp
from demo_webp import default_image, CALLER, RECEIVER, PIN

SEEDS = range(4)


def trial(image, size, loss, noise, seed, lock=False, rx_pin=None):
    creds = dict(caller=CALLER, receiver=RECEIVER, pin=PIN) if lock else {}
    audio, sent, report = image_webp.send_image(image, size, **creds)
    received = channel_sim.channel(audio, loss_rate=loss, noise_db=noise, seed=seed)
    if lock and rx_pin:
        creds = dict(creds, pin=rx_pin)
    out, rx = image_webp.receive_image(received, **creds)
    exact = out is not None and np.array_equal(np.asarray(out), np.asarray(sent))
    return exact, report


def main():
    image = default_image()

    print("round trip with no call at all (must be exact):")
    audio, sent, report = image_webp.send_image(image, 96)
    out, _ = image_webp.receive_image(audio)
    print(f"  96px open: exact={out is not None and np.array_equal(np.asarray(out), np.asarray(sent))}")

    print("\nthrough the simulated GSM call:")
    print("  size  loss  noise  | on air  | exact")
    for size, loss, noise in [(64, 0.02, -45), (96, 0.02, -45), (128, 0.02, -45),
                              (96, 0.05, -45), (96, 0.02, -25)]:
        results = [trial(image, size, loss, noise, s) for s in SEEDS]
        seconds = results[0][1]["seconds"]
        print(f"  {size:4d}  {loss:4.0%}  {noise:4d}dB | {seconds:5.1f}s | "
              f"{sum(r[0] for r in results)}/{len(results)}")

    print("\nlocked, 96px, 2% loss:")
    right = [trial(image, 96, 0.02, -45, s, lock=True)[0] for s in SEEDS]
    wrong = [trial(image, 96, 0.02, -45, s, lock=True, rx_pin="9999")[0] for s in SEEDS]
    print(f"  right PIN exact: {sum(right)}/{len(right)}")
    print(f"  wrong PIN exact: {sum(wrong)}/{len(wrong)}  (should be 0)")


if __name__ == "__main__":
    main()
