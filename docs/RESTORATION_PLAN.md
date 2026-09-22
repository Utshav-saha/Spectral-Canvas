# RESTORATION_PLAN.md

Roadmap for adding channel-inversion and a learned restoration step to
Spectral Canvas. Supersedes the earlier version of this file.

**Core principle: cascade, don't replace.** Classical DSP inverts everything
that is LTI. The model handles only what provably has no inverse — clipping
(nonlinear), rows annihilated by a stop-band, and aliasing fold-down. The model
takes the *output* of the analytic stage as its input, never the raw degraded
signal. This is both less work and a better report.

---

## What ships

| | Path | Image | Time | Status |
|---|---|---|---|---|
| **Track 1** | WAV, 44.1 kHz, no codec | 128×128 RGB | 38 s | primary deliverable |
| **Track 2** | Gen B (16-FSK) over GSM/SIP | 32×32 RGB, 4 levels | ~1.8 min | working; add upscaler |
| ~~Track 3~~ | Gen A over GSM | — | — | **cut** (see Rejected) |

---

## Phase 0 — Prerequisite (do first, ~1 hour)

`encode_activation_matrix` loops in Python over columns *and* rows: 49,152
iterations for 128×128 RGB, a few seconds per image. Dataset generation needs
thousands of encodes, so this must be a matrix multiply:

```python
sine_table = np.sin(2*np.pi*np.outer(row_frequencies, t))   # (rows, frame_samples)
frames = (activation.T @ sine_table) * window               # (cols, frame_samples)
audio  = frames.ravel()
```

Bit-identical, ~100× faster, speeds up the app too.

Check `backend/spectral/encoder/audio_encoder.py` first — CLAUDE.md describes
per-row start phases that aren't in the diverged top-level copy. If they exist,
the table becomes `np.sin(2*np.pi*f_r*t + phi_r)`.

Verify with `python -m tests.test_roundtrip` — all five paths must still report
0.000 mean pixel error.

---

## Phase 1 — Analytic channel inversion (the Signals and Systems core)

### 1a. Calibration column

Prepend one frame with every row at amplitude 1.0. Costs 0.1 s of audio.

At the receiver, for a bin-centred sinusoid:

```
|X_cal[k_r]| = 1 * g * |H(f_r)| * sum(hann) / 2
```

The clean reference (no channel) is `g * sum(hann) / 2`, so:

```
|H(f_r)| = |X_cal[k_r]| / (g * sum(hann) / 2)
```

That's a direct, blind, per-row estimate of the channel magnitude response.
It requires no knowledge of what the channel did.

Then correct every column:

```python
magnitude_matrix[r, :] /= H_est[r]
```

**This single step inverts low-pass, band-stop, resampling-with-anti-alias, and
any other unknown linear filtering** — because each row is a single frequency,
so an LTI channel can only scale it.

### 1b. Dead-row thresholding

A stop-band doesn't attenuate, it annihilates. Dividing by `H_est[r] = 0.001`
amplifies bin noise to full scale.

```python
DEAD = 0.05
dead_rows = H_est < DEAD
```

Mark those rows in a **confidence mask** and pass it to the model as an input
channel. Do not divide them. This mask is also what the UI shows the receiver:
*here is what arrived, here is what was guessed.*

### 1c. Echo deconvolution

Forward: `h[n] = delta[n] + decay*delta[n-D]`, so `H(z) = 1 + decay*z^(-D)`.
The inverse `1/(1 + decay*z^(-D))` is stable for |decay| < 1, which
`effects.py` guarantees. Implement recursively on the audio, before framing:

```python
x[n] = y[n] - decay * x[n-D]
```

Estimate D and decay from the autocorrelation of the received audio: peak at
lag D, height ~ decay. Classic, cheap, and a good derivation for the report.

### 1d. Frame-energy normalisation (robustness, cheap)

