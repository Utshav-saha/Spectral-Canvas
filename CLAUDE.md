# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Spectral Canvas is a Signals and Systems coursework project that turns an image into audio and back again. Image rows map to frequency lanes, columns map to time frames, and brightness sets amplitude. The WAV it produces is a real, playable file, and the receiver rebuilds the picture from that audio alone. A transmission can optionally be locked with two 11-digit phone numbers and a 4–8 digit PIN. A wrong PIN doesn't raise an error. It decodes to static, and that is intended behaviour that the UI explains.

Longer docs: `README.md` (running it) and `DESIGN.md` (the binding visual system) are at the root. Everything else lives in `docs/`: `BACKEND_GUIDE.md` (API contract and why the library is shaped the way it is), `FRONTEND_GUIDE.md`, `PRODUCT.md` (audiences and the planned features), `RESTORATION_PLAN.md` (channel inversion and the learned restoration step), plus the project-plan PDFs. `backend/voip/README.md` is the guide for the real-phone-call path and is the most current of them.

## Commands

The backend and frontend run as two processes. Start the backend first.

```bash
# Backend (FastAPI on :8000). Commands run from backend/ so `app` and `spectral` import as top-level packages.
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000     # health: /api/health, docs: /docs

# Round-trip test (a script, not pytest, and conftest.py excludes it from collection).
python -m tests.test_roundtrip

# The pytest suite, which is the voip/ package's. Skips cleanly without ffmpeg/libgsm/pjsua/SDK.
python -m pytest tests/ -q

# Text MFSK codec self-test
python spectral/text/text_codec.py

# Telephony experiments (bare sibling imports, so run from inside tel/; channel_sim needs ffmpeg with libgsm)
cd backend/spectral/tel
python3 demo.py                     # image -> 16-FSK -> simulated GSM call -> image; writes tx.wav
./run_local_call.sh tx.wav rx.wav   # real SIP loopback call; needs `brew install pjproject` (pjsua)
python3 test_pipeline.py            # Generation A over the simulated call, with the error

# Real phone call (backend/voip/, run from backend/). voip/README.md is the full guide.
python -m voip.cli check-env                                   # run this first, always
python -m voip.cli prepare --image cat.jpg --gen B --grid 16 --levels 4
python -m voip.cli simulate --run latest --pjsua --decode       # SIP loopback rehearsal
python -m voip.cli call --run latest --dial sip:you@sip.linphone.org
python -m voip.cli decode ~/Downloads/call.mka --run latest --json

# Frontend (Vite on :5173; proxies /api -> 127.0.0.1:8000)
cd frontend
npm install
npm run dev
npm run build && npm run preview
```

There is no linter and no JS test setup. The root `.venv` lacks FastAPI, so use a venv inside `backend/` as the README describes. `test_roundtrip` encodes five paths (grayscale open/locked, colour open/locked, rendered text) through the real int16 WAV container. Every path should report a mean pixel error of `0.000`.

## Repository layout gotchas

- **The live code is under `backend/` and `frontend/`.** The diverged top-level `spectral/` copy was deleted in the `reco_deco` merge. `tests/`, `requirements.txt`, `encoder_prev/`, `metadata.json`, `output_pepsi.wav` and `recovered.png` are still leftovers from before the move into `backend/`; don't edit them expecting any effect. `encoder_prev/` holds the abandoned binary-encoding approach.
- `frontend/dist/` is committed build output.
- The guides partly describe a target state that `backend/spectral/` doesn't fully match yet. Examples: `reference_gain` calibration and `synchronize(audio, metadata)`. Check the actual function signatures before relying on the guide. (`to_image_array` was on that list until it was actually added to `image_reconstructor.py`; it is now real.) The same applies to `FRONTEND_GUIDE.md`: its "SC-01 SignalBench" and indigo/coral palette became `DMG01.jsx` and the oxide/LCD palette in `DESIGN.md`.
- Several `spectral/` modules (the decoder and `audio_encoder.py`) add entries to `sys.path` and use bare sibling imports such as `from synchronizer import ...`. `app/`, by contrast, imports them as `spectral.decoder...`. When touching imports, make sure both the app and the test still import cleanly.

## Architecture

### Backend: a pure library plus a thin HTTP layer

