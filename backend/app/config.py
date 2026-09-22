import os

CORS_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]

MAX_UPLOAD_BYTES = 12 * 1024 * 1024

# Call recordings are bigger than pictures. A two-minute Generation C
# transmission recorded at 48 kHz stereo is about 23 MB, so the 12 MB picture
# limit would reject a perfectly good recording.
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
#   Gen C  WebP + Reed-Solomon over the same 16-FSK modem    -> the Call page
#
# Track 3 was Gen A over a voice call, and was cut: a GSM codec throws away
# exactly the amplitudes it depends on. backend/spectral/tel/rejected/README.md
# has the measurement.
#
# Generation C is not offered here. It needs a staged upload so size and
# quality can be re-planned without re-uploading, and it reports RS block
# health rather than a pixel error, so it has its own endpoints under
# /api/tel and its own page. See app/api/tel_routes.py and voip/README.md.
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
            "note": ("This gives you the audio to play down a line yourself, "
                     "at 32 x 32. To place a real call, or to send a full-"
                     "colour picture up to 160 px over the same modem, use the "
                     "Call page - it compresses to WebP and adds Reed-Solomon "
                     "first."),
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

# A call runs in real time, so refuse one nobody would sit through. 5 minutes
# also keeps the 8 kHz int16 WAV under MAX_UPLOAD_BYTES, so the file the
# receiver uploads is always one this server would have produced.
CALL_MAX_SECONDS = 300
