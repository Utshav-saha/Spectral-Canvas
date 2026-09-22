"""Generate (degraded, clean) activation pairs for the Track 1 restoration model.

Run from backend/ so `spectral` imports as a top-level package:

    python -m tools.make_dataset --sources data/sources      --out data/pairs_train
    python -m tools.make_dataset --sources data/sources_test --out data/pairs_test

Smoke test first (20 images, then LOOK at the output):

    python -m tools.make_dataset --sources data/sources --out data/pairs_smoke --limit 20

Stores float16 activation arrays, NOT pngs - the model trains on the 0..1
activation matrix before quantize_activation, so writing images here would
throw away exactly the precision we are trying to recover.

Output: shard_XXXX.npz with
    x : (N, 128, 128, 3) float16   degraded / model input
    y : (N, 128, 128, 3) float16   clean activation the encoder actually sent
plus shard_XXXX.json with the source filename and degradation chain per pair.

NOTE: targets the WAV path (44.1 kHz, no codec). Track 2 pairs need none of
this - see make_dataset_upscale() at the bottom.
"""

import os

# Each worker process does its own matrix multiplies. If BLAS also spawns a
# thread per core inside every worker, N workers x N threads fight over N cores
# and everything gets SLOWER. Pin BLAS to one thread per process. This must run
# before numpy is imported.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse
import hashlib
import json
from multiprocessing import Pool
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

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


# --------------------------------------------------------------------------
# degradation sampling
# --------------------------------------------------------------------------

def sample_chain(rng):
    """One random channel condition.

    ~15% clean, so the model learns to leave good input alone - otherwise it
    over-sharpens everything. Clipping is weighted up (~35% of degraded
    samples) because it is the one effect with no analytic inverse, so it is
    the model's main job.
    """
    if rng.random() < 0.15:
        return []

    chain = []
    kind = rng.choice(
        ["clip", "lowpass", "bandstop", "echo", "resample"],
        p=[0.35, 0.18, 0.17, 0.15, 0.15],
    )

    if kind == "lowpass":
        chain.append({"type": "lowpass", "cutoff": float(rng.uniform(2500, 7000))})
    elif kind == "bandstop":
        lo = float(rng.uniform(1500, 6000))
        chain.append({"type": "bandstop", "low": lo,
                      "high": lo + float(rng.uniform(300, 1200))})
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

from PIL import Image

TRANSFORMS = [None,
              Image.Transpose.FLIP_LEFT_RIGHT,
              Image.Transpose.FLIP_TOP_BOTTOM,
              Image.Transpose.ROTATE_180]


def encode_once(image):
    """Takes a PIL image now, so the caller can transform it first."""
    audio, metadata, activation = encode(
        image,
        target_width=COLS, target_height=ROWS,
        sample_rate=SAMPLE_RATE, frame_duration=FRAME_DURATION,
        gray_levels=GRAY_LEVELS, mode=MODE, security_enabled=False,
    )
    return audio, metadata, np.asarray(activation, dtype=np.float32)




def degraded_activation(audio, metadata, chain):
    out = apply_chain(audio, metadata["sample_rate"], chain)

    # alignment is known here, so slice directly instead of calling
    # synchronize() - its energy search can misfire on echoed/clipped audio,
    # and a column shift is a sync bug, not something the model should learn.
    per_channel = metadata["columns"] * metadata["frame_samples"]
    needed = 3 * per_channel
    if len(out) < needed:
        out = np.concatenate([out, np.zeros(needed - len(out))])
    out = out[:needed]

    # TODO(Phase 1): once the calibration column and echo deconvolution exist,
    # apply them here, between decode() and recover_activation(). The model
    # must train on post-inversion input, or it learns to redo work the
    # analytic stage already does. Until then this stores raw recovered input.
    channels = [
        recover_activation(decode(out[i * per_channel:(i + 1) * per_channel], metadata),
                           metadata)
        for i in range(3)
    ]
    return np.stack(channels, axis=-1).astype(np.float32)   # (H, W, 3)


def seed_for(path):
    """Seed from the filename, so a rerun - serial or parallel, any worker
    count - reproduces exactly the same pairs."""
    return int(hashlib.sha256(path.name.encode()).hexdigest()[:16], 16)


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------

# set once per worker by the pool initializer, so it is not pickled per task
_VARIANTS = 4


def _init_worker(variants):
    global _VARIANTS
    _VARIANTS = variants


def process_one(path_str):
    path = Path(path_str)
    try:
        rng = np.random.default_rng(seed_for(path))
        source = Image.open(path).convert("RGB")
        results = []

        for t_index, transform in enumerate(TRANSFORMS):
            image = source if transform is None else source.transpose(transform)
            audio, metadata, clean = encode_once(image)
            clean16 = clean.astype(np.float16)

            for v in range(_VARIANTS):
                chain = sample_chain(rng)
                x = degraded_activation(audio, metadata, chain).astype(np.float16)
                if x.shape != clean16.shape:
                    raise ValueError(f"shape mismatch: {x.shape} vs {clean16.shape}")
                results.append((x, clean16, {"source": path.name, "transform": t_index,
                                             "variant": v, "chain": chain}))
        return results, None

    except Exception as exc:
        return [], f"{path.name}: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# shard writing
# --------------------------------------------------------------------------

def write_shard(out_dir, index, x_buf, y_buf, meta_buf):
    np.savez_compressed(out_dir / f"shard_{index:04d}.npz",
                        x=np.stack(x_buf), y=np.stack(y_buf))
    (out_dir / f"shard_{index:04d}.json").write_text(json.dumps(meta_buf))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", required=True, help="directory of source images (searched recursively)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--variants", type=int, default=4, help="degradation conditions per source image")
    ap.add_argument("--shard-size", type=int, default=256, help="pairs per shard file")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--limit", type=int, default=None, help="only process the first N images (smoke test)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = sorted(str(p) for p in Path(args.sources).rglob("*")
                   if p.suffix.lower() in IMAGE_EXTS)
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        raise SystemExit(f"No images found under {args.sources}")

    print(f"{len(paths)} source images, {args.variants} variants each, "
          f"{args.workers} workers -> ~{len(paths) * args.variants} pairs")

    x_buf, y_buf, meta_buf = [], [], []
    shard = done = pairs = 0
    errors = []

    with Pool(args.workers, initializer=_init_worker, initargs=(args.variants,)) as pool:
        # imap (ordered), not imap_unordered: shard contents then come out the
        # same on every run, which matters when comparing model versions.
        for results, err in pool.imap(process_one, paths, chunksize=4):
            done += 1
            if err:
                errors.append(err)
                print(f"  skip {err}")

            for x, y, meta in results:
                x_buf.append(x)
                y_buf.append(y)
                meta_buf.append(meta)

            if len(x_buf) >= args.shard_size:
                write_shard(out_dir, shard, x_buf, y_buf, meta_buf)
                pairs += len(x_buf)
                print(f"  shard {shard:04d}  {len(x_buf)} pairs  (image {done}/{len(paths)})")
                x_buf, y_buf, meta_buf = [], [], []
                shard += 1

    if x_buf:
        write_shard(out_dir, shard, x_buf, y_buf, meta_buf)
        pairs += len(x_buf)
        shard += 1

    (out_dir / "errors.txt").write_text("\n".join(errors))
    print(f"done: {pairs} pairs in {shard} shards, {len(errors)} images skipped "
          f"(see errors.txt)")


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