- **`backend/spectral/` must never import FastAPI.** It is the DSP ground truth shared with the report and experiments. Anything HTTP-aware belongs in `backend/app/services/pipeline.py`.
- `encode(source, ...)` returns `(audio, metadata, activation)` and touches no disk. The source can be a path, bytes, or a `PIL.Image`. This keeps concurrent requests from colliding on shared output files.
- **Encode path:** `input/image_preprocessor` (image, text, or doodle → quantized activation matrix, where 0 = white/silent and 1 = black) → optional `common/security` (key from caller+receiver+PIN → row and column permutations, plus a noise mask) → `encoder/audio_encoder` (sum of per-row sines per frame, Hann-windowed, frequencies snapped to FFT bins, per-row start phases, headroom reserved for the mask). RGB is encoded as three channel blocks.
- **Container:** `common/wav_container.py` writes **16-bit PCM**, because browsers won't play float64 WAVs. It embeds the decode metadata (row frequencies, frame samples, columns, channels, gray levels, normalization gain, security flag) as a custom `SpCv` RIFF chunk. This lets the Receive page decode from a single uploaded file.
- **Decode path:** `decoder/synchronizer` → `stft_decoder` (FFT magnitude at each row bin, per frame) → `decrypter` (remove mask, unscramble) → `image_reconstructor.reconstruct()`.
- `channel/effects.py` (noise, echo, Butterworth filters, clipping, resampling/aliasing, and `apply_chain(audio, sr, effects)`) and `analysis/waveform.py::spectrogram()` are written but not yet exposed through an endpoint.
- **Text does not go through the image pipeline.** `source_type: "text"` uses `spectral/text/text_codec.py`: each UTF-8 byte is split into two 4-bit symbols, and each symbol is one Hann-windowed tone out of 16, spaced between 2 and 5 kHz, lasting 0.05 s. Decoding takes the FFT peak of each symbol and snaps it to the nearest tone. `pipeline.run_encode_text` and `run_decode_text` wrap the codec. Text WAV metadata carries `"kind": "text"`, and the routes branch on that field: decode returns `text` instead of `image_url`. Text can't be locked, and messages are capped at about 1426 UTF-8 bytes so the WAV stays under the 12 MB upload limit.

### Telephony path (`backend/spectral/tel/`)

This directory sends images over an 8 kHz voice call (GSM 06.10 over SIP), and `tel/README_TELEPHONY.md` has the full reasoning. `call_track.py` is the app-facing wrapper (**Track 2**, see below); the rest of the directory is still standalone scripts you run by hand. Voice codecs model each 20 ms frame with an 8-pole LPC envelope. That keeps *which* frequency is present but loses *how loud* it is, so the main app's parallel multitone scheme, where amplitude carries each pixel, fails here.
- `fsk_codec.py` is the working modem. It sends one tone per 40 ms symbol, chosen from 16 tones between 700 and 3200 Hz, which gives 100 bit/s raw. A preamble handles sync. The payload is Hamming(7,4) coded and interleaved, with a 16-bit length header. The decoder only takes an `argmax` over tone bins and never compares magnitudes. `image_fsk.py` converts between activation matrices and bits, and its `budget()` function gives the call length. `channel_sim.py` simulates the call offline: GSM, packet loss, AGC and noise.
- `tel_config.py`, `tel_encoder.py` and `tel_decoder.py` are **Generation A** over a call, reached through `call_track.py`. They were cut once, as "Track 3", and brought back deliberately: the loss they produce is what the restoration model is being trained to undo. `tel/patterns.py` holds the test image that used to live in `test_pipeline.py`.
- To reuse main-library pieces on this path, keep `scramble`/`unscramble`, which permute before the modem. **Drop the additive `generate_mask`/`remove_mask`**, because an RTP path never gives sample-exact alignment. `recover_activation` and the energy- or correlation-based sync don't carry over either.
- The bandwidth sets the limits: 24×24 at 4 levels takes about 20 s, while the app's default of 64×64 RGB at 16 levels would take about 14 minutes. Outputs must be 16-bit mono PCM at 8 kHz.

### The two tracks

A transmission goes out on one of exactly two tracks, chosen by the `track`
field on `/api/encode` and written into the WAV's `SpCv` metadata so the
receiver never has to be told which one it is holding. `app/config.py::TRACKS`
is the whole list, `/api/health` serves it, and the Send page builds its Track
selector from that response. `pipeline.track_of(metadata)` reads it back; a
file with no `track` field predates the split and is Track 1.

