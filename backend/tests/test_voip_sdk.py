"""The SDK guard rails, tested without the SDK.

Every liblinphone attribute name in call/session.py came from the 5.5 reference
and none of it could be checked while it was written -- the wrapper is not on
PyPI for Apple silicon. So the contract these tests hold to is not "the names
are right" but "a wrong name is survivable": it must produce a warning in
call.json, never an exception thirty seconds into a phone call.

That is testable with a plain object, which is what happens below.
"""

import pytest

from voip.call import sdk
from voip.config import VoipDependencyError

from conftest import needs_sdk


class Fake:
    """Stands in for a Core: has some of the attributes, not all."""

    def __init__(self):
        self.agc_enabled = True
        self.mime_type = "PCMU"
        self._enabled = False

    def enable(self, value):
        self._enabled = value
        return value

    def enabled(self):
        return self._enabled

    def explodes(self):
        raise RuntimeError("boom")


class Stubborn:
    @property
    def readonly(self):
        return 1


# --------------------------------------------------------------------------
# Presence checks never raise
# --------------------------------------------------------------------------

def test_available_is_safe_without_the_sdk():
    assert isinstance(sdk.available(), bool)


def test_version_is_none_without_the_sdk():
    if not sdk.available():
        assert sdk.version() is None


def test_probe_returns_a_dict_either_way():
    assert isinstance(sdk.probe(), dict)


def test_loading_without_the_sdk_explains_both_routes():
    if sdk.available():
        pytest.skip("the SDK is installed here")
    with pytest.raises(VoipDependencyError) as caught:
        sdk.load()
    message = str(caught.value)
    assert "linphone-sdk" in message
    assert "--fallback" in message, "the no-SDK route has to be offered"


# --------------------------------------------------------------------------
# try_set / try_get / try_call
# --------------------------------------------------------------------------

def test_setting_a_present_attribute_works():
    obj, warnings = Fake(), []
    assert sdk.try_set(obj, "agc_enabled", False, warnings) is True
    assert obj.agc_enabled is False
    assert warnings == []


def test_setting_a_missing_attribute_warns_instead_of_raising():
    """The whole point: a renamed attribute must not end the call."""
    obj, warnings = Fake(), []
    assert sdk.try_set(obj, "noise_suppression_enabled", False, warnings) is False
    assert len(warnings) == 1
    assert "noise_suppression_enabled" in warnings[0]


def test_a_setter_that_raises_is_caught():
    obj, warnings = Stubborn(), []
    assert sdk.try_set(obj, "readonly", 2, warnings) is False
    assert warnings and "readonly" in warnings[0]


def test_reading_a_missing_attribute_returns_the_default():
    warnings = []
    assert sdk.try_get(Fake(), "nope", "fallback", warnings) == "fallback"
    assert warnings


def test_calling_a_missing_method_returns_the_default():
    warnings = []
    assert sdk.try_call(Fake(), "nope", warnings=warnings, default=[]) == []
    assert warnings


def test_calling_a_method_that_raises_is_caught():
    warnings = []
    assert sdk.try_call(Fake(), "explodes", warnings=warnings, default="safe") == "safe"
    assert warnings


def test_try_call_tolerates_a_property_where_a_method_was_expected():
    """Some builds expose enabled() as a plain attribute. Both must work."""
    obj = Fake()
    obj.enable(True)
    assert sdk.try_call(obj, "enabled", default=False) is True

    class AsProperty:
        enabled = True
    assert sdk.try_call(AsProperty(), "enabled", default=False) is True


# --------------------------------------------------------------------------
# Log readability
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("CallStateStreamsRunning", "StreamsRunning"),
    ("RegistrationStateOk", "Ok"),
    ("CallState.CallStateEnd", "End"),
    ("Released", "Released"),
])
def test_state_names_are_shortened_for_logs(raw, expected):
    assert sdk.state_name(raw) == expected


def test_expected_attribute_lists_cover_what_session_uses():
    """If session.py starts touching something new, list it here too."""
    import inspect

    from voip.call import session

    source = inspect.getsource(session)
    for name in ("echo_cancellation_enabled", "agc_enabled", "use_files",
                 "audio_payload_types", "nat_policy"):
        assert name in source
        assert name in sdk.EXPECTED_CORE_ATTRIBUTES


# --------------------------------------------------------------------------
# With a real SDK (skipped everywhere it is not installed)
# --------------------------------------------------------------------------

@needs_sdk
def test_probe_reports_on_the_installed_build():
    found = sdk.probe()
    assert found
    missing = [name for name, ok in found.items() if not ok and not name.startswith("_")]
    assert not missing, f"this build lacks: {missing}"
