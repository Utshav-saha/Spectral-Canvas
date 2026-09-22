# voip — sending a picture through a real phone call

The rest of Spectral Canvas sends a picture through a *simulated* call:
`spectral/tel/channel_sim.py` runs the modem audio through GSM 06.10 offline.
This package sends it through a real one.

```
  prepare            call                    (the phone)          decode
  ────────           ────                    ───────────          ──────
  picture            register with SIP       answer               find the preamble
  → WebP + RS        dial the phone          mute the mic         read the header
  → 16-FSK tones     play tx.wav into it     press Record         demodulate
  → tx.wav           hang up                 send the file back   → picture + report
```

Nothing here reimplements the modem. `spectral/tel/fsk_codec.py` is the ground
truth and is imported unchanged, along with `image_webp.py` (WebP →
Reed-Solomon → PIN-keyed byte shuffle) and `image_fsk.py` (raw pixels).

## Why the package exists

A recording made on a phone differs from an array in memory in four ways that
each broke something:

| | |
|---|---|
| **The transmission starts at an unknown time.** You press Record, walk back to the laptop, press Enter. | `fsk_codec.find_preamble` searches the first **3 seconds** and gives up. `sync.py` searches the whole file — and is faster doing it, because it transforms every candidate frame once instead of per offset. |
| **You cannot tell a failed sync from a real one.** | `find_preamble` returns an offset and discards the score it computed. `sync.py` returns both, so "there is no transmission in this audio" is finally sayable. |
| **"It decoded" is not the same as "it decoded well."** | `argmax` throws away the margin. `dsp.py` keeps it, so a report can say how close the call came to failing. |
| **Recordings get stopped early.** | `fsk._symbol_magnitudes` zero-pads a short tail, so the modem returns confident garbage. Measured: a header claiming 600 bytes against slightly-short audio returned 600 bytes, of which 297 matched. `framing.py` clamps and reports the shortfall. |

Plus: Linphone records **Matroska** (`.mka`), which scipy cannot open, so
`audio_io.py` routes anything that is not a WAV through ffmpeg.

All four are worked around **without editing the modem**. `read_header` and
`demodulate` already accept an explicit `offset`; this package computes it and
passes it in.

## Setup

```bash
brew install ffmpeg libgsm pjproject        # decode, rehearsal, SIP loopback
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m voip.cli check-env                # run this first, always
```

Python 3.12 rather than 3.14: the pinned numpy and scipy predate 3.14 and have
no wheels for it, and the Linphone bindings are even less likely to.

Homebrew's ffmpeg is built without libgsm, which is why `libgsm` is installed
separately — `channel_sim` falls back to its `toast` binary.

## A run, end to end

```bash
# 1. make the audio
python -m voip.cli prepare --image cat.jpg --gen B --grid 16 --levels 4

# 2. rehearse. Twice: no phone involved, and worth doing every time
python -m voip.cli simulate --run latest --lead 30 --decode     # fake call
python -m voip.cli simulate --run latest --pjsua --decode       # REAL SIP call

# 3. the real thing
python -m voip.cli call --run latest --dial sip:you@sip.linphone.org
#    phone: answer → MUTE the mic → Record → (laptop) Enter → wait → stop
#    then AirDrop the recording back

# 4. decode it
python -m voip.cli decode ~/Downloads/call.mka --run latest --json
```

Credentials come from the environment, never the command line:

```bash
export VOIP_SIP_IDENTITY=sip:you-laptop@sip.linphone.org
read -s VOIP_SIP_PASSWORD && export VOIP_SIP_PASSWORD
```

You need **two** SIP accounts from <https://subscribe.linphone.org> — one for
the laptop and one signed in on the phone. Calling the account you are calling
*from* does not work.

### Don't skip the pjsua rehearsal

`simulate --pjsua` puts two pjsua instances on loopback: real SIP signalling,
real RTP packetisation, a real jitter buffer, real packet-loss concealment, and
real GSM — with no account, no phone and no SDK. It is the rung that catches
what the offline simulator cannot, and it takes one command.

**Check the negotiated codec every time.** If libgsm was not compiled in,
pjsua silently falls back to G.711, which is nearly transparent — a clean
decode over that proves almost nothing. The CLI prints what was negotiated and
warns when it is not what you asked for.

## Which generation to send

| | airtime | what it is | when |
|---|---|---|---|
| `--gen B --grid 16 --levels 4` | **9.6 s** | raw quantized pixels, Hamming(7,4) | first calls, and anything you expect to retry |
| `--gen C --size 96` | ~60 s | WebP + Reed-Solomon, full colour | the demo |
| `--gen C --size 128` | ~86 s | as above, more detail | the demo, if the line is good |
| `--text "..."` | ~7 s per 40 chars | UTF-8 through the Gen-C pipeline | messages |

