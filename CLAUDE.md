# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Spectral Canvas is a Signals and Systems coursework project that turns an image into audio and back again. Image rows map to frequency lanes, columns map to time frames, and brightness sets amplitude. The WAV it produces is a real, playable file, and the receiver rebuilds the picture from that audio alone. A transmission can optionally be locked with two 11-digit phone numbers and a 4–8 digit PIN. A wrong PIN doesn't raise an error. It decodes to static, and that is intended behaviour that the UI explains.

Longer docs: `README.md` (running it) and `DESIGN.md` (the binding visual system) are at the root. Everything else lives in `docs/`: `BACKEND_GUIDE.md` (API contract and why the library is shaped the way it is), `FRONTEND_GUIDE.md`, `PRODUCT.md` (audiences and the planned features), `RESTORATION_PLAN.md` (channel inversion and the learned restoration step), plus the project-plan PDFs. `backend/spectral/tel/README_TELEPHONY.md` is the guide for the call path.

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

# The pytest suite. Skips cleanly without libgsm.
python -m pytest tests/ -q

# Text MFSK codec self-test
python spectral/text/text_codec.py

# Telephony experiments (bare sibling imports, so run from inside tel/; channel_sim needs ffmpeg with libgsm)
cd backend/spectral/tel
python3 demo.py                     # image -> 16-FSK -> simulated GSM call -> image; writes tx.wav
python3 test_pipeline.py            # Generation A over the simulated call, with the error
./run_local_call.sh tx.wav rx.wav   # real SIP loopback between two pjsua instances; no account needed

# The call package's own CLI (from backend/; `check-env` first, it reports what is missing)
cd backend
python -m voip.cli check-env
python -m voip.cli prepare --image cat.jpg --gen B --grid 16 --levels 4
python -m voip.cli simulate --run latest --lead 30 --decode

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
- `fsk_codec.py` is the working modem. It sends one tone per 40 ms symbol, chosen from 16 tones between 700 and 3200 Hz, which gives 100 bit/s raw. A preamble handles sync. The payload is Hamming(7,4) coded and interleaved, with a 16-bit length header. The decoder only takes an `argmax` over tone bins and never compares magnitudes. **A `RESYNC` marker leads every segment of `RESYNC_INTERVAL` (64) payload symbols, the first included**, and `demodulate` re-anchors on each one absolutely rather than counting symbols from the preamble all the way through. The first segment needs one too because `PREAMBLE` cannot place it: alternating between two tones makes its score broad, and on a real 36 s call it locked 110 samples early, where the first segment read 57.8% correctly against 83.2% at the true alignment - and its *margin was higher* there, a window sitting mostly inside the previous symbol looking decisive and being wrong. With a leading marker a 250-sample preamble error is absorbed; without one, 160 was already fatal. The preamble's remaining job is only to be findable in a recording that starts whenever Record was pressed. It is there because a real 860 s call slipped 2.2 whole symbols in discrete jitter-buffer jumps and came back at 8.4% of pixels, chance: the tones were fine (92-98% correct once realigned) but a whole-symbol slip is *invisible* to every alignment score taken from the waveform, since a grid shifted by a whole symbol is still aligned to one. Unlike `PREAMBLE`, which alternates with a period of two symbols, `RESYNC`'s eight tones are all distinct, so nothing but the true position lines up. The interval is 64 and not larger because a handset does not just slip occasionally: measured across a real 160 s call the phone's timing wandered over 805 samples - 2.5 whole symbols - at a median 15.6 samples/s with jumps to 352, in both directions, against a tolerance of about 80. Replaying that measured wander over the real GSM channel, interval 512/256/128/**64**/32 scores 55.2/79.3/86.3/**98.6**/99.5% of pixels exact for 1.8/3.4/6.5/**12.7**/25.2% of added airtime, so 64 is the knee. The markers cost 12.5% of the wire, and take the 860 s call from 13.4% to 99.9%. `n_symbols` still counts payload symbols only; `wire_symbols` counts the markers too. A stream with no `resync_interval` in its metadata predates this and is read as one uninterrupted run. **`voip/encode.py` deliberately passes `resync=0`**: that package reads the wire with its own `voip/framing.py`, which counts symbols itself and carries a 16-bit descriptor instead, so the two framing layers each own the wire they read. `tests/test_fsk_resync.py` replays the measured slip profile, and `tools/check_recording.py` scores a recording before you decode it. `image_fsk.py` converts between activation matrices and bits, and its `budget()` function gives the call length. `channel_sim.py` simulates the call offline: GSM, packet loss, AGC and noise.
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
  alignment an RTP path cannot give. The airtime cap is **per generation**,
  in `GENERATIONS[...]["max_seconds"]`: A is capped at `MAX_SECONDS` (5 min),
  because a lossy picture is not worth sitting through, and **B has no cap at
  all** - exactness is the only reason to choose it. `plan()` returns `long`
  past 5 minutes so the page can caution rather than refuse. The biggest B
  will carry is 64x64 RGB at 16 levels: 16.1 min of wire time (14.3 of tones
  plus 12.5% of re-sync markers, 969 s), a 15.5 MB WAV,
  and it still runs in under a second because the "call" is offline. There is
  no modem ceiling underneath this - `image_fsk.encode_image` sends no length
  header, so the geometry comes from the session. The Send page's Track 2
  (`pipeline.run_encode_call`) is uncapped for the same reason;
  `CALL_MAX_SECONDS` is now only the threshold at which the encode response
  sets `long`, and past it the 8 kHz WAV is over `MAX_UPLOAD_BYTES`, so it
  cannot be uploaded back - rebuild from the session instead.
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
reproducible, which is what makes it a training target. Generation B is exact,
and goes up to 64x64 at the cost of airtime (64x64 at 4 levels is 144 s, at 16
levels 287 s, and there is no cap on it); the upscaler is what makes the small
grids worth sending. Generation C was
cut because a byte-exact file transfer has no loss to learn from -- the code
is in the git history, not the tree.

