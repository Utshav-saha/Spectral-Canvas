"""End to end: RGB image -> WebP -> Reed-Solomon -> keyed byte shuffle -> 16-FSK
-> simulated GSM call -> back. Experiment only; nothing here is in the repo."""
import hashlib
import io
import sys

import numpy as np
from PIL import Image
import reedsolo

sys.path.insert(0, "/Users/utshavsaha/Documents/Spectral-Canvas/backend/spectral/tel")
sys.path.insert(0, "/Users/utshavsaha/Documents/Spectral-Canvas/backend/spectral")
import fsk_codec as fsk
import channel_sim
from common.security import derive_key

CALLER, RECEIVER, PIN = "12345678901", "10987654321", "1234"


def byte_permutation(n, caller, receiver, pin):
    seed = hashlib.sha256(hashlib.sha256(derive_key(caller, receiver, pin)).digest() + b"bytes").digest()
    return np.random.default_rng(int.from_bytes(seed, "big")).permutation(n)


def to_bits(b):
    return np.unpackbits(np.frombuffer(b, np.uint8))


def from_bits(bits):
    return np.packbits(np.asarray(bits, np.uint8)).tobytes()


def psnr(a, b):
    m = np.mean((np.asarray(a, float) - np.asarray(b, float)) ** 2)
    return float("inf") if m == 0 else 10 * np.log10(255 ** 2 / m)


def send(img, n, quality, nsym, loss, noise_db, seed, rx_pin=PIN):
    small = img.resize((n, n), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "WEBP", quality=quality)
    webp = buf.getvalue()

    rs = reedsolo.RSCodec(nsym)
    coded = bytes(rs.encode(webp))
    perm = byte_permutation(len(coded), CALLER, RECEIVER, PIN)
    shuffled = bytes(np.frombuffer(coded, np.uint8)[perm])

    bits = to_bits(shuffled)
    header = np.array([int(b) for b in format(len(shuffled), "016b")], np.uint8)
    audio, info = fsk.modulate(bits, fec=False, header=header)

    rx = channel_sim.channel(audio, loss_rate=loss, noise_db=noise_db, seed=seed)
    length, offset = fsk.read_header(rx)
    n_bytes = int("".join(map(str, length)), 2)
    rx_bits = fsk.demodulate(rx, dict(info, n_payload_bits=n_bytes * 8), offset=offset)
    rx_bytes = np.frombuffer(from_bits(rx_bits)[:n_bytes], np.uint8)

    wrong_bytes = int(np.sum(rx_bytes != np.frombuffer(shuffled, np.uint8)[:len(rx_bytes)]))
    perm_rx = byte_permutation(n_bytes, CALLER, RECEIVER, rx_pin)
    unshuffled = np.empty_like(rx_bytes)
    unshuffled[perm_rx] = rx_bytes
    try:
        decoded = bytes(rs.decode(bytes(unshuffled))[0])
        out = Image.open(io.BytesIO(decoded)).convert("RGB")
        quality_db = psnr(out, small)
        ok = decoded == webp
    except Exception:
        ok, quality_db = False, None
    return dict(bits=len(bits), seconds=info["duration_seconds"], wrong_bytes=wrong_bytes,
                total_bytes=n_bytes, ok=ok, psnr=quality_db, header_ok=n_bytes == len(shuffled))


if __name__ == "__main__":
    img = Image.open("/Users/utshavsaha/Documents/Spectral-Canvas/tests/fixtures/pepsi.jpg").convert("RGB")
    print("size  q   RS parity | loss noise | on air  | header | byte errors (worst) | exact recoveries")
    for n, q, nsym, loss, noise in [
        (64, 50, 32, 0.02, -45), (96, 50, 32, 0.02, -45), (96, 50, 32, 0.05, -45),
        (96, 50, 32, 0.02, -25), (128, 50, 32, 0.02, -45), (96, 50, 48, 0.05, -25),
    ]:
        runs = [send(img, n, q, nsym, loss, noise, seed) for seed in range(6)]
        worst = max(r["wrong_bytes"] for r in runs)
        mean_err = np.mean([r["wrong_bytes"] / r["total_bytes"] for r in runs])
        print(f"{n:4d} {q:3d}  {nsym:4d}      | {loss:4.0%} {noise:4d}dB | {runs[0]['seconds']:5.1f}s | "
              f"{sum(r['header_ok'] for r in runs)}/6    | {mean_err:6.2%} ({worst:3d})        | "
              f"{sum(r['ok'] for r in runs)}/6")

    r = send(img, 96, 50, 32, 0.02, -45, 0)
    w = send(img, 96, 50, 32, 0.02, -45, 0, rx_pin="9999")
    print(f"\n96x96 right PIN: exact={r['ok']} PSNR vs sent 96x96 = {r['psnr']:.1f} dB (WebP loss only)")
    print(f"96x96 wrong PIN: exact={w['ok']} (Reed-Solomon cannot decode the mis-shuffled bytes)")
