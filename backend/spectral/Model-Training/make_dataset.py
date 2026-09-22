"""Generate (degraded, clean) activation pairs for the Track 1 restoration model.

Run from backend/ so `spectral` imports as a top-level package:
    python -m tools.make_dataset --sources data/sources --out data/pairs

Stores float16 activation arrays, NOT pngs - the model trains on the 0..1
activation matrix before quantize_activation, so writing images here would
throw away exactly the precision we are trying to recover.

NOTE: this targets the WAV path (44.1 kHz, no codec). Track 2 pairs need none
of this - see make_dataset_upscale() at the bottom.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from spectral.encoder.audio_encoder import encode
from spectral.decoder.stft_decoder import decode
from spectral.decoder.image_reconstructor import recover_activation
from spectral.channel.effects import apply_chain

ROWS = COLS = 128
MODE = "RGB"
SAMPLE_RATE = 44100
FRAME_DURATION = 0.1
GRAY_LEVELS = 16


# --------------------------------------------------------------------------
# degradation sampling
# --------------------------------------------------------------------------

def sample_chain(rng):
    """One random channel condition. Keep ~15% clean so the model learns to
    leave good input alone - otherwise it over-sharpens everything."""
    roll = rng.random()
    if roll < 0.15:
        return []

    chain = []
    kind = rng.choice(["lowpass", "bandstop", "echo", "clip", "resample"])

    if kind == "lowpass":
        chain.append({"type": "lowpass", "cutoff": float(rng.uniform(2500, 7000))})
    elif kind == "bandstop":
        lo = float(rng.uniform(1500, 6000))
        chain.append({"type": "bandstop", "low": lo, "high": lo + float(rng.uniform(300, 1200))})
    elif kind == "echo":
        chain.append({"type": "echo",
                      "delay": float(rng.uniform(0.02, 0.15)),
                      "decay": float(rng.uniform(0.2, 0.6))})
    elif kind == "clip":
        chain.append({"type": "clip", "threshold": float(rng.uniform(0.15, 0.5))})
    elif kind == "resample":
        chain.append({"type": "resample",
                      "target_rate": int(rng.choice([8000, 11025, 16000, 22050])),
                      "anti_alias": bool(rng.random() < 0.5)})

    # noise rides on top of most conditions, as it would in reality
    if rng.random() < 0.7:
        chain.append({"type": "noise", "snr_db": float(rng.uniform(10, 40))})

    return chain


# --------------------------------------------------------------------------
# the pipeline
# --------------------------------------------------------------------------

def clean_activation(path):
    """Ground truth: whole image resized to 128x128, NOT a random crop.

    A random 128x128 crop of a 2K photo is a texture patch. The real input is
    a whole image downscaled, which has completely different statistics.
    """
    image = Image.open(path).convert("RGB").resize((COLS, ROWS), Image.LANCZOS)
    return 1.0 - np.asarray(image, dtype=np.float32) / 255.0   # (H, W, 3)


def encode_once(path):
    """Cache-worthy step: encoding is slow, degrading is cheap."""
    audio, metadata, activation = encode(
        Image.open(path).convert("RGB"),
        target_width=COLS, target_height=ROWS,
        sample_rate=SAMPLE_RATE, frame_duration=FRAME_DURATION,
        gray_levels=GRAY_LEVELS, mode=MODE, security_enabled=False,
    )
    return audio, metadata


def degraded_activation(audio, metadata, chain):
    out = apply_chain(audio, metadata["sample_rate"], chain)

    # alignment is known here, so slice directly instead of calling
    # synchronize() - its energy search can misfire on echo/clipped audio and
    # a column shift is a sync bug, not something the model should learn.
    per_channel = metadata["columns"] * metadata["frame_samples"]
    needed = 3 * per_channel
    if len(out) < needed:
        out = np.concatenate([out, np.zeros(needed - len(out))])
    out = out[:needed]

    channels = [
        recover_activation(decode(out[i * per_channel:(i + 1) * per_channel], metadata),
                           metadata)
        for i in range(3)
    ]
    return np.stack(channels, axis=-1).astype(np.float32)   # (H, W, 3)


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", required=True, help="directory of source images")
    ap.add_argument("--out", required=True)
    ap.add_argument("--variants", type=int, default=4,
                    help="degradation conditions per source image")
    ap.add_argument("--shard-size", type=int, default=256)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    paths = sorted(p for p in Path(args.sources).rglob("*")
                   if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"})
    print(f"{len(paths)} source images")

    x_buf, y_buf, meta_buf, shard = [], [], [], 0

    for i, path in enumerate(paths):
        # seed from the filename so a rerun reproduces the same pairs
        seed = int(hashlib.sha256(path.name.encode()).hexdigest()[:16], 16)
        rng = np.random.default_rng(seed)

        try:
            audio, metadata = encode_once(path)
        except Exception as exc:
            print(f"  skip {path.name}: {exc}")
            continue

        clean = clean_activation(path)

        for v in range(args.variants):
            chain = sample_chain(rng)
            x_buf.append(degraded_activation(audio, metadata, chain).astype(np.float16))
            y_buf.append(clean.astype(np.float16))
            meta_buf.append({"source": path.name, "variant": v, "chain": chain})

        if len(x_buf) >= args.shard_size:
            np.savez_compressed(out / f"shard_{shard:04d}.npz",
                                x=np.stack(x_buf), y=np.stack(y_buf))
            (out / f"shard_{shard:04d}.json").write_text(json.dumps(meta_buf))
            print(f"  wrote shard {shard} ({len(x_buf)} pairs, image {i+1}/{len(paths)})")
            x_buf, y_buf, meta_buf, shard = [], [], [], shard + 1

    if x_buf:
        np.savez_compressed(out / f"shard_{shard:04d}.npz",
                            x=np.stack(x_buf), y=np.stack(y_buf))
        (out / f"shard_{shard:04d}.json").write_text(json.dumps(meta_buf))

    print("done")


# --------------------------------------------------------------------------
# Track 2: no audio pipeline needed at all
# --------------------------------------------------------------------------

def make_dataset_upscale(path, small=32, levels=4):
    """Generation B pairs. Bits arrive exact after Hamming(7,4), so the only
    loss is the downscale + quantisation done before transmitting. Running the
    GSM simulation here would be an expensive identity function."""
    image = Image.open(path).convert("RGB")
    clean = np.asarray(image.resize((128, 128), Image.LANCZOS), dtype=np.float32) / 255.0

    tiny = np.asarray(image.resize((small, small), Image.LANCZOS), dtype=np.float32) / 255.0
    tiny = np.round(tiny * (levels - 1)) / (levels - 1)

    return tiny, clean


if __name__ == "__main__":
    main()
