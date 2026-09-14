"""Throughput sweep: how many bits per second survive the simulated GSM call?

K simultaneous tones, each picked from its own sub-band of M tones, one symbol
every Ts. Information is only ever in WHICH tone is lit (argmax per sub-band),
never in how loud it is.
"""
import sys
import numpy as np
from scipy.signal.windows import tukey

sys.path.insert(0, "/Users/utshavsaha/Documents/Spectral-Canvas/backend/spectral/tel")
import channel_sim as cs

FS = 8000


def plan(K, M, sym_ms, f_lo=500.0, f_hi=3300.0):
    N = FS * sym_ms // 1000
    bw = FS / N
    edges = np.linspace(f_lo, f_hi, K + 1)
    bands = []
    for k in range(K):
        width = edges[k + 1] - edges[k]
        spacing = width / M
        f = edges[k] + spacing * (np.arange(M) + 0.5)
        bins = np.round(f / bw).astype(int)
        bands.append(bins)
    return N, bw, bands


def run(K, M, sym_ms, n_symbols=1500, loss=0.02, noise_db=-45.0, seed=0):
    N, bw, bands = plan(K, M, sym_ms)
    spacing_bins = np.min(np.diff(bands[0]))
    if spacing_bins < 1 or len(set(np.concatenate(bands))) < K * M:
        return None
    rng = np.random.default_rng(seed)
    sym = rng.integers(0, M, size=(n_symbols, K))
    t = np.arange(N) / FS
    win = tukey(N, 0.25)
    audio = np.zeros(n_symbols * N)
    for k in range(K):
        freqs = bands[k][sym[:, k]] * bw
        audio += (np.sin(2 * np.pi * freqs[:, None] * t[None, :]) * win).ravel()
    audio = audio / np.max(np.abs(audio)) * 0.7

    rx = cs.gsm_roundtrip(audio)
    rx = cs.packet_loss(rx, loss, seed=seed)
    rx = cs.agc(rx, seed=seed)
    rx = rx + 10 ** (noise_db / 20) * rng.standard_normal(len(rx))

    frames = rx[: n_symbols * N].reshape(n_symbols, N) * np.hanning(N)
    spec = np.abs(np.fft.rfft(frames, axis=1))
    got = np.stack([np.argmax(spec[:, b], axis=1) for b in bands], axis=1)
    ser = float(np.mean(got != sym))
    bits = int(np.log2(M))
    # bit errors: count differing bits between sent and received symbol index
    diff = got ^ sym
    ber = float(np.mean([bin(int(x)).count("1") for x in diff.ravel()])) / bits
    return dict(rate=K * bits * 1000 / sym_ms, ser=ser, ber=ber, spacing_hz=spacing_bins * bw)


configs = [
    (1, 16, 40), (1, 16, 20), (1, 32, 20), (1, 64, 40),
    (2, 16, 40), (2, 8, 20), (2, 16, 20),
    (3, 8, 40), (3, 16, 40), (3, 8, 20), (4, 4, 20), (4, 8, 40),
]

print("tones x M  sym   raw bps  spacing |  BER @2% loss  |  BER @5% loss  |  BER @2% loss, -25dB noise")
for K, M, ms in configs:
    rows = []
    for loss, noise in [(0.02, -45.0), (0.05, -45.0), (0.02, -25.0)]:
        r = [run(K, M, ms, loss=loss, noise_db=noise, seed=s) for s in range(3)]
        if r[0] is None:
            break
        rows.append(np.mean([x["ber"] for x in r]))
    if not rows:
        print(f"{K} x {M:2d}    {ms}ms  (tones do not fit on distinct bins)")
        continue
    info = run(K, M, ms, n_symbols=10)
    print(f"{K} x {M:2d}    {ms}ms  {info['rate']:6.0f}   {info['spacing_hz']:5.0f}Hz | "
          + " | ".join(f"{b:12.3%}  " for b in rows))
