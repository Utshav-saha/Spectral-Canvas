"""Lazy access to the modules in ``spectral/tel/``.

Those modules use bare sibling imports (``import fsk_codec as fsk``), so their
directory has to be on ``sys.path`` before any of them will import. This is the
same shim that already sits at the top of ``app/services/tel_pipeline.py``.

Every accessor is a function rather than a module-level ``import`` so that
``voip.cli check-env`` can *report* a missing ``reedsolo`` instead of dying on
the import line -- reporting what is missing is the whole point of that command.
"""

import importlib
import sys

from voip.config import TEL_DIR, VoipDependencyError

_CACHE = {}


def ensure_path():
    """Put spectral/tel on sys.path. Idempotent."""
    if TEL_DIR not in sys.path:
        sys.path.insert(0, TEL_DIR)


def _load(name, missing_hint=None):
    if name in _CACHE:
        return _CACHE[name]
    ensure_path()
    try:
        module = importlib.import_module(name)
    except ImportError as exc:
        hint = f" {missing_hint}" if missing_hint else ""
        raise VoipDependencyError(
            f"Could not import '{name}' from {TEL_DIR}: {exc}.{hint}"
        ) from exc
    _CACHE[name] = module
    return module


def fsk():
    """The 16-FSK modem. numpy + scipy only, no other dependencies."""
    return _load("fsk_codec")


def image_fsk():
    """Generation B: raw quantized pixels <-> bits."""
    return _load("image_fsk")


def image_webp():
    """Generation C: WebP + Reed-Solomon + keyed byte shuffle."""
    return _load(
        "image_webp",
        "It needs the reedsolo package: pip install reedsolo",
    )


def channel_sim():
    """The offline GSM 06.10 call simulator."""
    return _load("channel_sim")


def available(name):
    """True if that tel module imports, without raising if it does not."""
    try:
        _load(name)
        return True
    except VoipDependencyError:
        return False
