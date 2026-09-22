"""Reports that actually serialise, and say the same things every time.

json.dump refuses np.float64, and nearly every number in this package is one.
A report that cannot be written is a call that has to be made again, so this
gets its own tests.
"""

import json
import math
import os

import numpy as np
import pytest

from voip import decode, encode, report, simulate
from voip.config import MANIFEST_SCHEMA, REPORT_SCHEMA, VoipError

# The four names the troubleshooting table reads at a glance. Renaming any of
# them silently breaks the workflow this package is built around.
SUMMARY_KEYS = {"preamble_score", "offset_s", "payload_bits", "weak_symbols"}


@pytest.mark.parametrize("value,expected", [
    (np.float64(1.5), 1.5),
    (np.float32(2.0), 2.0),
    (np.int64(7), 7),
    (np.int32(7), 7),
    (np.bool_(True), True),
    (np.array([1, 2, 3]), [1, 2, 3]),
    (np.float64("inf"), None),          # a perfect match has infinite PSNR
    (np.float64("nan"), None),
    (float("inf"), None),
    (b"bytes", "bytes"),
])
def test_numpy_scalars_become_json(value, expected):
    assert report.jsonable(value) == expected


def test_nesting_is_converted_all_the_way_down():
    converted = report.jsonable(
        {"a": [np.float64(1.0), {"b": np.int64(2)}], "c": (np.bool_(False),)})
    json.dumps(converted)
    assert converted == {"a": [1.0, {"b": 2}], "c": [False]}


def test_a_real_report_serialises(tmp_path, synthetic_image):
    """The end-to-end guard: every value produced by a real decode must survive."""
    prepared = encode.prepare(source=synthetic_image, generation="C", size=64)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=20.0,
                                    gsm=False, seed=1)
    result = decode.decode(received)

    path = report.write_report(str(tmp_path), result.report)
    reloaded = json.loads(open(path).read())
    assert reloaded["schema"] == REPORT_SCHEMA
    assert _no_nan_or_inf(reloaded)


def _no_nan_or_inf(obj):
    if isinstance(obj, dict):
        return all(_no_nan_or_inf(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_no_nan_or_inf(v) for v in obj)
    if isinstance(obj, float):
        return not (math.isnan(obj) or math.isinf(obj))
    return True


def test_the_summary_block_keeps_its_four_names(synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="B", grid=16)
    received, _ = simulate.simulate(prepared.audio, lead_seconds=15.0,
                                    gsm=False, seed=1)
    assert set(decode.decode(received).report["summary"]) == SUMMARY_KEYS


def test_the_summary_block_survives_a_failed_decode():
    """A failure is exactly when someone reads these fields."""
    result = decode.decode(np.zeros(20 * 8000))
    assert set(result.report["summary"]) == SUMMARY_KEYS
    assert result.report["verdict"] == "no-sync"


def test_a_manifest_round_trips(tmp_path, synthetic_image):
    prepared = encode.prepare(source=synthetic_image, generation="C", size=64)
    run_dir = str(tmp_path / "r")
    encode.write_run(prepared, run_dir)

    manifest = report.load_manifest(run_dir)
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["generation"] == "C"
    assert manifest["wire"]["sample_rate"] == 8000
    assert manifest["run_id"] == "r"


def test_a_missing_manifest_is_explained(tmp_path):
    with pytest.raises(VoipError, match="No manifest.json"):
        report.load_manifest(str(tmp_path))


def test_a_foreign_schema_is_refused(tmp_path):
    report.write_json(str(tmp_path / "manifest.json"), {"schema": "something/else@9"})
    with pytest.raises(VoipError, match="expected"):
        report.load_manifest(str(tmp_path))


def test_run_folders_are_unique_and_sortable(tmp_path):
    first = report.new_run("demo", root=str(tmp_path))
    second = report.new_run("demo", root=str(tmp_path))
    assert first != second
    assert report.latest_run(str(tmp_path)) in (first, second)


def test_resolve_run_accepts_a_name_a_path_or_latest(tmp_path):
    created = report.new_run("demo", root=str(tmp_path))
    name = os.path.basename(created)
    assert report.resolve_run(created, root=str(tmp_path)) == created
    assert report.resolve_run(name, root=str(tmp_path)) == created
    assert report.resolve_run("latest", root=str(tmp_path)) == created


def test_resolve_run_explains_an_empty_runs_folder(tmp_path):
    with pytest.raises(VoipError, match="prepare"):
        report.resolve_run("latest", root=str(tmp_path / "empty"))


def test_summary_line_says_identical_rather_than_nothing():
    """A perfect match has no finite PSNR; the line must still report it."""
    line = report.summary_line({
        "verdict": "ok",
        "summary": {"preamble_score": 0.97, "offset_s": 30.0,
                    "payload_bits": 512, "weak_symbols": 0},
        "quality": {"identical": True, "psnr_db": None},
    })
    assert "PASS" in line and "pixel-identical" in line


def test_summary_line_survives_a_failure_with_no_numbers():
    line = report.summary_line({"verdict": "no-sync", "summary": {
        "preamble_score": None, "offset_s": None,
        "payload_bits": None, "weak_symbols": None}})
    assert "FAIL" in line
