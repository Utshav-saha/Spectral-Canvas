"""Constants, thresholds and exceptions for the VoIP transport.

Anything that describes the *wire* (sample rate, symbol length, tone table)
belongs to ``spectral/tel/fsk_codec.py`` and is read from there, never copied.
What lives here is what this package decides for itself: search thresholds,
defaults, file layout and error types.
"""

import os

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(PACKAGE_ROOT)
TEL_DIR = os.path.join(BACKEND_ROOT, "spectral", "tel")
RUNS_ROOT = os.path.join(PACKAGE_ROOT, "runs")


# --------------------------------------------------------------------------
# Wire (mirrors of fsk_codec, for use before numpy is importable)
# --------------------------------------------------------------------------
# voip.cli check-env has to print these on a machine with no numpy installed,
# which is exactly the machine where `import fsk_codec` fails. Everywhere else
# read them from the modem via voip._tel.fsk().

SAMPLE_RATE = 8000
SYMBOL_SAMPLES = 320             # 40 ms
PREAMBLE_SYMBOLS = 8
HEADER_SYMBOLS = 7
HEADER_BITS = 16


# --------------------------------------------------------------------------
# Generation B header (see framing.py)
# --------------------------------------------------------------------------

# Every Generation B transmission sets the top 4 bits. Generation C used the
# values below this marker as a raw byte count; it was cut, so a header that
# does not carry the marker is simply not a transmission we sent.
GEN_B_MARKER = 0xF
GEN_B_MAX_SIDE = 32              # 5 bits each for rows-1 and cols-1
LEVEL_CODES = {2: 0, 4: 1, 16: 2, 256: 3}
CODE_LEVELS = {v: k for k, v in LEVEL_CODES.items()}


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------

SYNC_STRIDE = SYMBOL_SAMPLES // 4        # 80 samples = 10 ms coarse grid
SYNC_SCORE_THRESHOLD = 0.35              # measured: 0.997 on signal, 0.109 on noise
SYNC_REFINE_BELOW = 0.70                 # only pay for decision-directed refinement
                                         # when the preamble score is marginal
DRIFT_PPM_GRID = (-300.0, -150.0, 0.0, 150.0, 300.0)


# --------------------------------------------------------------------------
# Symbol confidence
# --------------------------------------------------------------------------
# margin = top1 / top2 FFT magnitude within one symbol. A clean symbol runs in
# the tens; 1.0 means the two best tones were indistinguishable.

WEAK_MARGIN_THRESHOLD = 1.6


# --------------------------------------------------------------------------
# Transmission defaults
# --------------------------------------------------------------------------

# Generation A is the default: it is the one the codec damages, which is what
# the restoration model is trained on, and it is cheap enough on the wire to
# retry. Generation B is the exact-but-small fallback.
DEFAULT_GENERATION = "A"
DEFAULT_GRID = 24                # square side
DEFAULT_LEVELS = 4               # gray levels

# Digital silence around the transmission. The phone's Record button and the
# SIP audio path both need a moment to settle; two seconds costs nothing and
# stops a clipped preamble from wasting a whole call.
DEFAULT_LEAD_IN_S = 2.0
DEFAULT_LEAD_OUT_S = 1.0

# Past this, warn and suggest a smaller --size. Not a hard limit.
LONG_TRANSMISSION_WARN_S = 120.0


# --------------------------------------------------------------------------
# Payload kinds
# --------------------------------------------------------------------------

TEXT_MAGIC = b"SCTX"             # prefix that marks an RS payload as UTF-8 text
WEBP_RIFF = b"RIFF"
WEBP_TAG = b"WEBP"


# --------------------------------------------------------------------------
# Audio containers
# --------------------------------------------------------------------------
# Linphone records Matroska (.mka). Everything but .wav goes through ffmpeg.

AUDIO_EXTENSIONS = {
    ".wav", ".wave",
    ".mka", ".mkv", ".m4a", ".mp4", ".caf", ".aac",
    ".opus", ".ogg", ".oga", ".mp3", ".flac", ".amr", ".3gp",
}
NATIVE_EXTENSIONS = {".wav", ".wave"}


# --------------------------------------------------------------------------
# Environment variables (credentials never reach the command line)
# --------------------------------------------------------------------------

ENV_SIP_IDENTITY = "VOIP_SIP_IDENTITY"
ENV_SIP_PASSWORD = "VOIP_SIP_PASSWORD"
ENV_SIP_DOMAIN = "VOIP_SIP_DOMAIN"
ENV_SIP_DIAL = "VOIP_SIP_DIAL"

DEFAULT_SIP_DOMAIN = "sip.linphone.org"
DEFAULT_STUN_SERVER = "stun.linphone.org"
DEFAULT_CODEC = "PCMU"


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

MANIFEST_SCHEMA = "spectral-canvas/voip-manifest@1"
REPORT_SCHEMA = "spectral-canvas/voip-report@1"
CALL_SCHEMA = "spectral-canvas/voip-call@1"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class VoipError(Exception):
    """Base for everything this package raises deliberately.

    Messages are shown to end users (the CLI prints them, and tel_routes
    returns them verbatim as an HTTP `detail`), so write them for a person.
    """


class VoipDependencyError(VoipError):
    """A required external tool or module is missing. Message says how to get it."""


class SyncError(VoipError):
    """No preamble found, so there is nothing to decode."""


class FrameError(VoipError):
    """A preamble was found but the header that follows is not usable."""


class CallError(VoipError):
    """The Linphone call could not be placed, or ended before the audio played."""