### The call page (`/api/tel`, the Call page)

A call is either **simulated** offline or **placed for real** over SIP. The
simulated one is the default and needs nothing installed beyond libgsm; the
real one needs `pjsua` and a free SIP account, and degrades to an explanatory
message when either is missing.

- `app/api/tel_routes.py` + `app/services/tel_pipeline.py` serve the Call page
  under **`/api/tel`**, deliberately namespaced so the Send and Receive
  contract is untouched: `info`, `stage`, `plan`, `send`, `call`, `upload`,
  `inspect`, `receive`, `enhance`, `dial`, `dial/status`, `dial/{call_id}`,
  `play`, `play/devices`, `play/{play_id}`, `play/stop`, `audio/{id}`,
  `waveform/{id}`, `sent/{id}`, `recovered/{id}`, `enhanced/{id}`. `call` runs
  `spectral/tel/channel_sim.py` (GSM 06.10, packet loss, a wandering level,
  noise and random leading silence) over the transmission.
- **The real call lives in `backend/voip/`.** `dial.py` drives `pjsua` against
  a real SIP registrar — liblinphone's Python bindings are not on PyPI and are
  not needed, since `sip.linphone.org` is an ordinary registrar and the phone's
  Linphone app answers any SIP client. `audio_out.py` is the route that works
  everywhere `pjsua` is not packaged (Windows): play the transmission into a
  virtual audio cable that a softphone uses as its microphone. `call/session.py`
  is the older liblinphone route, still reachable from `voip/cli.py`. Both
  report *why* they cannot run rather than 404ing, so the page can say so.
- **pjsua's file player loops, and nothing on the command line stops it.**
  `--play-file` has no no-loop option and `--auto-play-hangup` only applies to
  `--auto-play`, which feeds incoming calls. The hangup comes two seconds after
  the last tone, so every call used to record the head of a *second*
  transmission. That is not cosmetic: the second preamble scores exactly as
  well as the first, so once Record is pressed a moment late it is the only one
  left, sync lands a few seconds from the end of the file with no transmission
  behind it, and the decoder zero-fills the rest and returns a **blank white
  picture reported as found**. Measured on a real 48x48 call: Record 0.25 s
  late took the rebuild from 100% of pixels exact to a blank frame. `dial.py`
  appends `LOOP_GUARD_SECONDS` (20 s) of silence to the WAV it hands pjsua, so
  the player loops into nothing; the silence is in the *file* only, and
  `expected_seconds` stays the length of the transmission. `voip.sync` defends
  the other side of it — see `find_preamble(need_samples=...)` below.
