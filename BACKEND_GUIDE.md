# Spectral Canvas — Backend Guide

For the person building the FastAPI side. Everything described here is written
and tested; this document explains *why* each piece is shaped the way it is, so
you can extend it without breaking the frontend contract.

---

## 1. The one rule

`spectral/` is a **pure Python library that imports nothing from FastAPI.**
`app/` is a thin HTTP wrapper around it.

This is what lets the two of us work in parallel. You build endpoints against
fixed function signatures; the frontend builds against your JSON. Neither of us
touches `spectral/` carelessly, because it is the shared ground truth that the
report and the experiments also depend on.

If you ever find yourself writing `from fastapi import ...` inside `spectral/`,
stop — that logic belongs in `app/services/`.

---

## 2. Folder structure

```
backend/
├── app/                          FastAPI layer only
│   ├── main.py                   creates the app, mounts CORS + router
│   ├── config.py                 defaults, limits, CORS origins
│   ├── api/routes.py             every endpoint
│   ├── schemas/models.py         Pydantic request/response models
│   ├── services/pipeline.py      glue: HTTP shapes <-> library calls
│   └── storage/session_store.py  in-memory session dict
│
├── spectral/                     pure library
│   ├── common/
│   │   ├── security.py           key derivation, scramble, noise mask
│   │   └── wav_container.py      WAV with embedded metadata  <-- NEW
│   ├── input/
│   │   └── image_preprocessor.py image + text + doodle -> activation
│   ├── encoder/
│   │   └── audio_encoder.py      activation -> audio
│   ├── channel/
│   │   └── effects.py            noise, echo, filters, clipping, aliasing
│   ├── decoder/
│   │   ├── synchronizer.py
│   │   ├── stft_decoder.py
│   │   ├── decrypter.py
│   │   └── image_reconstructor.py
│   └── analysis/
│       └── waveform.py           envelope, per-bucket stats, spectrogram
│
├── tests/
└── requirements.txt
```

Note `spectral/` moved *under* `backend/`. Migrate with `git mv` so history
follows the files:

```bash
git checkout -b backend-api
git mv spectral backend/spectral
git mv requirements.txt backend/requirements.txt
git mv tests backend/tests
```

---

## 3. What changed inside `spectral/` and why

These are not cosmetic. Each one was blocking something.

### 3.1 `encode()` returns data instead of writing files

**Before:** `encode(path, ...)` wrote `output.wav` and `metadata.json` to the
current working directory and returned `None`.

**Why that breaks:** two users hitting `/api/encode` at the same moment both
write `metadata.json`. User A's decode then reads User B's metadata and
reconstructs garbage. There is no lock that fixes this cleanly.

**Now:**

```python
audio, metadata, activation = encode(image_source, ...)
```

`image_source` accepts a path, raw bytes, or a `PIL.Image`. Nothing touches
disk. `app/` decides what to do with the bytes.

### 3.2 The WAV carries its own metadata

The Receive page uploads **one file**. But decoding needs `row_frequencies`,
`frame_samples`, `columns`, `channels`, `gray_levels` and
`normalization_gain`. Making the user carry a second `.json` around would be
miserable.

`spectral/common/wav_container.py` appends a custom `SpCv` RIFF chunk holding
that JSON. Players that don't recognise the chunk skip it, so the file still
plays in a browser, in VLC, anywhere.

```python
wav_bytes = write_wav_bytes(sample_rate, audio, metadata)
sample_rate, audio, metadata = read_wav_bytes(raw_bytes)   # metadata is None if absent
```

Verified: stdlib `wave` and `scipy.io.wavfile` both still read these files.

### 3.3 int16, not float64

`scipy.io.wavfile.write()` with a float64 array produces a 64-bit float WAV.
Python reads it back perfectly. **No browser will play it.** Since the whole
point is pressing play on a transmission, the container writes 16-bit PCM.

The quantisation error is ~3e-5 in amplitude, which is far below one
`gray_levels` step, so recovery is still exact.

### 3.4 Per-row start phases

Every tone used to start at phase zero, so all 64 rows peaked at the same
instant. A dark column produced a huge spike, global normalisation scaled the
*whole file* down to fit it, and every other column lost amplitude resolution.

`build_phases(rows)` gives each row a fixed pseudorandom phase from a seeded
RNG. The decoder takes `np.abs()` of the FFT, so phase is invisible to it —
this costs nothing and is stored in metadata anyway for the report.

### 3.5 Headroom reserved before the mask, not after

**Before:** normalise to peak 0.5, then add `alpha * mask` on top. With
`alpha=0.5` that lands at exactly 1.0; with `alpha=0.6` it clipped.

**Now:** `target_peak = 0.9 - alpha` when security is on, so the mask fits
inside the headroom. Measured peaks now sit at 0.77–0.90.

### 3.6 Reference-gain calibration replaced min-max

`normalize()` used to stretch min-to-max, which assumes the recovered image
contains both a pure-black and a pure-white pixel. Any image that didn't touch
both extremes got its contrast wrongly stretched — and then the error metric
reported "bad recovery" for a file that had transmitted **perfectly**.

`reference_gain(metadata)` synthesises one amplitude-1.0 tone using the exact
frequency, Hann window and frame length the encoder used, and measures the
magnitude it produces. Since encoding and the FFT are both linear:

```
magnitude = normalization_gain * true_amplitude * reference_gain
```

A Hann-windowed sinusoid's magnitude at its own exact bin depends only on N and
the window, not on which bin — so one tone calibrates every row. This is what
takes recovery to exactly 0.000 error.

Keep `normalize_minmax` as the fallback for metadata missing
`normalization_gain`. It is also a good report comparison: naive normalisation
versus metadata-informed normalisation.

