"""
Parameters for the telephony / VoIP channel Gen A

"""

SAMPLE_RATE = 8000

# 0.1 s == 800 samples == exactly 5 GSM frames (20 ms each).

FRAME_DURATION = 0.1
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_DURATION)   # 800
BIN_WIDTH = SAMPLE_RATE / FRAME_SAMPLES             # 10.0 Hz

F_LOW = 700.0
F_HIGH = 3000.0

# Pilots sit outside the data band so they never collide with a row.
# Two of them, so the decoder can correct spectral tilt as well as flat gain.
PILOT_LOW = 500.0
PILOT_HIGH = 3200.0

# Pilot amplitude relative to a fully-black pixel (activation 1.0).
PILOT_AMPLITUDE = 1.0

ROWS = 24
COLUMNS = 24
GRAY_LEVELS = 4

# Tukey taper fraction. 0.25 means the outer 12.5% at each end fades,
# the middle 75% is flat. Hann is tukey(1.0) and
# drives the signal to zero at every frame edge, which invites VAD/DTX.
TUKEY_ALPHA = 0.25

# Preamble: this many frames of pilots-only before the data starts.
PREAMBLE_FRAMES = 3