- **Track 1, `"wav"`** - `spectral/encoder/audio_encoder.py`, the original
  parallel multitone scheme. 44.1 kHz, 1-8 kHz, up to 128x128, 16 gray levels.
  The pixel is in a tone's *amplitude*. Text (`source_type: "text"`) is Track 1
  only; the backend refuses it on a call.
- **Track 2, `"call"`** - `spectral/tel/call_track.py` wrapping `image_fsk` and
  `fsk_codec`. 8 kHz, 700-3200 Hz, one tone per 40 ms symbol out of 16, 32x32
  at 4 levels, ~57 bit/s after Hamming(7,4). The pixel is in *which tone* is
  present, which is the only thing a GSM codec preserves. Locking is the
  permutation half only: `scramble`/`unscramble` run upstream of the modem, and
  the additive noise mask is dropped because cancelling it needs sample-exact
  alignment an RTP path cannot give. `CALL_MAX_SECONDS` (5 min) caps how much
  call time one request may ask for, which also keeps the 8 kHz WAV under the
  upload limit.
- **Over a call**, both generations are offered on the Call page. This was
  once cut as "Track 3" and brought back deliberately: Generation A's loss is
  the restoration model's training target, so the damage is the deliverable.

A *track* is a delivery path; a *generation* is the encoding it carries.

| | scheme | pixel lives in | over GSM | where |
|---|---|---|---|---|
| **Gen A** | parallel multitone, 2 pilots | a tone's amplitude | ~75% exact | Track 1, and the Call page |
| **Gen B** | 16-FSK, one tone per symbol | which tone plays | 100% exact | Track 2, and the Call page |
| ~~Gen C~~ | WebP + Reed-Solomon | bytes | exact or nothing | **cut** |

Generation A over a call is lossy *on purpose*: the damage is graded and
reproducible, which is what makes it a training target. Generation B is exact
but caps out near 32x32, which is what the upscaler is for. Generation C was
cut because a byte-exact file transfer has no loss to learn from -- the code
is in the git history, not the tree.

### The call path (`backend/voip/`, `/api/tel`, the Call page)### The call path (`backend/voip/`, `/api/tel`, the Call page)

Merged from `reco_deco`. This is the only part of the project that places a
**real** phone call; everything else either writes a file or simulates the
channel offline. `backend/voip/README.md` is the guide, and it is detailed -
read it before touching this.

- `backend/voip/` is a standalone package with its own CLI (`python -m
  voip.cli`), its own pytest suite under `backend/tests/test_voip_*.py`, and
  its own `runs/` scratch directory. It reimplements **none** of the modem:
  `spectral/tel/fsk_codec.py` stays the ground truth and is imported unchanged
  through `voip/_tel.py`, which is the same `sys.path` shim as
  `spectral/tel/__init__.py`. Both are idempotent and load the same module
  objects.
- What it adds around the modem is the four things a real recording needs that
  an in-memory array does not: whole-file preamble search that also returns its
  score (`sync.py`), decision margins kept rather than discarded (`dsp.py`),
  short-recording detection instead of confident garbage (`framing.py`), and
  ffmpeg transcoding, because Linphone records Matroska (`audio_io.py`).
- `app/api/tel_routes.py` + `app/services/tel_pipeline.py` serve the Call page
  under **`/api/tel`**, deliberately namespaced so the Send and Receive
  contract is untouched: `info`, `stage`, `plan/{id}`, `send`, `call`,
  `inspect`, `receive`, `audio/{id}`, `waveform/{id}`, `sent/{id}`,
  `recovered/{id}`. The `call` endpoint is the **offline** simulator; a real
  call goes through the CLI.
- `app/main.py` mounts that router inside a `try/except ImportError`, so a
  machine without `reedsolo` still starts the rest of the app and `/api/tel/*`
  answers 503 with what to install. Keep that guard.
- `MAX_AUDIO_UPLOAD_BYTES` (32 MB) is separate from `MAX_UPLOAD_BYTES` (12 MB),
  because a recorded call is much bigger than a picture.
- Frontend: `pages/Call.jsx` + `Call.css`, `api/telClient.js`, and a `/call`
  route. `telClient.js` is kept apart from `client.js` on purpose.
- **Placing a real call**: `voip/dial.py` + `POST /api/tel/dial`. It drives
  **pjsua**, not liblinphone: the SDK's Python bindings are on no package index
  and have to be compiled from source, and they are not needed, because
  `sip.linphone.org` is an ordinary SIP registrar and the Linphone app answers
  any SIP client. SIP-to-SIP, so no PSTN and no paid trunk. Credentials come
  from `VOIP_SIP_IDENTITY` / `VOIP_SIP_PASSWORD` in the server's environment
  and **never** from a request body. One call at a time, behind a lock.
  `voip/call/session.py` is the abandoned SDK route and has never run.