Normalise each frame by its own total energy so only *ratios* between rows
carry information. Removes all sensitivity to unknown overall gain / AGC.
Costs nothing; do it.

### 1e. What has no inverse

| Effect | Why | Handled by |
|---|---|---|
| Clipping | memoryless **nonlinear** — no `h[n]`, no `H(f)`. Intermodulation (2f1-f2) puts spurious energy on other rows | model |
| Dead rows | information destroyed, not attenuated | model (interpolate from neighbours) |
| Aliasing (anti_alias=False) | two rows sum into one bin | model, partially |

**These three are the model's entire job on Track 1.** Say so explicitly in
the report.

---

## Phase 2 — Dataset

See `tools/make_dataset.py`. Key points:

**Sources** (~8,000 total, all free):

| Dataset | Count | Role |
|---|---|---|
| DIV2K + Flickr2K (DF2K) | 3,450 | natural photos; standard restoration benchmark |
| Quick, Draw! (vector, rendered at 128×128) | ~2,000 | matches `DoodleCanvas` |
| LLD-logo (400×400) | ~1,500 | logos, flat colour regions |
| TU-Berlin sketches | ~1,000 | denser line art |

Use the **vector** Quick, Draw! ndjson and render at 128×128 — the released
28×28 bitmaps are useless here.

**Resize whole images; do not crop patches.** A random 128×128 crop of a 2K
photo is a texture patch. The real input is a whole image downscaled. Different
statistics entirely; this mismatch produces good validation numbers and
disappointing real output.

**Store the model's actual input**, i.e. post-analytic-inversion activations,
float16, `(3,128,128)`. Not PNGs — that re-quantises away the precision being
recovered. ~6 GB for 8,000 x 4 variants.

**Pipeline per pair:** encode (cached) -> `apply_chain` -> slice (skip
`synchronize()`, alignment is known) -> decode -> calibration inversion -> echo
deconvolution -> `recover_activation`.

**Degradation sampling:** randomise per sample. Keep ~15% clean, or the model
learns its job is always to change something and over-sharpens good input.
Seed from the filename so reruns reproduce.

**Emphasise clipping.** It's the only effect with no analytic inverse, so
weight it higher than the LTI effects — maybe 35% of degraded samples.

---

## Phase 3 — Model

Small U-Net. Base 32 channels, 3 encoder levels (128->64->32->16), residual
blocks, ~2-5M params. Not NAFNet/Restormer/SwinIR as published — those target
256x256+ photographic restoration and hold far more capacity than this problem
contains. Upgrade path if it plateaus: swap conv blocks for NAFBlocks, keep the
small U-Net shape.

**Input: 5 channels.**

| Channel | Contents | Why |
|---|---|---|
| 0-2 | post-inversion activation (R, G, B) | the image |
| 3 | normalised row index | degradations are axis-aligned; a conv net is translation-equivariant and cannot otherwise know row 0 is 8 kHz and row 127 is 1 kHz |
| 4 | confidence mask from 1b | tells the model which rows are guesses, not data |

Output: 3 channels, clean activation.

---

## Phase 4 — Training

- **L1 loss.** Optionally + 0.1*(1-SSIM).
- **No GAN or perceptual loss.** They optimise for looking-real, which is the
  exact failure mode to avoid (see Trustworthiness below).
- AdamW, cosine schedule, ~150 epochs, batch 16-32.
- Predict the **residual** (clean - input) rather than the image; most of the
  input is already correct after Phase 1, so this converges faster.

---

## Phase 5 — Evaluation

Report **three columns**, per degradation type, never averaged:

| | raw degraded | + analytic inversion | + model |
|---|---|---|---|
| low-pass | | | |
| band-stop | | | |
| echo | | | |
| **clipping** | | | |
| resample (aliased) | | | |

The analytic column is the honest baseline. A learned model measured only
against raw degraded output looks far better than it is. Low-pass and echo
should be nearly solved by column 2 — that is the expected and correct result,
not a disappointment.