- `_negotiated_codec` reads the codec out of pjsua's log, and the page shows
  it. It used to search for the word "codec", which pjsua never writes on a
  line that names one (`..audio updated, stream #0: GSM (sendrecv)` and
  `a=rtpmap:3 GSM/8000` are the real forms), so it returned None for every call
  ever placed — the one safeguard against a silent PCMU fallback, silently
  absent. Its test asserted against an invented log line, which is how the two
  agreed with each other and both stayed wrong; it now asserts against real
  pjsua output. `_run` hangs up with `ha`, not `h`: `h` closes whichever call
  pjsua's own cursor points at.
- Audio only comes back from a real call **as a recording made on the phone**
  (Linphone's in-call Record button): pjsua can only record its own inbound
  leg, which is the muted microphone. That recording arrives through `upload`
  (`read_any_audio` -> `voip/audio_io.py` -> ffmpeg, since Linphone writes
  Matroska `.mka` and iOS shares `.m4a`/`.caf`), and `upload` reads what it
  is off the recording itself.
- **Every Call-page transmission is self-describing on the wire.**
  `spectral/tel/descriptor.py` packs generation, grid, colour, levels, lock
  and FEC into 32 bits with a CRC-8, sent twice (28 symbols, 1.12 s) right
  after the FSK preamble: in the modem's header slot for Gen B, and as an
  "announcement" (FSK preamble + descriptor) played before Gen A's pilots.
  `call_track.identify(audio, offset)` rebuilds the full metadata from it
  (`fsk.frame_info` and `tel_encoder.describe` compute the info dicts without
  audio), so the Receive tab needs no send session - a recording made on
  someone else's machine rebuilds alone. A self-described recording's own
  settings always win; a `reference_id` then only supplies the picture to
  score against, and only if it is the same geometry. Reading against the
  wrong send used to report a complete recording as "stops before the
  transmission ends". A recording from before descriptors (or with both
  copies damaged) falls back to the old `reference_id` path.
- `receive` rebuilds from either end, `rx.wav`, `tx.wav` or an uploaded
  recording, and scores it against the activation actually transmitted when
  the send is known. `tel_pipeline.locate()` finds the start — `voip/sync.py`
  for both generations, pilot alignment only for a pre-descriptor Gen A. **Use `voip.sync`, not
  `fsk_codec.find_preamble`, here**: the latter only searches the first few
  seconds, which covers the simulator's 0.12-0.9 s of silence but never a real
  recording, where Record was pressed at some unknown point tens of seconds
  before playback started. `voip.sync` scans the whole file and returns a
  confidence score with the offset. `locate()` also passes
  **`need_samples`** — how much audio the transmission occupies, worked out
  before the search — and `find_preamble` then prefers the best candidate with
  room for it. That is what keeps a recording holding the preamble twice from
  syncing to the copy that has nothing behind it. The fallback is deliberate:
  when *nothing* has room the recording really is short, and the best-scoring
  offset with `truncated` set is the honest answer.
- **A truncated recording no longer claims to know which end is missing.**
  Record pressed after the tones began looks exactly like Record stopped before
  they ended, and naming only the second sends people off to re-record the same
  way again. The message gives the shortfall in seconds and both causes.
- **Whether a recording can carry a picture is decided by `symbol_error`, not
  by how loud anything was.** `tel_pipeline.symbol_error` decodes the payload,
  re-encodes it through Hamming(7,4) and the interleaver, and counts the
  symbols that disagree — a measurement that needs no reference picture, no
  send session and no knowledge of the channel, which is the situation the
  Receive tab is in. It replaced a rule (one bin dominating **and** the symbols
  winning by under 100x) that was calibrated against PCMU and was quietly
  invalidated when the project switched to GSM:

  | | bin imbalance | median margin | symbol errors |
  |---|---|---|---|
  | clean, no channel | 50x | 341 | 0.00% |
  | **real GSM call, 100% of pixels exact** | 45x | **50** | **0.00%** |
  | `hello.mkv`, damaged | 45x | 56 | 10.81% |
  | `me_rec.mkv`, destroyed | 47x | 59 | 11.61% |

  The margin does not separate them — the *perfect* recording scores lower than
  both damaged ones — because GSM 06.10 models every 20 ms frame with an 8-pole
  LPC envelope and squashes the runner-up tone whether or not anything is
  wrong. So every good GSM call was told its tones had not survived, and told
  to go and change phone settings that were already right. The margin is also
  insensitive to the damage that matters: tilting the band 0 to 80 dB moved it
  157 -> 145 while the symbol error went 0 -> 3.5%. `SYMBOL_ERROR_WARN` is
  0.02, calibrated on the same call:

  | symbol errors | 0.00% | 0.25% | 1.19% | 2.06% | 3.50% | 10.81% |
  |---|---|---|---|---|---|---|
  | pixels exact | 100% | 99.96% | 99.52% | 98.91% | 96.92% | ~87% |

  Imbalance and margin are still reported — they say something about *how* a
  recording is damaged — but they decide nothing.
