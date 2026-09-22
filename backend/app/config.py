import os

CORS_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
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

# The two tracks that ship. Track 3 (the parallel multitone scheme over a
# voice call) was cut - backend/spectral/tel/rejected/README.md says why.
# The frontend reads this from /api/health to build its track selector, so
# these are the only two a request can ask for.
TRACKS = {
    "wav": {
        "id": "wav",
        "number": 1,
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
        "label": "Phone call",
        "tagline": "Survives an 8 kHz voice codec",
        "summary": ("One tone at a time out of sixteen, so the pixel is in "
                    "which tone plays, never in how loud it is. A GSM call "
                    "destroys loudness but keeps pitch, so this gets through."),
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
