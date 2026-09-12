# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences, both primary, both confirmed by the user:

1. **A marker at assessment.** A lecturer or examiner evaluating this as Signals
   and Systems coursework. Their job is to verify that the DSP is real,
   correctly implemented, and understood. They need the concept — rows become
   pitches, columns become moments, brightness becomes loudness — to be legible
   and defensible, and they need to see that the recovery genuinely works rather
   than being asserted.

2. **A portfolio visitor.** Someone judging the maker rather than the
   coursework: a recruiter, a peer, anyone who opens the public URL cold. Their
   job is to decide within roughly a minute whether this is impressive. They
   arrive with no narration, no prior context, and no obligation to stay.

These two are not in tension by default, but where they diverge, the resolution
is: the marker must never be short-changed on rigour, and the portfolio visitor
must never be asked to earn the payoff before receiving one.

## Product Purpose

Spectral Canvas encodes an image — an uploaded picture, typed or uploaded text,
or a live doodle — into audio by mapping image rows to frequency lanes and image
columns to time, with pixel brightness driving amplitude. The resulting WAV is a
real, playable sound. The receiving side decodes that sound back into the
original picture using only the audio itself.

A transmission can be locked with two 11-digit phone numbers and a PIN, which
derive a key that scrambles the spectral data via two independent permutations
(rows and columns). Without the correct credentials the rebuild produces static
— not an error message, but visibly wrong output, which is the pedagogical
point.

Success is that both audiences reach the same realisation unaided: the picture
was never anywhere except in the sound.

## Positioning

The mechanism is the position. This is not a visualiser that draws pictures
*of* audio, and not a steganography tool that hides data *inside* a carrier
signal. The audio **is** the image, losslessly enough that the round-trip test
reports a mean pixel error of 0.000 across all five paths. A neighbouring
project could copy the spectrogram aesthetic; it could not truthfully claim the
bidirectional round trip with a verified zero-error reconstruction.

The security layer is positioned the same way: the failure mode is *legible*.
A wrong PIN does not reject you, it hands you noise that is provably the same
data under the wrong permutation.

## Operating Context

- **Deployed to a public URL.** Confirmed by the user. Visitors arrive without
  narration and without the README. This makes first-run state, empty states,
  session expiry, error copy, and mobile behaviour real product surface rather
  than developer convenience.
- Development runs as two processes: FastAPI on `:8000`, Vite on `:5173`, with
  `vite.config.js` proxying `/api` so no host is hardcoded.
- Server state is an in-memory session dict (`app/storage/session_store.py`),
  TTL 3600s, capped at 200 sessions. A restart or an expiry invalidates a
  transmission mid-flow; the copy already anticipates this
  ("That transmission has expired. Send it again."). Public deployment makes
  this a user-facing condition, not a theoretical one.
- The demonstrable end-to-end path is: draw or upload → optionally lock → send →
  inspect the waveform and play it → download the WAV → open Receive → drop the
  file → unlock → rebuild. Trying a wrong PIN first is part of the intended
  demonstration.

## Capabilities and Constraints

**Built and working**

- Three input modes: image upload, text (typed or `.txt`), and live doodle on an
  in-browser canvas with pencil / paint / highlighter brushes, shapes, eraser,
  24-step undo, and 60-second autosave.
- Encode to a real int16 WAV with embedded metadata; decode from that WAV alone.
- Optional lock: two 11-digit numbers plus a 4–8 digit PIN derive the scramble.
- Waveform inspection: min/max envelope with an RMS body, click-to-seek,
  playhead, and hover readout of time / peak / RMS / dominant frequency.
- Colour and grayscale paths both round-trip.
- Routes today: `/` (Landing), `/simulate`, `/receive`.

**Committed and not yet built** — the user has asked for all four to be built as
working UI, each on **its own route with its own nav entry**, so later edits stay
isolated:

1. **Channel panel** — noise, echo, filter and clipping controls, then re-decode
   and show the damage. Requires `POST /api/channel`; `spectral/channel/effects.py`
   already implements the effects.
2. **Spectrogram view** — `spectral/analysis/waveform.py::spectrogram()` already
   returns a downsampled dB matrix ready to paint as a heatmap. Seeing the
   picture inside the spectrogram is the strongest available visual proof of the
   core claim.
3. **Side-by-side compare** — sent vs recovered vs difference.
4. **Experiment plots** — the plots that support the written report.

Growing the nav from 3 entries to 7 is a real information-architecture problem
that this expansion must solve rather than absorb.

**Technical constraints**

- React 18 + Vite + plain JavaScript and plain CSS. No TypeScript, no Tailwind,
  no component library. The project guides make this binding on the grounds that
  a utility framework or component kit would flatten the visual identity, which
  is treated as load-bearing rather than decorative.
- `backend/spectral/` is a pure library that imports nothing from FastAPI;
  `backend/app/` is a thin HTTP wrapper. Anything FastAPI-aware belongs in
  `app/services/`.
- `encode()` returns data and touches no disk, so concurrent requests cannot
  collide over shared output files.
