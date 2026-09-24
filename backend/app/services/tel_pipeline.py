"""Glue between the HTTP layer and the call path in spectral/tel/.

Two generations go out over a voice line, both through `spectral/tel/
call_track.py`:

    A   parallel multitone with pilot tones. Amplitude carries the pixel, so a
        speech codec damages it - about 75% of pixels exact through simulated
        GSM. Fast on the wire (one frame per column), so it carries the bigger
        picture. The damage is the input the restoration model is trained on.
    B   16-FSK, one tone per symbol. Which tone carries the pixel, so a codec
        cannot touch it - 100% exact - at about a tenth of the resolution.

Generation C (WebP + Reed-Solomon) was cut: a byte-exact file transfer
has no graded loss to measure or learn from. It is in the git history.

Nothing here reimplements a modem. The tel modules use bare sibling imports
(`import fsk_codec as fsk`), so their directory goes on sys.path first.
"""

import io
import os
import shutil
import sys
from math import gcd

import numpy as np
from PIL import Image
from scipy.io import wavfile
from scipy.signal import resample_poly

_TEL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "spectral", "tel")
if _TEL not in sys.path:
    sys.path.insert(0, _TEL)

import channel_sim               # noqa: E402

from spectral.tel import call_track                    # noqa: E402
from spectral.analysis import waveform as wf           # noqa: E402
from spectral.decoder.image_reconstructor import to_image_array  # noqa: E402
from app.services.pipeline import validate_credentials  # noqa: E402

SAMPLE_RATE = call_track.SAMPLE_RATE

# What the Call page offers. Gen A spends one frame per column whatever the
# content, so it buys resolution cheaply; Gen B is serial and does not.
# A call runs in real time, so Generation A refuses one nobody would sit
# through. Generation B has no limit: it is the exact one, and the only reason
# to choose it is when correctness matters more than the wait - capping it
# would be refusing the whole point of it. The page warns past this many
# seconds instead of refusing.
MAX_SECONDS = 300
LONG_SECONDS = 300

GENERATIONS = {
    "A": {
        "id": "A",
        "label": "Generation A",
        "tagline": "Amplitude carries the pixel",
        "summary": ("The same parallel multitone scheme the WAV track uses, "
                    "narrowed to 700-3000 Hz with two pilot tones. A voice "
                    "codec models each frame with eight poles and cannot hold "
                    "that many tone levels, so the picture arrives damaged - "
                    "about three quarters of pixels exact. It is fast on the "
                    "wire, so it carries the most detail."),
        "sizes": [16, 24, 32, 48, 64],
        "default_size": 48,
        "levels": [2, 4, 8, 16],
        "default_levels": 16,
        "lossy": True,
        # a call in real time; a lossy picture is not worth sitting through
        "max_seconds": MAX_SECONDS,
    },
    "B": {
        "id": "B",
        "label": "Generation B",
        "tagline": "Which tone carries the pixel",
        "summary": ("One tone at a time out of sixteen. The decoder takes an "
                    "argmax and never compares loudness, so a codec that "
                    "destroys amplitude cannot touch it - the picture arrives "
                    "exact. The cost is airtime: 32 x 32 at 4 levels is about "
                    "36 s, and 64 x 64 is four times that, so the bigger "
                    "grids are only worth it when exactness matters more than "
                    "the wait."),
        "sizes": [16, 24, 32, 48, 64],
        "default_size": 32,
        "levels": [2, 4, 16],
        "default_levels": 4,
        "lossy": False,
        # no cap: exactness is the reason to pick this one
        "max_seconds": None,
    },
}



def info():
    return {
        "gsm_available": gsm_available(),
        "sample_rate": SAMPLE_RATE,
        "generations": list(GENERATIONS.values()),
        "default_generation": "A",
        "max_seconds": MAX_SECONDS,
    }


def gsm_available():
    return bool(shutil.which("toast") or channel_sim._ffmpeg_has_libgsm())


