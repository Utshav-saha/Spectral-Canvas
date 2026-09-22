# Rejected: Generation A over a voice call (Track 3)

These files are **cut from the product** and are not imported by anything that
ships. They are kept because the measurement they produce is a result the
report needs: it is the evidence for why Track 2 exists.

| file | what it is |
|---|---|
| `tel_config.py` | narrowband (8 kHz, 24 rows, 4 levels) parameters |
| `tel_encoder.py` | the main library's parallel multitone scheme, plus two pilot tones for gain correction |
| `tel_decoder.py` | pilot-based sync and per-frame level normalisation |
| `test_pipeline.py` | encode -> simulated GSM call -> decode, reports the error |

## Why it was cut

GSM 06.10 models each 20 ms frame as an 8-pole LPC envelope. Eight poles give
about four resonances to describe N simultaneous tone amplitudes -- a 4:1
projection at 24 rows, 32:1 at 128. The mapping is genuinely many-to-one, so
the information is destroyed, not attenuated, and no inverse and no learned
model can recover it; a model could only pick a likely preimage from a prior.
The scheme is also maximally exposed to AGC, because absolute level *is* the
payload, and `recover_activation` divides by a `normalization_gain` that no
real call carries.

Measured, with perfect sync and pilot normalisation: **~17.7% bit error**,
binary, unchanged across 2-16 gray levels, 8-32 rows, and 2/4/6/9 pilots. The
16-FSK modem in the parent directory gets 0.25% symbol error over the same
channel.

## Running it anyway

Still works, from inside this directory:

```bash
cd backend/spectral/tel/rejected
python3 test_pipeline.py
```

`channel_sim.py` is one level up and is found via the `sys.path` line at the
top of `test_pipeline.py`. It needs ffmpeg built with libgsm.
