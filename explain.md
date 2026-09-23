# Placing a real call, physically

Your accounts, as set up:

| where | Linphone username | SIP address |
|---|---|---|
| Mac (the backend dials as this) | `ahnafjamil` | `sip:ahnafjamil@sip.linphone.org` |
| Phone (this is what rings) | `ahnafjamil2` | `sip:ahnafjamil2@sip.linphone.org` |

---

## Why your call did not work

Three separate faults, all now fixed. None of them was your account or your steps.

### 1. Your network blocks SIP's own ports (this is the one that stopped you today)

Measured from your Mac:

| | |
|---|---|
| TCP 5060 (SIP) | **blocked** |
| TCP 5061 (SIP over TLS) | **blocked** |
| UDP 5060 (SIP) | sent, **no reply ever comes back** |
| TCP 443 (HTTPS) | **open** |

The dialler was registering over plain UDP 5060. The REGISTER went out and nothing
came back, and pjsua reports that silence as `503 Service Unavailable` after about
eighteen seconds — which reads like "the server is down" when it is really "your
network dropped it". That is the `never registered` error you saw, and because
registration never completed, no call was ever placed. Your phone had nothing to ring
for.

Linphone publishes a TLS endpoint **on port 443** for exactly this situation (its DNS
advertises `_sips._tcp … 443`). Port 443 is open almost everywhere, because it is what
HTTPS uses. Proven from your machine, with a deliberately wrong password:

```
SIP TLS transport is connected to sip.linphone.org:443
SIP/2.0 401 Unauthorized        <- the server challenges us
SIP/2.0 403 Forbidden           <- ...and rejects the wrong password
```

That is the server talking. With the right password it answers `200 OK`.

**This is also why your phone works and the Mac did not:** the Linphone app uses TLS on
443 by default. The backend was the only thing still trying port 5060.

The dialler now uses `sip:sip.linphone.org:443;transport=tls` by default, creates the
TLS transport (`--use-tls`), and routes the call itself through the same road
(`--outbound=…`) so the invitation cannot fall back to the blocked port. Registration
now fails or succeeds in well under a second instead of hanging for 25.

### 2. The phone never presented the incoming call

Registration and the invitation both worked — the call reached your account, which
is why it appeared in the phone's call history. What did not happen is the handset
*alerting* you.

This is a known limitation of Linphone on a mobile, not a bug in this project. An
incoming SIP call only rings a phone if the app is in the foreground with a live
registration, or if a push notification gets through to wake it. On the free service
push is unreliable, and the call lands in the history having never rung. **Nothing on
this machine can fix that** — the ringing decision belongs entirely to the phone.

So the direction is now reversible. **The phone calls the Mac instead.** A handset
placing an outgoing call needs no push notification and always works. The Mac answers
automatically and plays the transmission into the call. It is the same account, the
same registrar, the same codec and the same recording — only who dials changes.

On the Call page there is now a second button next to **Call and play**:

> **Or let the phone call this Mac**

Press it, and the page tells you which address to dial from the phone
(`sip:ahnafjamil@sip.linphone.org`). Verified end to end: a real SIP call placed *into*
this machine came back **pixel-identical**.

The failure messages for the outgoing direction are also much sharper now. Instead of
one "nobody answered" covering three unrelated problems, the dialler reads the SIP
responses and tells them apart: whether the phone was ever alerted at all (180 Ringing),
whether it was reached and refused the audio (488, a codec/encryption mismatch, not a
ringing problem), whether it was busy (486), declined (603), or registered nowhere
(404/480).

### 3. pjsua was being killed about a second after it started

The code wrote one newline to pjsua's standard input and then closed it — and pjsua
quits the moment its stdin closes. Measured on your machine: **it survived 1.19
seconds.** Even on a network that allowed SIP, that is not long enough to register, let
alone play 20 seconds of audio. The browser showed an elapsed time because the page
counts seconds whether or not anything is on the line.

### 4. It relied on `--auto-play`, which pjsua applies to incoming calls only