- What the dial endpoint cannot do is bring the audio back: pjsua records its
  own inbound leg, which is the phone's muted microphone, not the tones the
  phone received. The recording is made on the phone with Linphone's in-call
  Record button and uploaded to `POST /api/tel/upload`, which matches it to
  the send session for the geometry.

### The experiments bench (`/experiments`, `/api/channel`)

`spectral/channel/effects.py` sat unused since the start; `app/services/
channel_lab.py` is what exposes it. `GET /api/channel/effects` serves the
catalogue the page builds itself from, so the UI and the validator cannot
drift apart, and `POST /api/channel` runs a chain against an `/api/encode`
session and measures what it cost.

The figure that matters is `row_error`, the mean error per image row. One
image row is one frequency, so an LTI channel's fingerprint is *which* rows it
damages: a low-pass ramps at the top (row 0 carries `f_max`), a high-pass at
the bottom, a band-stop punches a contiguous hole, and clipping scatters,
because intermodulation puts energy on rows that were never sent.
`tests/test_channel_bench.py` pins that mapping -- if it ever inverts, every
figure in the report is wrong.

### HTTP layer

- `app/api/routes.py` defines every endpoint under `/api`: `health`, `encode` (multipart with a `payload` JSON string plus an optional `file`), `audio/{id}`, `preview/{id}`, `waveform/{id}?buckets=`, `inspect` (reads WAV metadata without decoding, so the UI knows whether the file is locked), `decode` (JSON with `session_id` and credentials), and `recovered/{id}`. **The frontend depends on these exact field names.** See BACKEND_GUIDE §4 for the shapes.
- `app/storage/session_store.py` is an in-memory dict behind a lock, with a 1 h TTL and a 200-session cap. It is deliberately not a database, so run a single worker.
- Validation lives in `pipeline.validate_credentials` and related functions. They raise `ValueError`, which routes turn into a 400. The frontend shows `detail` **verbatim**, so write error messages for end users.
- Limits and defaults live in `app/config.py`: 12 MB upload, 2000 characters, 160 px working size, 64×64 target, 44.1 kHz, 1–8 kHz band, 0.05 s frames, 16 gray levels. `f_max` must stay below Nyquist.
- Decode `metrics` (MAE/MSE/PSNR) are only present when the same session also holds the original activation.

### Frontend

- React 18, Vite, react-router, plain JS and plain CSS. **No TypeScript, Tailwind, or component libraries.** The project treats this as a binding rule, because the visual identity is load-bearing.
- Routes: `/` Landing (with the `DMG01` console, a client-only miniature of encode/lock/open), `/simulate` (Send), and `/receive`. Every fetch goes through `src/api/client.js`, which uses relative `/api` URLs. If the backend port changes, update `vite.config.js` too.
- `Receive.jsx` must import `Simulate.css` **before** `Receive.css`, because shared classes (`.module`, `.drop`, `.filecard`, `.lockfields`, `.switch`) are defined in `Simulate.css`.
- `WaveformScope` draws the backend's min/max/RMS envelope, not raw samples. `DoodleCanvas` smooths strokes on purpose, since jitter becomes audible noise.
- Visual rules in `DESIGN.md` are binding. The main ones:
  - Oxide red appears only on pressable things.
  - The LCD canvas uses exactly four tones and a bitmap font at 160×144.
  - Numbers, filenames and measurements use Spline Sans Mono with tabular figures. Prose uses Archivo, condensed via its width axis.
  - State is shown with `.led` dots in the fixed signal/open/locked colours.
  - Depth comes from shadows lit from the upper left, never from borders.
  - No scroll-triggered fade-ins. Respect `prefers-reduced-motion`.
  - Design tokens live in `src/styles/tokens.css`.

### Planned work (from docs/PRODUCT.md)

The channel panel is built (`/experiments`). Still open: a spectrogram view, a
side-by-side sent/recovered/difference compare, and experiment plots
(`spectral/analysis/experiments.py`, not yet written). The restoration model
itself is `docs/RESTORATION_PLAN.md` phases 2-5; `backend/tools/make_dataset.py`
is the dataset generator, and the only caller of `apply_chain` outside the
bench.
