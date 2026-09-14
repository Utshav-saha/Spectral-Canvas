# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Spectral Canvas is a Signals and Systems coursework project that turns an image into audio and back again. Image rows map to frequency lanes, columns map to time frames, and brightness sets amplitude. The WAV it produces is a real, playable file, and the receiver rebuilds the picture from that audio alone. A transmission can optionally be locked with two 11-digit phone numbers and a 4–8 digit PIN. A wrong PIN doesn't raise an error. It decodes to static, and that is intended behaviour that the UI explains.

Longer docs: `README.md` (running it), `BACKEND_GUIDE.md` (API contract and why the library is shaped the way it is), `FRONTEND_GUIDE.md`, `PRODUCT.md` (audiences and the planned features), and `DESIGN.md` (the binding visual system). `docs/` holds copies of the guides plus the project-plan PDFs.

## Commands

The backend and frontend run as two processes. Start the backend first.

```bash
# Backend (FastAPI on :8000). Commands run from backend/ so `app` and `spectral` import as top-level packages.
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000     # health: /api/health, docs: /docs

# Round-trip test (a script, not pytest). Must be run as a module from backend/.
python -m tests.test_roundtrip

# Text MFSK codec self-test
python spectral/text/text_codec.py

# Telephony experiments (bare sibling imports, so run from inside tel/; channel_sim needs ffmpeg with libgsm)
cd backend/spectral/tel
python3 demo.py                     # image -> 16-FSK -> simulated GSM call -> image; writes tx.wav
python3 test_pipeline.py            # the multitone-over-GSM attempt (reproduces the ~17.7% error result)
./run_local_call.sh tx.wav rx.wav   # real SIP loopback call; needs `brew install pjproject` (pjsua)

# Frontend (Vite on :5173; proxies /api -> 127.0.0.1:8000)
cd frontend
npm install
npm run dev
npm run build && npm run preview
```

There is no linter and no JS test setup. The root `.venv` lacks FastAPI, so use a venv inside `backend/` as the README describes. `test_roundtrip` encodes five paths (grayscale open/locked, colour open/locked, rendered text) through the real int16 WAV container. Every path should report a mean pixel error of `0.000`.

## Repository layout gotchas

- **The live code is under `backend/` and `frontend/`.** The top-level `spectral/`, `tests/`, `requirements.txt`, `encoder_prev/`, `metadata.json`, `output_pepsi.wav` and `recovered.png` are leftovers from before the move into `backend/`. They have diverged from the `backend/` copies, so don't edit them expecting any effect. `spectral/unused/` and `encoder_prev/` hold the abandoned binary-encoding approach.
- `frontend/dist/` is committed build output.
- The guides partly describe a target state that `backend/spectral/` doesn't fully match yet. Examples: `reference_gain` calibration, `to_image_array`, and `synchronize(audio, metadata)`. Check the actual function signatures before relying on the guide. The same applies to `FRONTEND_GUIDE.md`: its "SC-01 SignalBench" and indigo/coral palette became `DMG01.jsx` and the oxide/LCD palette in `DESIGN.md`.
- Several `spectral/` modules (the decoder and `audio_encoder.py`) add entries to `sys.path` and use bare sibling imports such as `from synchronizer import ...`. `app/`, by contrast, imports them as `spectral.decoder...`. When touching imports, make sure both the app and the test still import cleanly.

## Architecture

### Backend: a pure library plus a thin HTTP layer

- **`backend/spectral/` must never import FastAPI.** It is the DSP ground truth shared with the report and experiments. Anything HTTP-aware belongs in `backend/app/services/pipeline.py`.
- `encode(source, ...)` returns `(audio, metadata, activation)` and touches no disk. The source can be a path, bytes, or a `PIL.Image`. This keeps concurrent requests from colliding on shared output files.
- **Encode path:** `input/image_preprocessor` (image, text, or doodle → quantized activation matrix, where 0 = white/silent and 1 = black) → optional `common/security` (key from caller+receiver+PIN → row and column permutations, plus a noise mask) → `encoder/audio_encoder` (sum of per-row sines per frame, Hann-windowed, frequencies snapped to FFT bins, per-row start phases, headroom reserved for the mask). RGB is encoded as three channel blocks.
- **Container:** `common/wav_container.py` writes **16-bit PCM**, because browsers won't play float64 WAVs. It embeds the decode metadata (row frequencies, frame samples, columns, channels, gray levels, normalization gain, security flag) as a custom `SpCv` RIFF chunk. This lets the Receive page decode from a single uploaded file.
- **Decode path:** `decoder/synchronizer` → `stft_decoder` (FFT magnitude at each row bin, per frame) → `decrypter` (remove mask, unscramble) → `image_reconstructor.reconstruct()`.
- `channel/effects.py` (noise, echo, Butterworth filters, clipping, resampling/aliasing, and `apply_chain(audio, sr, effects)`) and `analysis/waveform.py::spectrogram()` are written but not yet exposed through an endpoint.
- `spectral/text/text_codec.py` is a separate, in-progress experiment. It encodes UTF-8 text as 4-bit MFSK with 16 carriers between 2 and 5 kHz. It is not wired into the app. The "text" source type in the API renders text to an image instead.

### Telephony path (`backend/spectral/tel/`, standalone, not wired into the app)

This directory sends images over an 8 kHz voice call (GSM 06.10 over SIP), and `tel/README_TELEPHONY.md` has the full reasoning. Voice codecs model each 20 ms frame with an 8-pole LPC envelope. That keeps *which* frequency is present but loses *how loud* it is, so the main app's parallel multitone scheme, where amplitude carries each pixel, fails here.
- `fsk_codec.py` is the working modem. It sends one tone per 40 ms symbol, chosen from 16 tones between 700 and 3200 Hz, which gives 100 bit/s raw. A preamble handles sync. The payload is Hamming(7,4) coded and interleaved, with a 16-bit length header. The decoder only takes an `argmax` over tone bins and never compares magnitudes. `image_fsk.py` converts between activation matrices and bits, and its `budget()` function gives the call length. `channel_sim.py` simulates the call offline: GSM, packet loss, AGC and noise.
- The `tel_*.py` files and `test_pipeline.py` hold the failed narrowband multitone attempt with pilot tones. They are kept on purpose for the report.
- To reuse main-library pieces on this path, keep `scramble`/`unscramble`, which permute before the modem. **Drop the additive `generate_mask`/`remove_mask`**, because an RTP path never gives sample-exact alignment. `recover_activation` and the energy- or correlation-based sync don't carry over either.
- The bandwidth sets the limits: 24×24 at 4 levels takes about 20 s, while the app's default of 64×64 RGB at 16 levels would take about 14 minutes. Outputs must be 16-bit mono PCM at 8 kHz.

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

### Planned work (from PRODUCT.md)

Each item gets its own route and nav entry: a channel panel (needs `POST /api/channel` wrapping `apply_chain`), a spectrogram view, a side-by-side sent/recovered/difference compare, and experiment plots (`spectral/analysis/experiments.py`, not yet written).
