"""Score a call recording before you spend an evening on a wrong theory.

    python tools/check_recording.py recording.mkv [tx.wav]

Run from backend/. With the tx.wav it also reports how many symbols actually
survived, which is the only number that predicts the picture. Without it you
still get the level and the margin.

Why the margin is the number to watch: the decoder takes an argmax over the
sixteen tone bins and never compares loudness, so what decides a symbol is how
decisively the winning tone beat the runner-up. Measured:

    clean loopback          200-340   exact
    860 s real call          10-17    93.6% of pixels once realigned
    36 s real call            4.8     58%  - Hamming miscorrects, not repairs

Below about 10 the FEC stops repairing errors and starts inventing them, so a
recording that scores low is not worth decoding twice: change something on the
phone and record again.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "spectral", "tel"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fsk_codec as fsk                                    # noqa: E402
from app.services import tel_pipeline as tel               # noqa: E402


def load(path):
    raw = open(path, "rb").read()
    if path.lower().endswith((".wav", ".wave")):
        from scipy.io import wavfile
        rate, pcm = wavfile.read(path)
        audio = pcm.astype(np.float64)
        audio /= 32768.0 if pcm.dtype == np.int16 else max(np.abs(audio).max(), 1)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, {"sample_rate_in": rate, "codec": "pcm"}
    return tel.read_any_audio(raw, os.path.basename(path))


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    audio, source = load(argv[0])
    print(f"file      {os.path.basename(argv[0])}")
    print(f"source    {source.get('codec')} at {source.get('sample_rate_in')} Hz"
          f" -> 8000 Hz, {len(audio)/8000:.2f}s")

    peak = float(np.abs(audio).max())
    rms = float(np.sqrt(np.mean(audio ** 2)))
    railed = float(np.mean(np.abs(audio) > 0.995))
    print(f"level     peak {peak:.4f}  rms {rms:.4f}  "
          f"crest {peak/max(rms,1e-9):.2f}  at the rail {railed*100:.3f}%")

    offset = fsk.find_preamble(audio, search_seconds=min(
        12.0, max(1.0, len(audio) / 8000 - 1)))
    print(f"preamble  at {offset} ({offset/8000:.3f}s)")

    start = offset + len(fsk.PREAMBLE) * fsk.SYMBOL_SAMPLES
    count = min(512, max(1, (len(audio) - start) // fsk.SYMBOL_SAMPLES))
    def margin_at(shift):
        ranked = np.sort(fsk._symbol_magnitudes(audio, start + shift, count),
                         axis=1)
        return float(np.median(ranked[:, -1] / (ranked[:, -2] + 1e-12)))

    # Margin has to be read at the best alignment, not wherever the preamble
    # happened to land. Measured on a real call, the preamble's offset scored
    # a margin of 19.9 while reading only 57.8% of symbols correctly, and the
    # alignment 110 samples later scored 4.8 and read 83.2%: a window sitting
    # mostly inside the *previous* symbol looks decisive and is wrong.
    margin = max(margin_at(s) for s in range(-320, 321, 10))
    verdict = ("exact" if margin > 100 else
               "usable" if margin > 10 else
               "TOO DAMAGED - record again, do not trust the picture")
    print(f"margin    {margin:.1f} over {count} symbols   -> {verdict}")

    if len(argv) > 1:
        sent, _ = load(argv[1])
        ref = np.argmax(fsk._symbol_magnitudes(
            sent, len(fsk.PREAMBLE) * fsk.SYMBOL_SAMPLES, count), axis=1)
        best, at = -1.0, 0
        for shift in range(-320, 321, 10):
            got = np.argmax(fsk._symbol_magnitudes(
                audio, start + shift, count), axis=1)
            agree = float(np.mean(ref == got))
            if agree > best:
                best, at = agree, shift
        fixed = float(np.mean(ref == np.argmax(
            fsk._symbol_magnitudes(audio, start, count), axis=1)))
        print(f"symbols   {fixed*100:.1f}% correct as aligned, "
              f"{best*100:.1f}% at best ({at:+d} samples)")
        print("          about 90% is where a picture starts being worth having")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