### 3.7 Multi-channel fixes (carried over)

- `synchronize(audio, metadata)` multiplies by `metadata["channels"]`. The old
  two-arg version silently aligned to the red channel and discarded green/blue.
- `decode()` splits RGB into three blocks and returns `(rows, cols, 3)`.
- `unscramble` uses `shape[:2]`, since a 3D array can't unpack into two names.
- `security_enabled` defaults to `False`, not `True`.

---

## 4. API contract

The frontend depends on exactly these shapes. Changing a field name breaks it.

### `GET /api/health`
```json
{ "status": "ok", "defaults": { ... } }
```

### `POST /api/encode`  (multipart)

Two parts: `payload` (a JSON **string**) and optional `file`.

```jsonc
// payload
{
  "source_type": "image" | "text" | "doodle",
  "text": "...",              // when source_type = text
  "data_url": "data:image/png;base64,...",  // when source_type = doodle
  "target_width": 64, "target_height": 64,
  "mode": "L" | "RGB",
  "gray_levels": 16,
  "frame_duration": 0.05,
  "f_min": 1000, "f_max": 8000,
  "security_enabled": false,
  "caller": "01712345678", "receiver": "01898765432", "pin": "4321",
  "alpha": 0.15
}
```

Response:
```json
{
  "session_id": "a1b2c3...", "metadata": { ... }, "stats": { ... },
  "encrypted": true, "rows": 64, "columns": 64, "duration": 9.6,
  "audio_url": "/api/audio/a1b2c3...", "preview_url": "/api/preview/a1b2c3..."
}
```

### `GET /api/audio/{id}` → `audio/wav` (with `Content-Disposition: attachment`)
### `GET /api/preview/{id}` → `image/png`, the processed source at transmission size

### `GET /api/waveform/{id}?buckets=900`

```json
{
  "envelope": { "min": [...], "max": [...], "rms": [...] },
  "buckets":  [ { "t": 0.05, "peak": 0.81, "rms": 0.31, "freq": 4410.0 }, ... ],
  "stats":    { "duration": 9.6, "sample_rate": 44100, "peak": 0.9,
                "rms": 0.31, "crest_factor": 2.9, "dbfs": -0.9 }
}
```

A 6-second clip is ~265k samples. The canvas only has ~900 columns, so sending
raw samples would be megabytes for no visual gain. The min/max envelope
preserves the waveform's shape exactly at ~1/150th the payload.

### `POST /api/inspect`  (multipart, `file`)

Reads an uploaded WAV **without decoding it** — this is what lets the Receive
page say "this is locked, enter a PIN" before asking for anything.

```json
{
  "session_id": "...", "encrypted": true, "has_metadata": true,
  "metadata": { ... }, "stats": { ... },
  "message": "This transmission is locked. Enter the numbers and PIN to open it."
}
```

`has_metadata: false` means someone uploaded an ordinary WAV. Say so plainly
and still show the waveform — don't error out.

### `POST /api/decode`  (JSON)
```json
{ "session_id": "...", "caller": "...", "receiver": "...", "pin": "..." }
```
→
```json
{
  "session_id": "...", "image_url": "/api/recovered/...",
  "rows": 64, "columns": 64, "mode": "RGB", "decrypted": true,
  "metrics": { "mae": 0.0, "mse": 0.0, "psnr": null }
}
```

`metrics` is only non-null when the session also holds the original activation
(i.e. the same browser encoded it). A genuinely received file has nothing to
compare against, which is correct.

**A wrong PIN does not error.** It decodes successfully to noise, because that
is what actually happens — the scramble is reversed with the wrong permutation.
The frontend explains this rather than pretending it was a failure.

### `GET /api/recovered/{id}` → `image/png`

---

## 5. Sessions

`app/storage/session_store.py` is a dict behind a lock, with a 1-hour TTL and a
200-session cap. Deliberately **not** a database: a session is one encode or one
decode, and if the server restarts everything should be gone. That is correct
behaviour for a simulator, and it keeps the deployment to one process.

If you later run more than one worker, sessions stop being shared. Either pin
to one worker (`--workers 1`) or move the dict to Redis. For a course demo, one
worker is right.

---

## 6. Validation

All of it lives in `pipeline.validate_credentials`. Numbers must be exactly
`PHONE_DIGITS` (11) digits; PIN 4–8 digits. `f_max` must stay under Nyquist and
above `f_min`. Uploads cap at 12 MB, text at 2000 characters.

Raise `ValueError` in the service layer; `routes.py` converts it to a 400 with
the message intact. The frontend shows that message verbatim, so **write error
text for the user, not for the log**: "Caller number must be exactly 11 digits"
rather than "validation failed on field caller".

---

## 7. What to build next

`spectral/channel/effects.py` is written but **not yet exposed as an endpoint**.
This is the highest-value remaining work, because §8 of the project plan is
most of your syllabus credit — convolution, LTI systems, impulse response,
frequency response and aliasing all live there, and none of them are
demonstrated by the encoder alone.

Suggested endpoint:

```
POST /api/channel
{ "session_id": "...", "effects": [ {"type":"lowpass","cutoff":4000},
                                    {"type":"echo","delay":0.08,"decay":0.4} ] }
-> new session_id with the distorted audio, ready to decode
```

`apply_chain(audio, sample_rate, effects)` already takes exactly that list. The
visible payoff: low-pass fades the *top* of the picture, band-stop removes a
horizontal band, echo smears columns rightward. That mapping is the single best
thing you can put in the demo.

After that: `spectral/analysis/experiments.py` for the parameter sweeps
(frame duration vs accuracy, SNR vs accuracy), which gives the report its graphs.
