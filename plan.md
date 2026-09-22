# plan.md — Spectral Canvas: real VoIP transport (`reco_deco`)

> **Owner:** Abrar
> **Branch:** `reco_deco`, cut from `origin/Voip-simulation`
> **Deliverable:** `backend/voip/` — a new package inside the repo, not a ZIP
> **Status:** implemented. Sections 1–3 explain what changed and why; 4–9 are the
> reference; 10 is what still needs the phone.

---

## 0. What this is

Send an image or a message from the laptop to a phone through a **real Linphone
VoIP call**. The laptop turns the picture into 16-FSK modem audio and plays it
into the call. The phone records it with Linphone's in-call Record button. The
recording comes back to the laptop, which decodes it into the picture and
writes a quality report.

---

## 1. How this plan changed once the codebase was read

The original version of this document assumed the teammate's modem did not
exist yet. It therefore specified a self-contained repo-root `voip_transport/`
with its **own bundled reference modem** — its own FEC, framing, tone table and
image codec — plus a `TeamModem` adapter to swap in his version later, across
roughly forty files.

That assumption was wrong. On `origin/Voip-simulation` the following already
existed and worked:

| already built | what it is |
|---|---|
| `backend/spectral/tel/fsk_codec.py` | the verified 16-FSK modem, with **exactly** the parameters this plan specified for its "reference modem": 8 kHz, 40 ms symbols, 16 tones 700–3200 Hz, Tukey 0.25, Hamming(7,4), interleave depth 16, 8-symbol preamble, 7-symbol 16-bit header |
| `backend/spectral/tel/image_webp.py` | **Generation C**, which this plan listed as out of scope: WebP → Reed-Solomon(32) → PIN-keyed byte shuffle → 16-FSK |
| `backend/spectral/tel/image_fsk.py` | Generation B: raw quantized pixels ↔ bits |
| `backend/app/services/tel_pipeline.py` | `run_send` / `run_call` / `run_inspect` / `run_receive` |
| `backend/app/api/tel_routes.py` | the whole `/api/tel/*` surface |
| `frontend/src/pages/Call.jsx` | a Call page with Send and Receive tabs |
| `backend/spectral/tel/run_local_call.sh` | a working pjsua SIP loopback |

Building the original plan would have duplicated a verified modem and
guaranteed a merge conflict. So:

**Cut** — `modem/` (constants, fsk, fec, framing) and `payload/image_codec.py`,
which duplicate `fsk_codec.py` and `image_fsk.py`. `adapter.py`, `TeamModem`,
`SC_MODEM=team`, `SC_TEAM_MODEM_MODULE`: there is no modem to adapt *to*, it is
imported directly. "Generation C out of scope": it is done and shipping.

**Kept** — the Linphone account setup, the SDK build and its one-day time-box,
the BlackHole fallback, the phone-side call ritual, the troubleshooting table,
and the experiment programme.

**Changed** — the folder moved from a repo-root `voip_transport/` to
`backend/voip/`, a sibling of `app/` and `spectral/`, so it imports as a
top-level package exactly as they do and every command runs from `backend/`.

---

## 2. The four defects a real recording exposed

These are the actual content of this branch. None of them shows up in a
simulated call, because the simulator hands the decoder an array it produced
itself, seconds ago, in the same process.

**1. The preamble search only looks at the first 3 seconds.**
`fsk_codec.find_preamble(audio, search_seconds=3.0, step=4)`. The simulator
prepends at most 900 ms of silence, so this never mattered. On a real call you
press Record, walk back to the laptop and press Enter, and the transmission
starts 10 to 60 seconds in — outside the window, every time. Widening it
naively is not an option either: at `step=4` a 60-second search is 120,000
separate 8×320 FFT batches.

**2. The score is computed and thrown away.** `find_preamble` returns an offset
and nothing else, so there is no way to say "there is no transmission in this
audio" — it always returns *some* offset, confidently.

**3. No per-symbol confidence anywhere.** `argmax` picks the winning tone and
discards the margin, so "it decoded" and "it decoded with nothing to spare"
look identical.

**4. A truncated recording decodes into fabricated bytes.**
`fsk._symbol_magnitudes` zero-pads when the audio runs out and reports nothing.
Measured on this modem: a header claiming 600 bytes against audio holding 592
symbols returned a full 600 bytes, of which 297 matched — with no error.

