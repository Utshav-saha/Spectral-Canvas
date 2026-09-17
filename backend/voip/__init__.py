"""Real VoIP transport for Spectral Canvas: call, record, decode.

The rest of the project sends a picture through a *simulated* phone call
(``spectral/tel/channel_sim.py`` runs the modem audio through GSM 06.10
offline). This package sends it through an *actual* one: the laptop dials a
phone with Linphone, plays the 16-FSK modem audio into the call, the phone
records it, and the recording comes back here to be decoded.

Nothing here reimplements the modem. ``spectral/tel/fsk_codec.py`` is the
ground truth and is imported unchanged; this package supplies the parts a real
recording needs and an in-memory array does not:

    sync.py     a whole-file preamble search that reports a score
                (fsk_codec.find_preamble only looks at the first 3 seconds and
                throws its score away)
    dsp.py      per-symbol confidence, so a report can say how marginal a
                decode was rather than only what it decoded
    framing.py  a self-describing header for the raw-pixel path, and a bounds
                check so a recording stopped early is reported as truncated
                instead of silently decoding fabricated bytes
    audio_io.py Linphone records Matroska (.mka), not WAV

Import layout: this is a top-level package alongside ``app`` and ``spectral``,
so every command runs from ``backend/`` -- ``python -m voip.cli ...``.

``import voip`` must stay cheap and must never require numpy, the Linphone SDK
or FastAPI, because ``voip.cli check-env`` exists precisely to report on a
machine where those are missing.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
