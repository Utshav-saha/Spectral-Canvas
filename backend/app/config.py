import os

CORS_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]

MAX_UPLOAD_BYTES = 12 * 1024 * 1024

# Call recordings are bigger than pictures. A phone records the whole call, not
# just the transmission, and does it at its own rate in its own container, so
# the 12 MB picture limit would reject a perfectly good recording.
MAX_AUDIO_UPLOAD_BYTES = 32 * 1024 * 1024

MAX_TEXT_CHARS = 2000
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 200

PHONE_DIGITS = 11
PIN_MIN, PIN_MAX = 4, 8

DEFAULTS = {
    "target_width": 64, "target_height": 64,
    "sample_rate": 44100, "f_min": 1000, "f_max": 8000,
    "frame_duration": 0.05, "gray_levels": 16,
    "mode": "L", "alpha": 0.15,
}

SIZE_LIMIT = 160
PREVIEW_SCALE = 8

# The two tracks that ship. A *track* is a delivery path; a *generation* is the
# encoding it carries, and the two are separate axes:
#
#   Gen A  parallel multitone, pixel in a tone's amplitude   -> Track 1
#   Gen B  16-FSK raw pixels, pixel in which tone plays      -> Track 2
#
# The Call page offers Gen A and Gen B over a real voice line. Generation C
# (WebP + Reed-Solomon) was cut.
#
# Gen A over a voice call is lossy - a GSM codec throws away exactly the
# amplitudes it depends on - and that is now the point rather than a reason to
# cut it: the damage is what the restoration model is trained to undo. It
# lives on the Call page, with its own endpoints under /api/tel, because it
# needs a staged upload and a different set of controls.
#
# The frontend reads this from /api/health to build its track selector, so
# these are the only two a request can ask for.
TRACKS = {
    "wav": {
        "id": "wav",
        "number": 1,
        "generation": "A",
        "label": "Direct WAV",
        "tagline": "A clean file, no codec in the way",
        "summary": ("Every row is a tone and the pixel sets how loud it is. "
                    "Highest detail, but it only survives a channel that keeps "
                    "amplitudes intact."),
        "sample_rate": 44100,
        "band": [1000, 8000],
        "sizes": [32, 48, 64, 96, 128],
        "default_size": 64,
        "gray_levels": 16,
        "supports_colour": True,
        "supports_lock": True,
        "supports_text": True,
    },
    "call": {
        "id": "call",
        "number": 2,
        "generation": "B",
        "label": "Phone call",
        "tagline": "Survives an 8 kHz voice codec",
        "summary": ("One tone at a time out of sixteen, so the pixel is in "
                    "which tone plays, never in how loud it is. A GSM call "
                    "destroys loudness but keeps pitch, so this gets through."),
        # Where to go for the same channel at higher resolution, and for
        # placing an actual call rather than downloading the audio.
        "more": {
            "href": "/call",
            "label": "Call page",
            "note": ("This gives you the audio to play down a line "
                     "yourself. To place an actual call to a phone, or to "
                     "send the same picture the lossy way and see what a "
                     "voice codec does to it, use the Call page."),
        },
        "sample_rate": 8000,
        "band": [700, 3200],
        "sizes": [16, 24, 32],
        "default_size": 32,
        "gray_levels": 4,
        "supports_colour": True,
        "supports_lock": True,
        "supports_text": False,
    },
}

DEFAULT_TRACK = "wav"

# Not a limit any more: the call track is Generation B, the exact one, and the
# only reason to choose it is when correctness matters more than the wait.
# Past this many seconds the encode response sets `long` so the page can say
# so - it no longer refuses. Worth knowing: 5 minutes is also where the 8 kHz
# int16 WAV passes MAX_UPLOAD_BYTES, so a longer file cannot be uploaded back
# to this server even though it can be produced.
CALL_MAX_SECONDS = 300