Plus `read_any_wav` being scipy-only, while Linphone records **Matroska**.

### How they were fixed without touching the modem

`read_header(audio, offset=None)` and `demodulate(audio, info, offset=None)`
both already accept an explicit offset; the 3-second window is only reached
when it is `None`. So `voip/` computes the offset itself and always passes it
in. `fsk_codec.py` is not edited by one character.

That matters for two reasons. The published 0.25 % symbol-error figure in
`README_TELEPHONY.md` was measured with the current search behaviour, and
returning a score as well as an offset would be a breaking signature change to
a file the teammate's web page depends on.

Measured, against the real modem:

```
   lead   new off   err  score    t(s) |   old off      err    t(s)
   0.0s         0    +0  0.997   0.010 |         0       +0   0.152
   2.5s     20000    +0  0.997   0.006 |     20000       +0   0.131
  12.0s     96000    +0  0.997   0.007 |      6891  -89109   0.130
  45.0s    360000    +0  0.997   0.013 |      7079 -352921   0.130

noise only: score 0.109  ->  correctly reported as "no transmission"
```

Sample-exact at every lead, 10–20× faster even where the old one still worked,
and a score that separates signal (0.997) from noise (0.109) cleanly enough to
threshold at 0.35.

---

## 3. What was built

```
backend/voip/
├── README.md          setup, the five commands, how to read a report
├── config.py          constants, thresholds, exceptions
├── _tel.py            lazy access to spectral/tel (bare sibling imports)
├── audio_io.py        .wav via scipy, .mka/.m4a/.caf via ffmpeg; 16-bit WAV out
├── dsp.py             symbol magnitudes + the confidence argmax discards
├── sync.py            whole-file preamble search, vectorised, with a score
├── framing.py         the 16 header bits; bounds checks against the recording
├── payload.py         Gen-B / Gen-C / text; sniffing what came back
├── encode.py          prepare(): source -> tx.wav + manifest.json
├── decode.py          decode(): recording -> picture/text + report.json
├── quality.py         metrics, the verdict ladder, human hints
├── report.py          run folders, numpy-safe JSON
├── simulate.py        rehearsals: codec-free, GSM, and real SIP loopback
├── cli.py             check-env / prepare / call / decode / simulate
└── call/
    ├── sdk.py         lazy import, try_set, capability probe
    ├── session.py     register -> dial -> play -> stats -> hang up
    └── fallback.py    BlackHole route, needs no SDK
```

Three existing files were touched, all glue, none of them DSP:

- `app/services/tel_pipeline.py` — added `read_any_audio` (ffmpeg fallback);
  `_find_transmission` now locates the preamble with `voip.sync` over the whole
  file and returns a score, weak-symbol count and truncation flag;
  `run_inspect` and `run_receive` report them. `read_any_wav` is unchanged byte
  for byte, and every existing response field is preserved, so `Call.jsx` keeps
  working untouched.
- `app/api/tel_routes.py` — `/inspect` accepts non-WAV containers.
- `app/config.py` — `MAX_AUDIO_UPLOAD_BYTES = 32 MB`, because a two-minute
  Generation C transmission recorded at 48 kHz stereo is ~23 MB and the 12 MB
  picture limit would reject a perfectly good recording.

---

## 4. Wire format

**Generation C** is unchanged and bit-identical to what the web page produces:
`[8 preamble][7 header = 16-bit packet byte count][2 symbols per byte]`. Audio
prepared here still opens in `image_webp.receive_image`.

**Generation B** had no header at all — `encode_image` sends none, and the
shape and gray-level count live only in the sender's `info` dict, which is
useless when the receiver has nothing but audio. It now gets a descriptor in
the same 16 bits:

```
bit 15..12  0xF   marker      (a Gen-C packet would need >= 61440 bytes = 82 minutes)
bit 11.. 7  rows - 1          (1..32)
bit  6.. 2  cols - 1          (1..32)
bit  1.. 0  levels code       0=2, 1=4, 2=16, 3=256
```

