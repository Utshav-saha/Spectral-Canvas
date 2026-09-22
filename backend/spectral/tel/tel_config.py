"""
Parameters for the telephony / VoIP channel.

Everything here is chosen to survive an 8 kHz speech codec (GSM 06.10 over
SIP, AMR-NB over cellular). Compare with the desktop values in your
audio_encoder.py, which assume a transparent 44.1 kHz channel.

    desktop            telephony          why
    ---------------------------------------------------------------
    44100 Hz           8000 Hz            the call IS 8 kHz, no choice
    1000..8000 Hz      700..3000 Hz       usable band is ~300..3400 Hz
    64 rows            24 rows            need >= ~90 Hz row spacing
    16 gray levels     4 gray levels      amplitude accuracy is poor
    hann window        tukey(0.25)        avoid periodic near-silence
    no pilots          2 pilot tones      kill AGC / gain uncertainty
"""

SAMPLE_RATE = 8000

# 0.1 s == 800 samples == exactly 5 GSM frames (20 ms each).
# Keeping frame_duration a whole multiple of 20 ms means a symbol never
# straddles a codec frame boundary in a ragged way.
FRAME_DURATION = 0.1
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_DURATION)   # 800
BIN_WIDTH = SAMPLE_RATE / FRAME_SAMPLES             # 10.0 Hz

# Data rows live strictly inside the band, with margin at both ends.
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
# the middle 75% is flat. Hann (your current window) is tukey(1.0) and
# drives the signal to zero at every frame edge, which invites VAD/DTX.
TUKEY_ALPHA = 0.25

# Preamble: this many frames of pilots-only before the data starts.
PREAMBLE_FRAMES = 3
