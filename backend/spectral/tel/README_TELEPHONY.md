# Sending your images over a voice call

## The measured result that drives everything

| scheme | through GSM 06.10 + 2% loss + AGC + noise |
|---|---|
| your current one (16 rows sounding at once, amplitude = pixel) | **17.7% bit error**, binary, with perfect sync and pilot normalisation |
| 16-FSK, one tone per 40 ms symbol | **0.25% symbol error**, 100 bit/s |

I tried to rescue the parallel scheme first: narrowband frequency plan, 2/4/6/9
pilot tones for gain correction, 8 to 32 rows, 2 to 16 gray levels, per-column
resync. It stays at 17–20% no matter what.

The reason is structural. GSM 06.10 represents each 20 ms frame as an **8-pole
LPC envelope plus a sparse pulse residual**. Eight poles cannot describe 16
independent spectral lines at correct relative levels. AMR-NB on a real
cellular call is the same family and behaves the same way.

What survives perfectly is *which* frequency is present. What does not survive
is *how loud* it is. So: one tone at a time, information in the tone's
identity, decoder does `argmax` over 16 bins and never compares a magnitude to
a threshold.

---

## Answer on VoIP services

**You do not need one.** `run_local_call.sh` runs two `pjsua` instances on your
MacBook calling each other over loopback — real SIP signalling, real RTP, real
GSM encoding, real jitter buffer, no account, no provider, free forever.

```
brew install pjproject          # pjproject, NOT pjsip
which pjsua                     # empty? build from source, see the script header
./run_local_call.sh tx.wav rx.wav
```

**Verify which codec actually got used, every run.** If GSM was not compiled
into your build, pjsua silently negotiates PCMU (G.711) instead. G.711 does no
LPC modelling, so it is nearly transparent — your images would decode perfectly
and the result would be meaningless.

```
grep -iE "sdp|codec|GSM|PCMU" /tmp/pjsua_caller.log | head -20
```

Other macOS specifics: zsh aborts on an unquoted `*`, so `--dis-codec='*'` must
stay quoted; `--null-audio` is what keeps macOS from prompting for microphone
access, so don't drop it; and if pjsua fights you, run it in a Debian container
instead — loopback between two processes in one container behaves identically.

If you want a real network hop, free SIP accounts exist (sip.linphone.org,
OnSIP). **Twilio is not free** — the trial is time-limited credit, and it
injects a spoken announcement at call start that will land on top of your
preamble. Skip it unless you specifically need PSTN termination to a real
mobile, and even then only at the end for one confirmation run.

Swap the codec with `CODEC=iLBC ./run_local_call.sh` etc. to compare — that
table is a good result for your writeup.

---

## Changes to your existing files

### `audio_encoder.py` — replaced for the telephony path

Keep it as-is for the high-quality webapp mode. For calls, use `fsk_codec.py`.
If you want to know why each parameter had to change:

| yours | telephony | reason |
|---|---|---|
| `sample_rate=44100` | 8000 | the call *is* 8 kHz |
| `f_min=1000, f_max=8000` | 700–3200 | usable band is ~300–3400 Hz; **34 of your 64 rows currently sit above 3400 Hz and arrive as nothing** |
| `np.hanning(frame_samples)` | `tukey(alpha=0.25)` | Hann drives every frame edge to zero; that periodic near-silence invites VAD/DTX |
| `frame_duration=0.1` | 0.04 | keep it a whole multiple of 20 ms either way, so a symbol never straddles a codec frame raggedly |
| `gray_levels=16` | 4 (as *bits*, not amplitudes) | amplitude carries no reliable information |

### `image_reconstructor.py` — `recover_activation` must go

```python
scale = gain * window_sum / 2.0
activation = magnitude_matrix / scale
```

This divides by `normalization_gain` from metadata, i.e. it assumes the channel
preserved absolute amplitude. It did not. `image_fsk.bits_to_activation`
replaces it. Everything else in this file stays: `activation_to_pixels`,
`save_image`, `to_png_bytes`, `mae/mse/psnr`.

### `synchronizer.py` — `find_start_by_energy` is too coarse

10% of peak, frame-granular. Over a codec the onset is soft and you land half a
frame off, which smears every column into its neighbour. `fsk_codec.find_preamble`
replaces it: slides the symbol grid and scores how sharply energy concentrates
into the expected preamble tone.

`find_start_by_correlation` cannot be rescued at all — CELP does not preserve
waveform phase, so cross-correlating the raw samples is meaningless after the
codec. Sync has to happen in the magnitude domain.

### `security.py` / `decrypter.py` — keep the scramble, **drop the mask**

`scramble` / `unscramble` permute the activation matrix, upstream of the modem.
They work unchanged — call `encrypt()` before `activation_to_bits()` and
`decrypt()` after `bits_to_activation()`.

`generate_mask` / `remove_mask` must be disabled:

```python
x[n] = y[n] - alpha * m[n]
```

is an exact sample-for-sample cancellation. An RTP path has a jitter buffer,
packet loss concealment that fabricates 20 ms of audio, and clock drift. You
will never have sample-exact alignment, so the subtraction adds a full-strength
noise burst instead of removing one. It is also broadband noise, which is
exactly what a speech codec discards first.

### `image_preprocessor.py` — unchanged

`process_image`, `resize_image`, `to_activation`, `quantize`,
`render_text_image` all still apply. Just call it with a smaller target size.

---

## New files

| file | role |
|---|---|
| `fsk_codec.py` | the modem: bits ↔ audio, preamble sync, Hamming(7,4), interleaving, self-describing header |
| `image_fsk.py` | activation matrix ↔ bitstream; `budget()` tells you the call length |
| `channel_sim.py` | offline GSM roundtrip + packet loss + AGC + noise, ~1 s per trial |
| `demo.py` | full round trip with an ASCII preview, writes `tx.wav` |
| `run_local_call.sh` | two `pjsua` instances, real SIP call, free |
| `tel_*.py`, `test_pipeline.py` | the narrowband multitone attempt, kept so you can reproduce the 17.7% result for your report |

---

## Time budget — pick your image size from this

At 100 bit/s raw, ~57 bit/s after FEC:

| image | bits | call length |
|---|---|---|
| 16×16, 4 levels | 512 | 9.3 s |
| 24×24, 4 levels | 1152 | 20.5 s |
| 32×32, 2 levels | 1024 | 18.2 s |
| 32×32, 4 levels | 2048 | 36.2 s |
| 32×32, 16 levels | 4096 | 72 s |
| **64×64 RGB, 16 levels (your current default)** | 49152 | **14 minutes** |

All of the above decoded with **0 bit errors** through the simulated channel
except the last. Your current default is not viable over a voice channel at any
quality setting — this is a bandwidth limit, not a tuning problem.

Practical recommendation: **24×24 at 4 levels**, about 20 s per call. Send text
through `render_text_image` at the same size and it reads fine.

---

## Two things left to do

1. **RGB.** Three channels triples the time. Either send grayscale for calls, or
   subsample chroma — full-resolution luma plus 2× downsampled colour costs
   about half of naive RGB.
2. **A retry path.** At 5% packet loss I measured 0.1% residual bit error. Add a
   CRC per 128-bit block so the receiver can mark blocks as bad rather than
   showing corrupt pixels.