Every other field derives arithmetically: `n = rows*cols*log2(levels)`,
`hamming_pad = (-n) % 4`, `coded = (n+hamming_pad)//4*7`,
`interleave_pad = (-coded) % 16`, `n_symbols = (coded+interleave_pad)//4`,
`symbol_pad = 0`. Verified against `modulate()`'s own info dict for every
shape. Generation B is grayscale only; colour goes over Generation C.

**Text** goes through the Generation C pipeline as `b"SCTX" + utf8`.
`spectral/text/text_codec.py` cannot be reused: it is 44.1 kHz MFSK between 2
and 5 kHz with no FEC, and a voice channel keeps roughly 300–3400 Hz, so most
of its tones never arrive — the same reason the original 64-row image encoder
had to be abandoned for this path. Reed-Solomon gives byte-exact text or an
honest failure, keeps the PIN lock, and needs no new code path. Images carry no
prefix, so the format stays backward compatible.

---

## 5. Commands

```bash
cd backend && source .venv/bin/activate

python -m voip.cli check-env                                  # run this first
python -m voip.cli prepare --image cat.jpg --gen B --grid 16
python -m voip.cli simulate --run latest --lead 30 --decode   # fake call
python -m voip.cli simulate --run latest --pjsua --decode     # REAL SIP call
python -m voip.cli call --run latest --dial sip:you@sip.linphone.org
python -m voip.cli decode ~/Downloads/call.mka --run latest --json
```

Credentials come from `VOIP_SIP_IDENTITY` / `VOIP_SIP_PASSWORD` /
`VOIP_SIP_DIAL`, never the command line. There is a test asserting the password
never reaches stdout, stderr or `call.json`.

| | airtime | when |
|---|---|---|
| `--gen B --grid 16 --levels 4` | 9.6 s | first calls, and anything you expect to retry |
| `--gen C --size 96` | ~60 s | the demo |
| `--gen C --size 128` | ~86 s | the demo, on a good line |
| `--text "..."` | ~7 s / 40 chars | messages |

Start with Generation B: a tenth of the air time, and it degrades gracefully —
a bad symbol costs pixels. Reed-Solomon does not; past 16 bad bytes in a
255-byte block the picture fails to open and tells you nothing about why.

---

## 6. The rehearsal ladder

Each rung catches something the one below cannot. Do not skip the third.

1. **Codec-free** (`simulate --no-gsm`) — long lead-in, telephone band, AGC
   wander, packet loss. Needs no tools, so it runs in CI.
2. **Real GSM 06.10** (`simulate`) — the teammate's `channel_sim`, via ffmpeg
   with libgsm or `toast`.
3. **Real SIP loopback** (`simulate --pjsua`) — two pjsua instances calling
   each other. Real signalling, real RTP packetisation, a real jitter buffer,
   real packet-loss concealment, real GSM, with **no account, no phone and no
   SDK**. It catches what the simulator cannot: a `tx.wav` that is not 8 kHz
   16-bit mono PCM (pjsua refuses to open one at all), an off-by-one in the
   lead-in padding, and a codec silently falling back to PCMU.

**Check the negotiated codec on every run.** If libgsm was not compiled in,
pjsua falls back to G.711 without complaining, and G.711 is nearly transparent
— a clean decode over it proves almost nothing. The CLI prints what was
negotiated and warns when it differs from what was asked for.

---

## 7. Results so far

All on this machine, no phone involved yet.

| path | channel | result |
|---|---|---|
| Gen-B 16×16 @ 4 levels | codec-free, 30 s lead | pixel-identical |
| Gen-B 16×16 @ 4 levels | **real SIP call**, GSM 06.10 | **pixel-identical**, preamble 0.79, 0 weak symbols |
| Gen-C 96×96 RGB | **real SIP call**, GSM 06.10, 65 s | **pixel-identical**, 0 bytes repaired of a 64-byte budget |
| Gen-C 128 px | codec-free, 30 s lead | pixel-identical |
| text, incl. emoji | codec-free | byte-exact |
| locked, right PIN | codec-free | opens |
| locked, wrong PIN | codec-free | `wrong-pin`, static image |
| recording cut short | codec-free | `truncated`, names the missing symbol count |
| noise only | — | `no-sync`, score 0.108 |

164 tests, 3 skipped on a machine with everything installed; the whole suite
also passes with no ffmpeg, no libgsm, no pjsua and no SDK.