def _check(generation, size, levels, colour):
    if generation not in GENERATIONS:
        raise ValueError("Generation must be A or B.")
    spec = GENERATIONS[generation]
    if size not in spec["sizes"]:
        raise ValueError(f"Generation {generation} carries "
                         f"{', '.join(str(s) for s in spec['sizes'])} pixels square.")
    if levels not in spec["levels"]:
        raise ValueError(f"Generation {generation} carries "
                         f"{', '.join(str(n) for n in spec['levels'])} gray levels.")
    mode = "RGB" if colour else "L"
    seconds = call_track.budget_seconds(size, size, levels, mode, generation)

    limit = spec.get("max_seconds")
    if limit is not None and seconds > limit:
        raise ValueError(
            f"That would take {seconds / 60:.1f} minutes of call time, and the "
            f"limit for Generation {generation} is {limit // 60} minutes. Use a "
            f"smaller grid, fewer gray levels, or send it in grayscale. "
            f"Generation B has no limit, if you want to wait it out."
        )
    return mode, seconds


def open_image(upload_bytes):
    if not upload_bytes:
        raise ValueError("Choose an image file to send.")
    try:
        image = Image.open(io.BytesIO(upload_bytes))
        image.load()
    except Exception:
        raise ValueError("That file is not a picture we can read. Try a PNG or JPG.")
    return image


def plan(generation, size, levels, colour):
    """Quote the airtime without doing the encode, so the page can show it
    while the settings are still being chosen."""
    mode, seconds = _check(generation, size, levels, colour)
    spec = GENERATIONS[generation]
    channels = 3 if colour else 1
    return {
        "generation": generation,
        "rows": size, "columns": size,
        "gray_levels": levels,
        "mode": mode,
        "channels": channels,
        "seconds": round(seconds, 2),
        "lossy": spec["lossy"],
        "pixels": size * size * channels,
        "max_seconds": spec.get("max_seconds"),
        # no cap on B, but past five minutes the page should say so plainly
        "long": bool(seconds > LONG_SECONDS),
    }


def _png(image_array):
    image = Image.fromarray(image_array,
                            mode="RGB" if image_array.ndim == 3 else "L")
    scale = max(1, 320 // max(1, image.width))
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale),
                             Image.Resampling.NEAREST)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def to_wav_bytes(audio, sample_rate=SAMPLE_RATE):
    buf = io.BytesIO()
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    wavfile.write(buf, sample_rate, pcm)
    return buf.getvalue()


def run_send(image_bytes, generation, size, levels, colour, locked=False,
             caller=None, receiver=None, pin=None, autocontrast=True):
    mode, _ = _check(generation, size, levels, colour)
    if locked:
        validate_credentials(caller, receiver, pin)

    audio, metadata, activation = call_track.encode(
        open_image(image_bytes), target_width=size, target_height=size,
        gray_levels=levels, mode=mode, generation=generation,
        security_enabled=locked, caller=caller, receiver=receiver, pin=pin,
        autocontrast=autocontrast,
    )

    sent_array = to_image_array(activation, levels)
    return {
        "audio": audio,
        "metadata": metadata,
        "wav_bytes": to_wav_bytes(audio),
        "sent_png": _png(sent_array),
        "sent_array": sent_array,
        "report": {
            "generation": generation,
            "rows": metadata["rows"], "columns": metadata["columns"],
            "gray_levels": levels, "mode": mode,
            "channels": metadata["channels"],
            "seconds": metadata["duration_seconds"],
            "scheme": metadata["scheme"],
            "band": metadata["band"],
            "lossy": GENERATIONS[generation]["lossy"],
            "autocontrast": bool(autocontrast),
            "locked": bool(locked),
        },
        "stats": wf.global_stats(audio, SAMPLE_RATE),
    }


def run_call(audio, loss, noise_db, seed):
    if not 0.0 <= loss <= 0.3:
        raise ValueError("Packet loss must be between 0% and 30%.")
    if not -80.0 <= noise_db <= -10.0:
        raise ValueError("Noise floor must be between -80 and -10 dB.")
    if not gsm_available():
        raise ValueError("No GSM 06.10 codec is installed on the server, so the "
                         "call cannot be simulated. Install libgsm, or an ffmpeg "
                         "built with it.")
    try:
        received = channel_sim.channel(audio, sample_rate=SAMPLE_RATE, loss_rate=loss,
                                       noise_db=noise_db, seed=int(seed))
    except RuntimeError as exc:
        raise ValueError(str(exc))
    return {
        "audio": received,
        "wav_bytes": to_wav_bytes(received),
        "stats": wf.global_stats(received, SAMPLE_RATE),
    }


