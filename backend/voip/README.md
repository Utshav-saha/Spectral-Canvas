# voip — sending a picture through a real phone call

The rest of Spectral Canvas sends a picture through a *simulated* call:
`spectral/tel/channel_sim.py` runs the modem audio through GSM 06.10 offline.
This package sends it through a real one.

```
  prepare            call                    (the phone)          decode
  ────────           ────                    ───────────          ──────
  picture            register with SIP       answer               align on the preamble
  → grid + levels    dial the phone          mute the mic         read the geometry
  → gen A or gen B   play tx.wav into it     press Record         demodulate
  → tx.wav           hang up                 send the file back   → picture + report
```

Nothing here reimplements a modem. `spectral/tel/call_track.py` is the ground
truth for both generations and is imported unchanged: Generation A through
`tel_encoder`/`tel_decoder`, Generation B through `image_fsk`/`fsk_codec`.

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
python -m voip.cli prepare --image cat.jpg --gen A --grid 24 --levels 4

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

| | airtime | what it is | over GSM |
|---|---|---|---|
| `--gen A --grid 24` | **2.7 s** | multitone, 2 pilot tones, amplitude carries the pixel | ~75% of pixels exact |
| `--gen A --grid 48 --colour` | ~18 s | the same, in colour | as above |
| `--gen B --grid 16` | 9.6 s | one tone per symbol, Hamming(7,4) | **100% exact** |
| `--gen B --grid 24` | 20.8 s | as above, more detail | 100% exact |

The two fail in opposite ways, and that is the whole point of keeping both.

**Generation B is exact.** The decoder takes an `argmax` over sixteen tones and
never compares magnitudes, so a codec that flattens loudness cannot reach it.
The cost is airtime: it caps out around 32×32 grayscale.

**Generation A is not, on purpose.** The pixel is in a tone's amplitude, which
is exactly what GSM 06.10's 8-pole LPC envelope throws away. It comes back
damaged by a margin that is graded and reproducible, which is what makes it a
training target rather than a failure. It is also about eight times cheaper per
pixel than Generation B, so it carries the resolution.

Start with Generation A: it is under three seconds, so a failed attempt costs
nothing, and it is the one the restoration work is about.

Text does **not** use `spectral/text/text_codec.py`. That is 44.1 kHz MFSK
between 2 and 5 kHz with no error correction, and a voice channel keeps roughly
300–3400 Hz, so most of its tones never arrive. `--text` renders the message
into the grid and sends it as a picture instead.

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

## The SDK route was abandoned

The liblinphone Python bindings are on no package index, for any platform, and
have to be compiled from the SDK source tree. `voip/call/session.py` was
written against the 5.5 reference and **has never run**.

It turned out not to be needed. Linphone is a SIP client and
`sip.linphone.org` is an ordinary SIP registrar, so the app on the phone
answers a call from any SIP client — including `pjsua`, which this repo already
depends on for the loopback rehearsal. `voip/dial.py` drives it:

```bash
brew install pjproject
export VOIP_SIP_IDENTITY=sip:you-laptop@sip.linphone.org
read -s VOIP_SIP_PASSWORD && export VOIP_SIP_PASSWORD
```

Then either `POST /api/tel/dial` from the Call page, or drive pjsua yourself.
SIP to SIP, so it never touches the phone network and costs nothing.

**What no route can do is bring the audio back.** pjsua records its own inbound
leg, which is your muted microphone — not the tones the phone received. The
recording has to be made on the phone with Linphone's in-call Record button and
then uploaded. No app can capture another app's call audio: Android blocks it
and iOS does not allow it at all.

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
- Generation B is grayscale only, up to 32×32: the 16-bit header has room for
  exactly `rows`, `cols` and a level code. Colour goes over Generation A, which
  has no wire header at all — its geometry lives in the run manifest, so
  `decode` needs `--run` pointed at the run that produced the audio.
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
