"""Talking to liblinphone without trusting it to have the names we expect.

The attribute names below come from the liblinphone 5.5 Python reference, and
they drift between releases. None of them could be checked while this was
written -- the SDK is not installable in the sandbox and is not on PyPI for
Apple silicon at all -- so every single access goes through ``try_set`` or
``try_get``. A renamed attribute then produces a line in ``call.json``'s
warnings instead of an exception thirty seconds into a phone call.

``probe()`` exists for the same reason: run ``voip.cli check-env`` on the real
Mac *before* attempting a call and it prints exactly which of these names the
installed build actually has.
"""

import importlib.util

from voip.config import VoipDependencyError

INSTALL_HINT = (
    "The liblinphone Python wrapper is not installed. It is not on PyPI for "
    "macOS on Apple silicon, so it has to be built from source:\n"
    "  brew install cmake ninja yasm nasm doxygen pkg-config\n"
    "  git clone --recursive https://gitlab.linphone.org/BC/public/linphone-sdk.git\n"
    "  cd linphone-sdk && git checkout <5.4-or-newer-tag>\n"
    "  cmake --preset=default -B build-python -DENABLE_PYTHON_WRAPPER=ON -DENABLE_VIDEO=OFF\n"
    "  cmake --build build-python --target install\n"
    "  cmake --build build-python --target wheel && pip install <the .whl>\n"
    "Give it a day at most. If it will not build, the fallback route needs no "
    "SDK and reaches the same channel:\n"
    "  brew install blackhole-2ch && python -m voip.cli call --run latest --fallback"
)

# What session.py expects to find. Checked, never assumed.
EXPECTED_ATTRIBUTES = [
    "Factory", "Core", "CallState", "RegistrationState",
]
EXPECTED_CORE_ATTRIBUTES = [
    "echo_cancellation_enabled", "agc_enabled", "noise_suppression_enabled",
    "use_files", "play_file", "video_capture_enabled", "video_display_enabled",
    "audio_payload_types", "create_nat_policy", "nat_policy", "start", "stop",
    "iterate", "create_account_params", "create_address", "add_auth_info",
    "create_account", "add_account", "default_account", "create_call_params",
    "invite_address_with_params", "add_listener",
]


def available():
    """True if `import linphone` would work. Never raises, never imports."""
    try:
        return importlib.util.find_spec("linphone") is not None
    except (ImportError, ValueError):
        return False


def load():
    """Import the SDK, or explain how to get it."""
    try:
        import linphone
    except ImportError as exc:
        raise VoipDependencyError(INSTALL_HINT) from exc
    return linphone


def version():
    if not available():
        return None
    try:
        return str(load().Core.get_version())
    except Exception:
        return "unknown"


def probe():
    """Which expected names this build actually has. {} when no SDK.

    Run this first on the real machine. It is much cheaper to find a renamed
    attribute here than halfway through a call.
    """
    if not available():
        return {}
    try:
        linphone = load()
    except VoipDependencyError:
        return {}

    found = {name: hasattr(linphone, name) for name in EXPECTED_ATTRIBUTES}

    # Core attributes need an instance; creating one is cheap and side-effect free.
    try:
        core = linphone.Factory.get().create_core("", "", None)
        for name in EXPECTED_CORE_ATTRIBUTES:
            found[f"core.{name}"] = hasattr(core, name)
    except Exception as exc:
        found["core.<instantiation>"] = False
        found["_error"] = str(exc)
    return found


# --------------------------------------------------------------------------
# Safe accessors
# --------------------------------------------------------------------------

def try_set(obj, name, value, warnings=None):
    """Set an attribute if it exists; otherwise record why not and carry on."""
    warnings = warnings if warnings is not None else []
    if not hasattr(obj, name):
        warnings.append(f"liblinphone has no '{name}'; skipped (wanted {value!r})")
        return False
    try:
        setattr(obj, name, value)
        return True
    except Exception as exc:
        warnings.append(f"setting '{name}' to {value!r} failed: {exc}")
        return False


def try_get(obj, name, default=None, warnings=None):
    if not hasattr(obj, name):
        if warnings is not None:
            warnings.append(f"liblinphone has no '{name}'; using {default!r}")
        return default
    try:
        return getattr(obj, name)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"reading '{name}' failed: {exc}")
        return default


def try_call(obj, name, *args, warnings=None, default=None):
    """Call a method if it exists. Tolerates SDKs that expose it as a property."""
    if not hasattr(obj, name):
        if warnings is not None:
            warnings.append(f"liblinphone has no '{name}()'; skipped")
        return default
    try:
        attribute = getattr(obj, name)
        return attribute(*args) if callable(attribute) else attribute
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"calling '{name}()' failed: {exc}")
        return default


def state_name(value):
    """'CallStateStreamsRunning' -> 'StreamsRunning', for readable logs."""
    text = str(value)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    for prefix in ("CallState", "RegistrationState"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text
