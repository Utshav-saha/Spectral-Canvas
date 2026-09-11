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
