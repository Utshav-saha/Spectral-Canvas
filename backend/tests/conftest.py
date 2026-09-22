"""Shared settings for the backend test suite.

`test_roundtrip.py` is deliberately not collected: it is a `__main__` script
that prints a table, not a pytest module, so collecting it would find no tests.
Run it directly instead, from `backend/`:

    python -m tests.test_roundtrip
"""

import pytest

collect_ignore = ["test_roundtrip.py"]


def _gsm_available():
    try:
        from app.services.tel_pipeline import gsm_available
        return gsm_available()
    except Exception:
        return False


needs_gsm = pytest.mark.skipif(
    not _gsm_available(), reason="no GSM 06.10 codec (brew install libgsm)")