pjsua's own help says *"(to incoming calls only)"*. So even with the first two fixed, an
*outgoing* call would have carried silence: your `tx.wav` sat in pjsua's audio mixer
connected to nothing. An outgoing call needs the player wired to the call's own port by
hand, which the dialler now does.

### 5. It fought Linphone Desktop over port 5060

pjsua binds SIP port 5060 by default — and so does every other SIP application,
including Linphone Desktop on the same Mac. Whichever starts second silently fails to
open its transport. The dialler now picks a free port at runtime, so it no longer
matters what else is running.

Verified end to end after all of these: real SIP calls in **both** directions came back
**pixel-identical**.

## Before you dial: four things to check

**1. Quit Linphone Desktop on the Mac.** It is signed in as `ahnafjamil`, the same
account the backend registers as, and two clients on one account make it ambiguous which
one a call belongs to. (The port clash that used to cause is fixed — the backend now
picks its own free port — but the duplicate registration is still worth avoiding.) The
backend does not need the desktop app at all; it speaks SIP itself.

**2. The phone's Linphone app must be OPEN and in the foreground.** This is the most
common reason a SIP call never arrives. A locked phone with the app closed only rings if
push notifications are working, which is not reliable on a free account. Unlock the
phone, open Linphone, leave it on screen.

**3. Set the phone's audio settings.** In the phone's Linphone app:
- **Codecs** → enable **PCMU** only (and PCMA if you like). Disable Opus.
- **Echo cancellation → OFF**
- **Noise suppression → OFF**

Both are designed to remove steady tones, which is exactly what your transmission is.
Leave them on and you will get a recording that decodes to nothing.

**4. Export the credentials in the same terminal that runs the backend, then start it
there.** Environment variables do not travel between terminal tabs.

```bash
cd ~/Documents/University/Uni_projects/Spectral_Canvas/Spectral-Canvas/backend
source .venv/bin/activate
export VOIP_SIP_IDENTITY=sip:ahnafjamil@sip.linphone.org
read -s VOIP_SIP_PASSWORD && export VOIP_SIP_PASSWORD   # type it, press Enter, it stays hidden
uvicorn app.main:app --reload --port 8000
```

Confirm it took, in another tab:

```bash
curl -s http://127.0.0.1:8000/api/tel/dial/status
```

You want `"pjsua": true` and `"configured": true`, and `registrar` should read
`sip:sip.linphone.org:443;transport=tls` — port 443, not 5060. If `configured` is false, the
variables did not reach the server process — you exported them in a different tab, or
started uvicorn before exporting.

---

## The call, step by step

**Start smaller than you did.** Your 24×24 at 4 levels was 20.5 s. For the first
successful call use **Generation B, grid 16, 4 levels** — about 9.6 s, so a failed
attempt costs you ten seconds instead of half a minute. Scale up once one works.

1. Open <http://localhost:5173/call>, **Send** tab.
2. Drop a picture. Set **Generation B**, **Grid 16**, **Gray levels 4**, colour off.
3. Press **Encode for a call**. Wait for `tx.wav` to appear.
4. Pick up the phone. Open Linphone. Leave it on screen.
5. In **Place a real call**, the tag should read **Ready**. Type:
   `sip:ahnafjamil2@sip.linphone.org`
6. Press **Call and play**. Now watch the state line on the page:

   | state | what is happening | what you do |
   |---|---|---|
   | `registering` | logging in to sip.linphone.org | nothing |
   | `ringing` | the phone should be ringing | **answer it** |
   | `answered` | you have ~8 seconds | **mute the mic, then press Record** |
   | `playing` | the tones are going out | leave both devices alone |
   | `done` | finished | **stop the recording** on the phone |

   The page now tells you explicitly when to press Record — that gap did not exist before.

   **If the phone does not ring** — which is what happened to you, and is a phone-side
   limitation nothing here can change — press **Or let the phone call this Mac**
   instead. The page then shows `waiting`, and you dial
   `sip:ahnafjamil@sip.linphone.org` from the phone's Linphone app. The Mac answers by
   itself, you get the same ~8 second gap to **mute the mic and press Record**, and the
   rest is identical. This is the direction to prefer: a phone placing a call never
   depends on push notifications.