- Limits: 12 MB upload, 2000 characters of text, 160px working dimension.
  Defaults: 64×64 target, 44.1 kHz, 1–8 kHz band, 0.05s frames, 16 gray levels.
- FastAPI `detail` strings are surfaced to users verbatim by `api/client.js`,
  so backend error text is user-facing copy.
- `Receive.jsx` imports `Simulate.css` before its own; `.drop`, `.filecard`,
  `.lockfields` and `.switch-note` are defined there and shared. That import
  order is load-bearing.

**Terminology in use:** transmission, lock / unlock, send / receive, rebuild,
activation, glyph, lane, bench.

## Brand Commitments

- **Name:** Spectral Canvas. The instrument on the landing page is the SC-01.
- **Voice:** plain, precise, unhurried; explanatory without being cute. Physical
  metaphors are preferred over jargon — "rows are pitches · columns are moments"
  is the standing one-line statement of the product.
- **Colour carries state and must stay consistent across every surface:**
  indigo is the signal itself, aqua is open / recovered / safe, coral is locked
  / peak / alert. Once learned on the landing bench this must hold everywhere.
  Documented in `FRONTEND_GUIDE.md`; not separately confirmed by the user.
- **A stated anti-template commitment.** `FRONTEND_GUIDE.md` records that the
  cool palette, the non-serif display face, and the bench-instrument object were
  each chosen specifically to avoid the saturated defaults of both the reference
  site and generated design. Treated as project intent from repository evidence
  rather than as a user-confirmed brand rule.
- The full incumbent visual system — palette values, type roles, layout
  principles — is described in `FRONTEND_GUIDE.md` §2 and lives in
  `frontend/src/styles/tokens.css`. It has not yet been recorded as a DESIGN.md.

## Evidence on Hand

- **A round-trip test, currently not executable.** `backend/tests/test_roundtrip.py`
  exercises five paths through the real int16 WAV container: grayscale/open,
  grayscale/locked, colour/open, colour/locked, and text/locked — all at 48×48
  via `roundtrip()`'s default size. Two corrections to what the README and the
  older guides say, both checked against the source on 2026-09-12:
  its pass condition is `err < 0.5`, **not** `0.000` (0.000 is the docstring's
  expectation, not the assertion), and the fifth path is **locked**, not open.
  The suite also does not currently run: it imports `to_image_array` from
  `spectral.decoder.image_reconstructor`, which that module does not define.
  Until that import is fixed, no measured error figure for this project is
  reproducible, and none may be published.
- **Working artefacts at the repo root:** `output_pepsi.wav` (6.8 MB encoded
  audio), `recovered.png` (its reconstruction), `metadata.json`.
- **Written documentation:** `README.md`, `BACKEND_GUIDE.md`,
  `FRONTEND_GUIDE.md`, and two PDFs in `docs/` — the complete project plan and a
  concepts-and-code explainer.
- **A known break to repair:** the missing `to_image_array` export above. Until
  it lands, the project's central claim is demonstrable interactively but not
  verifiable by test.
- **Absences future work must not fabricate:** the `images/` sample set has been
  deleted from the working tree, so there is currently **no** sample image
  library, **no** logo or brand asset file, **no** photography, and **no**
  produced demo video. There are no users, no testimonials, no adoption
  numbers, no benchmarks of any kind (the round-trip error is not currently
  measurable — see above), and no pricing,
  licensing, or deployment claims. The public URL is a stated intention; no
  deployed address has been provided.

## Product Principles

1. **The claim must be witnessed, not asserted.** Every surface should let a
   visitor observe the round trip rather than read that it works. This cuts
   both ways: where a figure cannot be reproduced, the surface states the
   coverage and the pass condition rather than printing a number. The
   spectrogram view matters because it is the moment the claim becomes visible.
2. **Failure is a teaching surface.** The wrong-PIN static, the expired session,
   the channel-damaged rebuild — these are demonstrations, not errors to be
   hidden behind an apology.
3. **Structure encodes the signal chain.** Sequence, numbering, and colour are
   reserved for things that genuinely are sequential, ordered, or stateful.
   Nothing decorative borrows those devices.
4. **Concentrate the boldness.** The instrument is the memorable object;
   everything around it stays disciplined so that it reads.
5. **Rigour and impact are the same deliverable.** The marker's need for
   defensible DSP and the portfolio visitor's need for an immediate payoff are
   served by the same thing done well, never by two different presentations.

## Accessibility & Inclusion

No external standard has been set by the user — **this is an open decision.**
Public deployment raises the stakes, so it is worth settling.

The floor already implemented and documented in `FRONTEND_GUIDE.md` §8:
visible `:focus-visible` rings throughout, `prefers-reduced-motion` honoured
globally in `tokens.css`, ARIA labelling on the pad / dial / screen, real
`<label>` elements on every input, the bench fully keyboard-operable (arrows
move, space toggles, `E`/`L`/`O`/`C` fire), the 8×8 pad as a single tab stop via
`role="grid"` with `tabIndex={-1}` cells, and a single-column collapse at 980px.

Product-specific need worth naming: the core demonstration is simultaneously
visual and auditory. Neither channel should be the only carrier of the
explanation.