- **Clipping is reported and is not treated as damage.** It used to raise an
  alarm of its own at 2%. GSM overshoots on its own (this modem hands it a
  signal peaking at 0.70 and gets one pinned at 1.0 back, **4.6% of samples
  clipped on a perfect call**), so the threshold fired on every GSM recording
  and blamed the phone's volume for what the codec was doing. And clipping
  costs this modem nothing anyway: driven to 2x, 10x, 500x full scale — 95% of
  samples clipped, near enough a square wave — the symbol error stays 0.00%,
  because 16-FSK decides by which tone bin is largest and squaring a sine
  leaves its fundamental on top. `clipped_fraction` and `saturated` are still
  in the payload as facts about the recording.
- `app/main.py` mounts that router inside a `try/except ImportError`, so a
  machine missing the call dependencies still starts the rest of the app and
  `/api/tel/*` answers 503 with what to install. Keep that guard.
- Frontend: `pages/Call.jsx` + `Call.css`, `api/telClient.js`, and a `/call`
  route. `telClient.js` is kept apart from `client.js` on purpose. Two tabs,
  both kept mounted so switching never throws work away: **Send** (picture,
  encode, simulated call or a real one, rebuilt) and **Receive** (drop a
  recording, inspect it against the send, rebuild).
- `tests/test_tel_simulation.py` walks the simulated path over HTTP for both
  generations; the GSM tests skip without libgsm. The `tests/test_voip_*.py`
  suite covers the call package, skipping cleanly without ffmpeg/pjsua/the SDK.

### Undoing the channel (`spectral/channel/inverse.py`)

The other half of `effects.py`, and the Signals and Systems core: if the
channel was LTI then `Y(f) = H(f)X(f)`, so dividing by H(f) gets X back.
Division is regularised as `H / (H^2 + eps)`, which is what stops a near-zero
H(f) from amplifying noise; `EPSILON` is 1e-5, chosen by measuring both ways
(1e-6 inverts a clean low-pass better, 1e-5 wins once there is noise).

- `undo_chain` walks the chain **in reverse** and reports three outcomes,
  which are deliberately not merged: `undone` (a real inverse ran),
  `attempted` (band-stop: the shoulders come back, the killed rows do not)
  and `skipped` (no inverse exists, so nothing was done). `INVERTIBLE`/`WHY`
  is the single source of that table; `/api/channel/effects` serves it as
  `inversion` and the Experiments page renders it, so the page cannot claim
  something the code does not do.
- Measured end to end at 64x64, mean pixel error: echo 37.98 -> **0.0000**
  (exact), low-pass 80.90 -> 15.17, band-stop 50.05 -> 29.68 (better, still
  broken), clipping unchanged by definition. `tests/test_inverse.py` pins that
  ordering.
- `POST /api/channel` takes `undo`, `epsilon` and `restore`, and returns
  `undone` and `restored` blocks, each with its own image, metrics and row
  profile beside the damaged one.
- `undo_gain` must be told the peak the encoder aimed for (0.8 open, 0.5
  locked). Scaling to anything else costs ~0.08 of mean activation error,
  because the decoder divides by the gain in the metadata.

### The restoration models (`spectral/restore/`)

Two checkpoints in `tools/`, one per track, both the same U-Net apart from the
first layer. `spectral/restore/model.py` holds that network **once** and reads
the shape off the file (`inp.weight` is `(base, in_channels, 3, 3)`), so
retraining and dropping the new `.pt` in place is all a new model needs -
including a different width or a different number of input planes. Both
wrappers re-read the file when its mtime changes, so no server restart either.
`load_state_dict` is strict: a file that is not this network fails at load
rather than quietly producing worse pictures.

Neither checkpoint recorded how it was fed, so **both conventions were
measured**, not guessed, by running every plausible combination against known
pairs. Both predict a **residual** - the change to add to the input, not the
picture. Reading the output directly scores 2-6x worse on both.