def read_any_wav(raw_bytes):
    """Any PCM or float WAV -> mono float64 at 8 kHz, plus what it was before."""
    try:
        rate, data = wavfile.read(io.BytesIO(raw_bytes))
    except Exception:
        raise ValueError("That does not look like a WAV file.")

    channels = 1 if data.ndim == 1 else data.shape[1]
    if data.dtype == np.int16:
        audio = data / 32768.0
    elif data.dtype == np.int32:
        audio = data / 2147483648.0
    elif data.dtype == np.uint8:
        audio = (data.astype(np.float64) - 128.0) / 128.0
    else:
        audio = data.astype(np.float64)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float64)
    if len(audio) == 0:
        raise ValueError("That WAV file has no audio in it.")

    if rate != SAMPLE_RATE:
        g = gcd(int(rate), SAMPLE_RATE)
        audio = resample_poly(audio, SAMPLE_RATE // g, int(rate) // g)

    return audio, {"sample_rate_in": int(rate), "channels": channels,
                   "resampled": rate != SAMPLE_RATE}


def read_any_audio(raw_bytes, filename=None):
    """Any recording -> mono float64 at 8 kHz.

    WAV still goes through read_any_wav, byte for byte as before. Anything else
    goes through ffmpeg, because Linphone's in-call recorder writes Matroska
    (.mka) and an iOS share can arrive as .m4a or .caf -- none of which scipy
    will open, and all of which are what a real call actually produces.

    Imported inside the function so the app still starts if voip/ is absent.
    """
    if not raw_bytes:
        raise ValueError("Choose an audio file to inspect.")

    try:
        return read_any_wav(raw_bytes)
    except ValueError:
        pass

    try:
        from voip.audio_io import load_audio_bytes
    except ImportError:
        raise ValueError(
            "That does not look like a WAV file, and the converter for other "
            "formats is unavailable on this server."
        )

    try:
        audio, meta = load_audio_bytes(raw_bytes, filename)
    except Exception as exc:
        raise ValueError(str(exc))

    return audio, {"sample_rate_in": int(meta.get("sample_rate_in", SAMPLE_RATE)),
                   "channels": int(meta.get("channels", 1)),
                   "resampled": bool(meta.get("resampled", False)),
                   "container": meta.get("container"),
                   "codec": meta.get("codec")}


def locate(audio, metadata):
    """Where the transmission starts in a recording, and how sure we are.

    Neither generation can be read out of a bare recording on its own: Gen A's
    grid geometry and Gen B's symbol count live in the send's metadata, which
    a recording of a real call obviously does not carry. So the page keeps the
    send session and hands its metadata back here. What this adds is the
    offset, because a phone recording starts whenever Record was pressed.
    """
    from voip import sync as voip_sync
    import fsk_codec as fsk

    if metadata.get("generation") == "A":
        gen_meta = metadata["tel"]
        frames = metadata["columns"] * metadata["channels"] + gen_meta["preamble_frames"]
        announce = int(gen_meta.get("announce_symbols") or 0)
        if not announce:
            # from before the announcement: only the pilots to go on
            import tel_decoder
            start = int(tel_decoder.pilot_align(audio, gen_meta))
            end = start + frames * gen_meta["frame_samples"]
            return {"offset": start, "score": None, "truncated": bool(end > len(audio))}
        need = announce * fsk.SYMBOL_SAMPLES + frames * gen_meta["frame_samples"]
        located = voip_sync.find_preamble(audio, need_samples=need)
        end = located.offset + need
        return {"offset": int(located.offset), "score": round(float(located.score), 4),
                "found": bool(located.found), "truncated": bool(end > len(audio))}

    info = metadata["fsk"]
    # wire_symbols counts the re-sync markers; n_symbols does not, so a
    # marker-carrying transmission is longer on the wire than its payload
    frames = len(fsk.PREAMBLE) + int(info.get("wire_symbols") or info["n_symbols"])
    if info.get("has_header"):
        frames += int(info.get("header_symbols", fsk.HEADER_SYMBOLS))
    # How much audio the transmission needs, worked out before the search so
    # the search can prefer a preamble with room behind it: a recording can
    # hold the preamble twice over (voip.sync._best_with_room explains why),
    # and picking the later copy rebuilds a blank picture.
    need = frames * fsk.SYMBOL_SAMPLES
    located = voip_sync.find_preamble(audio, need_samples=need)
    end = located.offset + need
    return {"offset": int(located.offset), "score": round(float(located.score), 4),
            "found": bool(located.found), "truncated": bool(end > len(audio))}


# Whether the tones survived the call, measured against the transmission's own
# error-correcting code rather than against how loud anything was.
#
# The measurement that forced this. A real 48x48 call over GSM that rebuilt
# **100% of pixels exactly**, beside two phone recordings that did not:
#
#                              bin imbalance   median margin   symbol errors
#   clean, no channel                50x            341             0.00%
#   real GSM call, perfect           45x             50             0.00%
#   hello.mkv, damaged               45x             56            10.81%
#   me_rec.mkv, destroyed            47x             59            11.61%
#
# The margin does not separate them - the *perfect* recording scores lower than
# both damaged ones - so the old rule (imbalance high AND margin low) fired on
# every good GSM call there is. It was calibrated when the project dialled with
# PCMU, which is nearly transparent; GSM 06.10 models each 20 ms frame with an
# 8-pole LPC envelope, and that squashes the runner-up tone whether or not
# anything is wrong. Swapping the codec quietly invalidated the metric.
#
# Nor is the margin sensitive to the damage that matters: tilting the band by
# 0 to 80 dB moved it only 157 -> 145 while the symbol error went 0 -> 3.5%.
#
# So the warning now rests on `symbol_error`, which asks the recording a
# question with a knowable answer: decode it, re-encode what came out, and
# count the symbols that disagree. Hamming(7,4) and the interleaver repair most
# of them, which is why the threshold is where it is - calibrated on the same
# call, spectral tilt as the damage:
#
#   symbol errors   0.00%   0.25%   1.19%   2.06%   3.50%   10.81%
#   pixels exact     100%  99.96%  99.52%  98.91%  96.92%     ~87%
#
# Imbalance and margin are still reported, because they say something about
# *how* a recording is damaged. They just no longer decide anything.
#
# Neither does clipping, which used to raise an alarm of its own. Two
# measurements retired it. GSM overshoots on its own - this modem hands the
# codec a signal peaking at 0.70 and gets one pinned at 1.0 back, 4.6% of
# samples above 0.99 on a *perfect* call - so the 2% threshold fired on every
# GSM recording there is, and told people to turn the phone's volume down when
# the codec was doing it. And clipping does not cost this modem anything
# anyway: driven to 2x, 10x, 500x full scale - 95% of samples clipped, near
# enough a square wave - the symbol error stays 0.00%, because 16-FSK decides
# by which tone bin is largest and squaring a sine leaves its fundamental on
# top. `clipped_fraction` is still reported as a fact about the recording;
# it is no longer evidence of anything.
SATURATED_FRACTION = 0.02
SYMBOL_ERROR_WARN = 0.02
BIN_IMBALANCE = 25.0
WEAK_MEDIAN_MARGIN = 100.0

# kept under its old name: other callers and tests read it
CLIPPED_FRACTION = SATURATED_FRACTION


def symbol_error(audio, metadata, offset=0):
    """Fraction of symbols the channel corrupted, with no original to compare to.

    The transmission carries Hamming(7,4) over an interleaver, so the payload
    can be decoded and then re-encoded, and the symbols that come back out are
    the symbols that should have arrived. Everything the code had to repair
    shows up as a disagreement. No reference picture, no send session and no
    knowledge of the channel is needed - only the recording and what it says it
    is, which is the situation the Receive tab is actually in.

    None when it cannot be measured: Generation A, a short recording, or
    anything unexpected, in which case the caller falls back on the other
    numbers rather than inventing one.
    """
    if metadata.get("generation") != "B":
        return None
    try:
        import fsk_codec as fsk

        info = dict(metadata.get("fsk") or {})
        n_symbols = int(info.get("n_symbols") or 0)
        if n_symbols < 64:
            return None

        head = (int(info.get("header_symbols", fsk.HEADER_SYMBOLS))
                if info.get("has_header") else 0)
        start = int(offset) + (len(fsk.PREAMBLE) + head) * fsk.SYMBOL_SAMPLES
        if start >= len(audio):
            return None

        interval = int(info.get("resync_interval") or 0)
        if interval:
            # read exactly the way demodulate does, re-anchoring on each marker,
            # so the comparison is against the symbols the decoder really used
            chunks, pos, left = [], start, n_symbols
            while left > 0:
                pos = fsk.find_marker(audio, pos)
                count = min(interval, left)
                chunks.append(np.argmax(
                    fsk._symbol_magnitudes(audio, pos, count), axis=1))
                pos += count * fsk.SYMBOL_SAMPLES
                left -= count
            got = np.concatenate(chunks)
        else:
            got = np.argmax(fsk._symbol_magnitudes(audio, start, n_symbols), axis=1)

        bits = fsk.demodulate(audio, info, int(offset))
        coded, _ = fsk.hamming_encode(np.asarray(bits, dtype=np.uint8))
        coded, _ = fsk.interleave(coded)
        expected, _ = fsk._to_symbols(coded)

        count = min(len(got), len(expected))
        if count < 64:
            return None
        return float(np.mean(got[:count] != expected[:count]))
    except Exception:
        return None


def signal_health(audio, metadata, offset=0):
    """Whether the recording can carry a picture at all.

    Sync succeeding says only that the preamble was found; it says nothing
    about whether the tones survived. Without this the page rebuilds a
    confident-looking wrong picture and the obvious suspect is the PIN.
    """
    clipped = float(np.mean(np.abs(audio) > 0.99))
    report = {"clipped_fraction": clipped,
              "saturated": bool(clipped > SATURATED_FRACTION),
              "bin_imbalance": None, "median_margin": None,
              "dominant_tone_share": None, "symbol_error": None,
              "warning": None}

    if metadata.get("generation") == "B":
        try:
            import fsk_codec as fsk
            from voip.dsp import symbol_decisions, symbol_magnitudes

            info = metadata.get("fsk") or {}
            head = (int(info.get("header_symbols", fsk.HEADER_SYMBOLS))
                    if info.get("has_header") else 0)
            start = int(offset) + (len(fsk.PREAMBLE) + head) * fsk.SYMBOL_SAMPLES
            count = min(int((metadata.get("fsk") or {}).get("n_symbols") or 0),
                        max(0, (len(audio) - start) // fsk.SYMBOL_SAMPLES))
            if count > 32:
                mags = symbol_magnitudes(audio, start, count)
                per_bin = mags.mean(axis=0)
                report["bin_imbalance"] = float(
                    per_bin.max() / max(float(np.median(per_bin)), 1e-12))
                symbols, margin = symbol_decisions(mags)
                report["median_margin"] = float(np.median(margin))
                report["dominant_tone_share"] = float(
                    np.bincount(symbols, minlength=16).max() / count)
        except Exception:
            pass

    report["symbol_error"] = symbol_error(audio, metadata, offset)

    errors = report["symbol_error"]

    if errors is not None and errors > SYMBOL_ERROR_WARN:
        report["warning"] = (
            f"About {errors * 100:.0f} symbols in every 100 arrived as the wrong "
            "tone, and the error-correcting code can only repair a fraction of "
            "that, so some of this picture will be read wrong however well it "
            "syncs and whatever the PIN. Check that echo cancellation and noise "
            "suppression really are off on the phone and that GSM is enabled in "
            "its codec list, and keep the handset off speakerphone - a "
            "microphone that can hear the earpiece puts the room on top of the "
            "tones."
        )
    return report


def identify(audio):
    """The settings a recording carries in its own descriptor, or None.

    What lets the Receive tab rebuild a recording made on someone else's
    machine: every Call-page transmission now says what it is (see
    spectral/tel/descriptor.py). None for one from before that, a damaged
    descriptor, or no transmission at all - those still need their send.
    """
    from voip import sync as voip_sync
    try:
        located = voip_sync.find_preamble(audio)
        if not located.found:
            return None
        return call_track.identify(audio, located.offset)
    except Exception:
        return None


def _wire_samples(metadata):
    """How many samples the whole transmission occupies, preamble included."""
    import fsk_codec as fsk

    if metadata.get("generation") == "A":
        gen_meta = metadata.get("tel") or {}
        frames = (metadata["columns"] * metadata["channels"]
                  + gen_meta.get("preamble_frames", 0))
        announce = int(gen_meta.get("announce_symbols") or 0)
        return (announce * fsk.SYMBOL_SAMPLES
                + frames * gen_meta.get("frame_samples", 0))

    info = metadata.get("fsk") or {}
    frames = len(fsk.PREAMBLE) + int(info.get("wire_symbols") or info.get("n_symbols") or 0)
    if info.get("has_header"):
        frames += int(info.get("header_symbols", fsk.HEADER_SYMBOLS))
    return frames * fsk.SYMBOL_SAMPLES


def run_inspect(audio, metadata=None, self_described=False):
    stats = wf.global_stats(audio, SAMPLE_RATE)
    if metadata is None:
        return {
            "found": False,
            "self_described": False,
            "message": ("Loaded, but this recording does not say what it is: "
                        "either it was sent before transmissions carried their "
                        "own settings, or its opening was damaged. Encode the "
                        "same picture on the Send tab with the same settings, "
                        "then come back and it will be read against that."),
            "stats": stats,
        }

    located = locate(audio, metadata)
    health = signal_health(audio, metadata, located["offset"])
    if located.get("found") is False:
        message = ("No transmission preamble was found in this audio. Either it "
                   "is not a recording of this call, or Record was started too "
                   "late.")
    elif located["truncated"]:
        # Short at this offset, and the audio cannot say which end is missing:
        # Record pressed after the tones began looks exactly like Record
        # stopped before they ended. Naming only the second sends people off to
        # re-record the same way again.
        short = (located["offset"] + _wire_samples(metadata)) - len(audio)
        message = (f"Found, but the recording is about {short / SAMPLE_RATE:.0f}s "
                   "shorter than the transmission, so part of the picture is "
                   "missing. Either Record was pressed after the tones had "
                   "started, or it was stopped before they finished - start it "
                   "before you answer and leave it running until the call hangs "
                   "up on its own.")
        if not self_described:
            message += (" If the recording is complete, check it is being read "
                        "against the send it really came from: a different "
                        "grid, level count or colour setting changes the "
                        "length expected.")
    else:
        message = "Found. Rebuild it whenever you are ready."

    # A degraded recording syncs perfectly well; say so before it is rebuilt
    # into a confident-looking wrong picture.
    if health["warning"]:
        message = f"{message} {health['warning']}"

    return {
        "found": located.get("found", True),
        # read off the recording itself rather than taken from a send
        "self_described": bool(self_described),
        "rows": metadata.get("rows"), "columns": metadata.get("columns"),
        "gray_levels": metadata.get("gray_levels"),
        "mode": metadata.get("mode", "L"),
        # whether the *transmission* was locked, straight from the send it was
        # matched against. A recording carries no such flag of its own, and the
        # page used to read a key no endpoint has ever returned, so every
        # recording came up "Locked".
        "locked": bool(metadata.get("security_enabled", False)),
        "clipped_fraction": round(health["clipped_fraction"], 4),
        # a fact about the recording, not a verdict on it: GSM saturates the
        # signal by itself and this modem decodes a square wave perfectly well
        "saturated": health["saturated"],
        "dominant_tone_share": (None if health["dominant_tone_share"] is None
                                else round(health["dominant_tone_share"], 3)),
        "bin_imbalance": (None if health["bin_imbalance"] is None
                          else round(health["bin_imbalance"], 1)),
        "median_margin": (None if health["median_margin"] is None
                          else round(health["median_margin"], 1)),
        # the fraction of symbols the channel corrupted, measured against the
        # transmission's own error-correcting code: the one number here that
        # does not depend on the picture or the codec
        "symbol_error": (None if health["symbol_error"] is None
                         else round(health["symbol_error"], 4)),
        "signal_warning": health["warning"],
        "generation": metadata.get("generation"),
        "offset_seconds": round(located["offset"] / SAMPLE_RATE, 3),
        "preamble_score": located["score"],
        "truncated": located["truncated"],
        "message": message,
        "stats": stats,
    }


def _retimed(trimmed, metadata):
    """Put a real recording back on a uniform symbol grid before decoding.

    A phone does not keep the laptop's timebase - measured at 87 ms of wander
    over a 60 s call, more than two symbols - and the modem locks its grid once
    from the preamble. Generation A is left alone: its pilot tones realign it
    per frame already.

    Never worse than not trying: anything unexpected returns the audio
    untouched and the decoder does exactly what it did before.
    """
    if metadata.get("generation") == "A":
        return trimmed
    info = metadata.get("fsk") or {}
    if not info.get("n_symbols"):
        return trimmed
    # A transmission carrying re-sync markers retimes itself, absolutely, at
    # every marker. Stretching it onto one uniform grid first can only fight
    # that: measured on the real 860 s call, retiming scored *worse* than
    # leaving it alone (0.883 vs 0.852 syndrome rate) because its 8-symbol
    # margin search is noise once the margin is ~10 rather than ~200.
    if info.get("resync_interval"):
        return trimmed
    try:
        import fsk_codec as fsk
        from voip import sync as voip_sync

        total = len(fsk.PREAMBLE) + int(info["n_symbols"])
        if info.get("has_header"):
            total += int(info.get("header_symbols", fsk.HEADER_SYMBOLS))
        return voip_sync.retime(trimmed, 0, total)
    except Exception:
        return trimmed


def run_receive(audio, metadata, locked=False, caller=None, receiver=None,
                pin=None, sent_array=None):
    if metadata is None:
        raise ValueError("Choose which transmission this audio is a recording of.")
    if locked:
        validate_credentials(caller, receiver, pin)

    located = locate(audio, metadata)
    trimmed = audio[located["offset"]:] if located["offset"] else audio
    trimmed = _retimed(trimmed, metadata)

    try:
        image_array = call_track.decode(
            trimmed, metadata, caller=caller, receiver=receiver, pin=pin,
            decrypt_enabled=bool(locked))
    except Exception as exc:
        raise ValueError(f"The audio could not be read as a transmission: {exc}")

    result = {
        "ok": True,
        "generation": metadata.get("generation"),
        "locked": bool(locked),
        "rows": metadata["rows"], "columns": metadata["columns"],
        "mode": metadata.get("mode", "L"),
        "offset_seconds": round(located["offset"] / SAMPLE_RATE, 3),
        "truncated": located["truncated"],
        "png": _png(image_array),
        # kept so the model can be run on it later, at its real size: _png
        # blows it up to ~320 px for display, which is not what to feed a model
        "array": image_array,
    }

    # Against what was actually put on the wire, so the number is the channel's
    # doing and not the quantiser's.
    if sent_array is not None and np.shape(sent_array) == image_array.shape:
        got = image_array.astype(float)
        ref = np.asarray(sent_array, dtype=float)
        mse = float(np.mean((got - ref) ** 2))
        exact = float(np.mean(got == ref))
        result["match"] = {
            "identical": bool(mse == 0),
            "mae": round(float(np.mean(np.abs(got - ref))), 3),
            "psnr": None if mse == 0 else round(10 * np.log10(255.0 ** 2 / mse), 2),
            "exact_fraction": round(exact, 4),
        }
    return result


def model_status():
    """Whether the learned upscaler can run here, and why not if it cannot."""
    from spectral.restore import upscaler
    return upscaler.status()


def run_enhance(image_array):
    """Track 2's missing detail, guessed back by the model.

    Generation B arrives bit-exact, so nothing here is repairing transmission
    damage. What it undoes is the shrinking and the 4-level quantising done
    *before* the call, which are the two things a voice line has no airtime
    for. See spectral/restore/upscaler.py.
    """
    from spectral.restore import upscaler

    if not upscaler.available():
        raise ValueError(upscaler.status()["message"])

    array = np.asarray(image_array)
    if array.size == 0:
        raise ValueError("There is no rebuilt picture to enhance yet.")

    enhanced = upscaler.enhance(array)
    return {
        "png": _png(enhanced),
        "size": int(enhanced.shape[0]),
        "from_size": int(array.shape[0]),
        "compare": upscaler.compare(array, enhanced),
    }


def waveform_payload(audio, buckets):
    return {
        "envelope": wf.peak_envelope(audio, buckets),
        "buckets": wf.bucket_stats(audio, SAMPLE_RATE, buckets),
        "stats": wf.global_stats(audio, SAMPLE_RATE),
    }
