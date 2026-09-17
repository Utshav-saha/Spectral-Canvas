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