**Track 2 - `upscaler.py`, `tools/upscaler_best.pt`, `POST /api/tel/enhance`.**
3 channels in. Upscales and dequantises a Generation B picture (32x32 at a few
levels -> 128x128); input is RGB 0-1 resized to 128 with bicubic. Beats plain
bicubic 27.63 vs 33.80 mean pixel error on the real call path. The Call page's
"Enhance with the model" button is the only caller, and the result is shown as
a third picture captioned as a guess, never merged into the rebuilt one.

**Track 1 - `restorer.py`, `tools/restore_v3.pt`, `POST /api/channel`
with `restore: true`.** 5 channels in, exactly RESTORATION_PLAN Phase 3:
0-2 the activation, 3 the row index (0 at the top row, which is `f_max`), 4 a
confidence mask (**1 where the row arrived, 0 where a stop-band killed it** -
measured; the other polarity scores worse). It runs **after** the LTI inverse,
never instead of it, because the inverse is exact where it applies.

`restore_v3.pt` replaced `restore_best.pt`, which is still in `tools/` and
still loads if `SPECTRAL_RESTORER` points at it. Both are the same 5-channel
U-Net, so the swap was the path and nothing else - `model.py` reads the shape
off the file. Both were measured, not assumed, on the same conventions:
residual output (reading it directly scores ~5x worse on both) and
1-where-alive on channel 4 (~7x worse flipped).

Measured end to end at 128x128 grayscale, 16 levels, mean activation error
against the activation sent - and the reason the bench shows three columns
rather than one number:

| | damaged | + inverse | + v3 | + restore_best |
|---|---|---|---|---|
| clipping | 0.0453 | 0.0453 | **0.0431** | 0.0373 |
| clip + noise | 0.0423 | 0.0423 | 0.0424 | **0.0365** |
| noise | 0.0003 | 0.0003 | **0.0004** | 0.0379 |
| band-stop | 0.0826 | 0.0472 | **0.0427** | 0.0820 |
| low-pass | 0.1444 | 0.0450 | **0.0417** | 0.0795 |
| echo | 0.0533 | **0.0000** | **0.0000** | 0.0377 |
| already clean | 0.0000 | **0.0000** | **0.0000** | 0.0377 |

The trade changed shape, which is the reason to prefer v3. The old checkpoint
bought its clipping win by repainting everything it touched - 0.0377 of error
into a picture with nothing wrong with it, and it broke echo, which the
inverse had already undone exactly. v3 is far gentler: its residual on clean
input is +0.0076 against the old one's +0.0408, it keeps echo exact, and it
improves band-stop and low-pass *on top of* the inverse where the old one made
both markedly worse.

**The 0.0000 entries above are at 16 levels and are a quantisation result, not
an identity.** The step at 16 levels is 0.0667, and v3's residual on clean
input is about a tenth of that, so the quantiser swallows it whole. Raise
`gray_levels` and it reappears: clean input scores 0.0000 / 0.0019 / 0.0061 /
0.0076 at 16 / 32 / 64 / 256 levels, changing 0.1% of pixels at 16 and 91% at
256. v3 is an order of magnitude quieter than the old checkpoint, which is the
real claim; it is not a no-op. That is why `restore` stays **off by default**
and the page still reports all three stages.

The same timidity is why v3 barely moves clipping. That damage is nearly pure
bias - mean signed error -0.0448 against mean absolute 0.0453, with 43% of
pixels exactly one level too light - and both checkpoints push the right way,
but v3's residual is 27% of the needed magnitude where the old one's is 87%.
Under a 0.0667 step a 0.0129 correction moves only 5.8% of pixels across a
level boundary. On pixels actually corrected v3 still nets slightly ahead
(fixes 3.5%, breaks 0.9%) of the old checkpoint (fixes 21.1%, breaks 19.0%),
which wins on MAE by churning half the image.
Those numbers move when the checkpoint is replaced; nothing else has to.

torch is **in `requirements.txt`** (pinned to 2.14.0), so both models are on by
default. The graceful-degradation path is still intact and worth keeping: drop
the line and everything except the two model buttons still runs. `status()` on
each wrapper drives the button, `/api/tel/info` and `/api/channel/effects`
carry it as `model`, and the endpoints answer 503 with what to install.
`tests/test_upscaler.py` and `tests/test_restorer.py` skip without it, which is
why the suite reports 3 skips with torch present and 22 without.

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
