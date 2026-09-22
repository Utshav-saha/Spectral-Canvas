"""Track 2: an image over an 8 kHz voice call, as 16-FSK.

The modules in here were written to be run directly from this directory
(`python3 demo.py`), so they import each other by bare name: `import
fsk_codec`. Putting this directory on sys.path keeps that working while also
letting the app say `from spectral.tel import call_track`. The same trick is
used in spectral/decoder/image_reconstructor.py.

Only fsk_codec, image_fsk, channel_sim and call_track ship. The parallel
multitone attempt is quarantined in rejected/ - see rejected/README.md.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