Hold out 100-200 **real** samples (actual playback-and-record, actual
round-trips) for evaluation only. Synthetic training plus synthetic evaluation
will tell you the model works when it doesn't.

---

## Track 2 — Phone call (separate, simpler)

Generation B unchanged. 32x32 RGB at 4 levels, ~1.8 min at ~57 bit/s.

**No audio simulation needed for the dataset.** Bits arrive exact after
Hamming(7,4), so the pair is pure image ops:

```python
clean    = image.resize((128,128))
degraded = quantize(image.resize((32,32)), levels=4)
```

Running `channel_sim.py` here would be an expensive identity function.

Model's job: upscale + dequantize 32x32x4-level -> 128x128x256. Same U-Net,
input 3 channels (no row index — there is no frequency axis in this task).

**Be explicit in the report that the added detail is inferred from a learned
prior, not transmitted.** Report MAE alongside the perceptual result so the
distinction is visible.

### Real-call errors the model does NOT fix

| Problem | Fix |
|---|---|
| Uncorrected bit errors | median/impulse filter, or stronger ECC — **not** the restoration model, which blends wrong 4-bit values into neighbours and spreads the damage |
| Clock drift over minutes | periodic re-sync |
| Packet loss (burst) | interleaver depth must exceed expected burst length |
| Comfort-noise / silence suppression | keep the signal continuously active |

AGC is harmless to B — `argmax` ignores absolute level. That immunity is B's
entire reason for existing.

---

## Trustworthiness (report section, costs nothing to write)

A model can improve MAE while making the system *less* trustworthy.

- Without the model: 20% error, visibly broken, receiver knows to ask for a
  resend. The failure is loud.
- With the model: 6% error, looks like a clean photograph — but those 6% are
  invented pixels that look exactly as convincing as the correct ones. A
  handwritten 3 reads as an 8 and nobody knows.

A receiver's ability to tell "this arrived correctly" from "this didn't" is
itself a feature. Two design consequences, both already above: L1 not GAN, and
surface the confidence mask in the UI so guessed regions are labelled rather
than silently blended in.

---

## Rejected

- **Generation A over a phone call (Track 3).** GSM 06.10's 8-pole LPC gives
  ~4 resonances to describe N simultaneous tone amplitudes — a 4:1 projection
  at 32 rows, 32:1 at 128. Genuinely many-to-one, so a model can only pick a
  likely preimage from a prior. Also maximally exposed to AGC, since absolute
  level *is* the payload, and `recover_activation` relies on a
  `normalization_gain` that no real call carries. Expect roughly halving the
  error, not eliminating it, with strongly content-dependent results.
- **Generation A at 128 rows.** Error scales with carrier count; this triples
  down on A's one weakness. Per-tone level also drops to ~-28 dBFS.
- **128x128 over B uncompressed.** 196,608 bits ~ 57 min.
- **Generation C (WebP + Reed-Solomon).** Out of syllabus scope. Worth one
  report sentence: this is the gap C fills, so without it, high-resolution over
  a voice call is unavailable.

---

## Critical path, in order

1. Phase 0 — vectorise encoder, `test_roundtrip` still 0.000
2. Phase 1a/1b — calibration column + dead-row mask
3. Phase 5 table, columns 1 and 2 only — **this alone is a complete,
   defensible result**
4. Phase 1c/1d — echo deconvolution, frame-energy normalisation
5. Phase 2 — dataset
6. Phases 3-4 — model
7. Phase 5 column 3
8. Track 2 upscaler

**Cut list, in order, if time runs short:** Track 2 upscaler -> the model
entirely (steps 5-7) -> echo deconvolution.

Stopping after step 3 still gives a coursework result with a derivation, an
implementation and a measurement. Stopping after step 4 gives a strong one. The
model is the upside, not the foundation — do not let it block the parts that
are certain to work.