Start with Generation B. It is a tenth of the air time, so a failed attempt
costs ten seconds instead of a minute and a half, and it **degrades
gracefully** — a bad symbol costs you pixels. Reed-Solomon does not: past 16
bad bytes in any 255-byte block the picture does not get worse, it fails to
open at all, and tells you nothing about why.

Text does **not** use `spectral/text/text_codec.py`. That is 44.1 kHz MFSK
between 2 and 5 kHz with no error correction, and a voice channel keeps roughly
300–3400 Hz, so most of its tones never arrive — the same reason the original
64-row image encoder had to be abandoned for this path.

## On the phone, before the first call

| setting | value | why |
|---|---|---|
| Codecs | PCMU (and PCMA) only | a gentle waveform codec; start easy |
| Echo cancellation | **off** | it treats a steady tone as echo and notches it out |
| Noise suppression | **off** | a pure tone looks exactly like a fan |
| During the call | **mute the microphone** | Linphone records both directions, so room noise lands on top of the tones |
| Recording | the in-call **Record** button | a normal voice-recorder app cannot capture another app's call audio — Android blocks it and iOS does not allow it at all |

## Reading a report

`report.json` opens with a flat `summary` block holding the four numbers worth
reading at a glance:

| field | healthy | if it looks wrong |
|---|---|---|
| `preamble_score` | above ~0.8 | below 0.35 → no transmission found. Wrong file, or Record pressed too late |
| `offset_s` | your lead-in plus however long you took | near 0 or near the end → sync locked onto noise |
| `payload_bits` | what `prepare` printed | different → the header was corrupted, and everything after it is meaningless |
| `weak_symbols` | 0, or a handful | many → DSP left on, or a speech codec got negotiated |

`verdict` is one of `ok`, `wrong-pin`, `rs-failed`, `truncated`, `bad-header`,
`no-sync`, ordered by how early things went wrong, and `hints` says what to try
next.

## If the SDK will not build

Expect this. The Linphone Python bindings are not on PyPI for macOS on Apple
silicon and have to be compiled from source. Give it a day, then use the
fallback, which reaches the same channel:

```bash
brew install blackhole-2ch
# Linphone desktop → Preferences → Audio → input device: BlackHole 2ch
#                                       → echo cancellation OFF, PCMU only
# place the call by hand, then:
python -m voip.cli call --run latest --fallback
```

Only the dialling is manual. Say which route produced which result in the
write-up — the fallback is a stopgap, not the API integration.

## Tests

```bash
cd backend && python -m pytest tests/ -q
```

Everything skips cleanly on a machine with no ffmpeg, no libgsm, no pjsua and
no SDK. The end-to-end tests use a codec-free rehearsal for exactly that
reason; the GSM and SIP rungs live in `test_voip_channel.py`.

`tests/conftest.py` does not collect `test_roundtrip.py`, because it is a
`__main__` script that prints a table rather than a pytest module. Run it on
its own with `python -m tests.test_roundtrip`. (It used to fail to import as
well, for a missing `to_image_array`; that name exists now.)

## Notes for whoever touches this next

- Every liblinphone attribute name in `call/session.py` came from the 5.5
  reference and **none of it has been run against a real SDK**. Every access
  goes through `sdk.try_set` / `try_get`, so a renamed attribute produces a
  warning in `call.json` rather than an exception mid-call. `check-env` prints
  `sdk.probe()`; look at it before the first call.
- Generation B is grayscale only, up to 32×32. The 16-bit header has room for
  exactly `rows`, `cols` and a level code. Colour goes over Generation C.
- Clock drift between the laptop's DAC and the phone's ADC is unmeasured.
  Roughly 260 ppm would walk half a symbol over an 86-second transmission, and
  Bluetooth or AirPlay resamplers can exceed that. `decode --drift-scan`
  searches ±300 ppm. If drift turns out to dominate, the real fix is a
  postamble for a two-point estimate — that changes the wire format, so it is a
  decision, not a tweak.
- `channel_sim._gsm_roundtrip_ffmpeg` leaks a temp directory per call, so a
  long parameter sweep fills `/var/folders`. Not fixed here; it is the
  teammate's file. `voip.audio_io` uses a `try`/`finally`-cleaned temp file
  instead.
- `runs/` is git-ignored by its own `.gitignore`. The repo's root `.gitignore`
  does **not** ignore `*.wav` or `*.png`, so curated results go to
  `spectral/tel/results/` by hand, the way that folder already works.