7. Share the recording from the phone to the Mac (AirDrop is easiest). Linphone saves it
   as a **`.mka`** file.
8. Back in the browser, switch to the **Receive** tab — **on the Call page**, *same
   browser tab, do not reload*.

   > ⚠️ **Not the "Receive" item in the top nav.** That is a different page, for WAVs
   > this app wrote itself. A call recording is sound captured off a voice line, so it
   > has no Spectral Canvas header and never can — that page will tell you
   > "no Spectral Canvas header" and there is nothing wrong with your recording. Its
   > grid and generation live in the Call page's Send tab session, which is why it has
   > to be rebuilt there. (That page now says so and points you here.)

9. Drop the recording. **Linphone on a phone writes Matroska — `.mkv` or `.mka`** — and
   both are accepted directly. Do not convert to WAV first; ffmpeg handles it, and a
   conversion is one more place to lose the signal. It will report where the
   transmission starts and how strong the preamble is. Press **Rebuild picture**.

---

## If it still fails

The page now gives you a real reason instead of a silent `done`. The three you might see:

- **"The registrar rejected the account: the password is wrong"** — retype it with
  `read -s VOIP_SIP_PASSWORD` and restart the backend. A trailing space counts.
- **"No reply from sip:sip.linphone.org:443… almost always the network"** — 443 is
  blocked too, which is unusual. Try a phone hotspot. You can also point the backend
  somewhere else with `export VOIP_SIP_REGISTRAR=...`.
- **"was alerted — the phone's SIP stack answered 180 Ringing"** — the call reached the
  handset and it really did try to ring. Silent mode, or you missed it.
- **"never even sent 180 Ringing, so the call was not presented on the handset"** — the
  push notification did not get through. **Use the answer mode.**
- **"refused the audio (488 Not Acceptable Here)"** — a media mismatch, not a ringing
  problem. Set Media encryption to None in the phone's Linphone settings and enable PCMU.
- **"is busy (486)" / "declined (603)" / "registered nowhere (404/480)"** — say exactly
  what they say.

When the call completes, check the `Codec` line in the readout. If it says PCMU that is
fine and expected here. If you ever see Opus, the phone's codec list was not restricted
and the result proves less than it looks like — Opus is nearly transparent.

### Prove the machinery without touching the phone

If you want to isolate whether the problem is your phone, your network or the code, run
the loopback. It never leaves your Mac, so it works even on a network that blocks SIP.
It places a genuine SIP call between two local pjsua instances — real signalling, real
RTP, real codec — with no account and no handset:

```bash
cd backend && source .venv/bin/activate
python -m voip.cli prepare --image yourpicture.png --gen B --grid 16 --levels 4
python -m voip.cli simulate --run latest --pjsua --codec GSM --decode
```

If that decodes and a real call does not, the fault is on the phone side — codecs, echo
cancellation, or the Record button.

---

## The automated test that now covers this

`backend/tests/test_voip_dial_live.py` places real SIP calls through the actual dialler —
in both directions, including the answer mode —
with a second pjsua standing in for your phone: it answers and records, exactly as the
phone does. It then decodes the picture out of that recording.

```bash
cd backend && source .venv/bin/activate
python -m pytest tests/test_voip_dial_live.py -q      # ~70 s, needs pjsua
```

It is written to fail on both original bugs, not just to pass on the fix. Run against the
old code it reports:

```
the call lasted 4.6s but the transmission is 12.6s long - pjsua hung up before it finished
verdict no-sync
```

The first line catches the closed-stdin bug, the second catches `--auto-play`. Neither
was visible to any test that only inspected the command line, which is why both reached a
real handset.

Whole suite: **224 passed, 22 skipped**.

Three unit tests also now pin the network fix specifically: that the registrar defaults
to TLS on 443, that the TLS transport is actually created, and that the call is routed
through the same proxy rather than being left to find the blocked port by itself.