---

## 8. Reading a report

`report.json` opens with a flat `summary` block holding the four numbers the
troubleshooting table refers to, so they stay exactly where they are:

| field | healthy | if it looks wrong |
|---|---|---|
| `preamble_score` | above ~0.8 | below 0.35 → no transmission found. Wrong file, or Record pressed too late |
| `offset_s` | your lead-in plus however long you took | near 0 or near the end → sync locked onto noise |
| `payload_bits` | what `prepare` printed | different → the header was corrupted; everything after is meaningless |
| `weak_symbols` | 0, or a handful | many → DSP left on, or a speech codec negotiated |

`verdict` runs `no-sync` → `bad-header` → `truncated` → `rs-failed` →
`wrong-pin` → `ok`, ordered by how early things went wrong, and `hints` says
what to try next.

---

## 9. Linphone accounts and the SDK

Two accounts from <https://subscribe.linphone.org> — one for the laptop, one
signed in on the phone. Calling the account you are calling *from* does not
work. There is no API key; "the Linphone API" means the liblinphone library
plus a SIP account.

On the phone, before the first call: **PCMU only**, echo cancellation **off**,
noise suppression **off**, **mute the microphone** during the transmission, and
record with the **in-call Record button** — a normal voice-recorder app cannot
capture another app's call audio, Android blocks it and iOS does not allow it.

The Python wrapper is not on PyPI for macOS on Apple silicon and has to be
built from the SDK source. **Time-box it to one day.** The fallback reaches the
same channel and needs no SDK:

```bash
brew install blackhole-2ch
# Linphone desktop → Preferences → Audio → input: BlackHole 2ch, EC off, PCMU only
# place the call by hand, then:
python -m voip.cli call --run latest --fallback
```

Say which route produced which result in the write-up; the fallback is a
stopgap, not the API integration.

---

## 10. What still needs the hardware

Everything above the SIP loopback is verified. These are not:

- **Every liblinphone attribute name** in `call/session.py`. They come from the
  5.5 reference and could not be run against a real SDK. Each access goes
  through `sdk.try_set`, so a renamed attribute warns in `call.json` rather
  than crashing mid-call, and `check-env` prints `sdk.probe()` — look at it
  before dialling. What is untestable is the *behaviour*: whether disabling AGC
  actually stops the tones being squashed.
- **Whether the bindings build at all** on Apple silicon. The largest schedule
  risk, hence the fallback being a peer route rather than an afterthought.
- **Whether the phone's Record button captures far-end audio**, and in which
  container and codec.
- **Whether the carrier path negotiates GSM/AMR-NB or something transparent**
  like Opus at 48 kHz.
- **Clock drift.** The laptop's DAC and the phone's ADC are independent
  oscillators; ~260 ppm would walk half a symbol over an 86-second
  transmission, and Bluetooth or AirPlay resamplers can exceed that.
  `decode --drift-scan` searches ±300 ppm. If drift dominates, the real fix is
  a postamble for a two-point estimate — that changes the wire format, so it is
  a decision, not a tweak.

### Order for the first real call

1. `check-env` on the Mac, and read the SDK probe.
2. `call --dry-run` — register and probe, place no call.
3. **The fallback route first**, Generation B 16×16. Nine seconds, cheap to retry.
4. The SDK route, Generation B.
5. Generation C at 96 px, then 128 px for the demo.

### Experiments worth running once one call works

Change one variable at a time, three repeats each: codec
(PCMU → PCMA → GSM → Opus), network (same Wi-Fi → mobile data), payload size,
phone DSP on versus off, and FEC on versus off. `report.json` already carries
everything those need — `preamble_score`, `weak_fraction`, `repaired_bytes`,
`rs_headroom` and the RTP loss rates from `call.json`.

---

## 11. Still open, and not this branch's job

`to_image_array` is imported by `app/services/pipeline.py:193` and
`tests/test_roundtrip.py` but exists in no committed version of
`image_reconstructor.py`. The main (non-telephony) decode path and the original
round-trip test are both broken by it, on every branch. `tests/conftest.py`
skips collecting that file so the rest of the suite can run; fixing it belongs
to whoever owns the image pipeline.
