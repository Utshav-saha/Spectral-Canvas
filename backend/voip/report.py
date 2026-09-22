"""Run folders and JSON that survives numpy.

One transmission = one run folder. The sender writes ``manifest.json`` and
``tx.wav`` into it; the call writes ``call.json``; the decoder drops the
recording in and writes ``report.json``. Keeping them together is what lets
``decode`` compare what came back against what went out.
"""

import datetime as _dt
import json
import os
import re

import numpy as np

from voip.config import MANIFEST_SCHEMA, RUNS_ROOT, VoipError

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text, fallback="run"):
    slug = _SLUG_RE.sub("-", str(text).lower()).strip("-")
    return slug[:40] or fallback


def new_run(slug="run", root=None):
    """Create runs/<YYYYmmdd-HHMMSS>-<slug>/ and return its path."""
    root = root or RUNS_ROOT
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(root, f"{stamp}-{slugify(slug)}")
    suffix = 1
    while os.path.exists(path):
        suffix += 1
        path = os.path.join(root, f"{stamp}-{slugify(slug)}-{suffix}")
    os.makedirs(path)
    return path


def latest_run(root=None):
    """The most recently created run folder, or None."""
    root = root or RUNS_ROOT
    if not os.path.isdir(root):
        return None
    runs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    return max(runs, key=os.path.getmtime) if runs else None


def resolve_run(arg, root=None):
    """Accept a path, a bare folder name, or the word 'latest'."""
    root = root or RUNS_ROOT
    if arg in (None, "", "latest"):
        run = latest_run(root)
        if run is None:
            raise VoipError(
                f"No run folders yet in {root}. Make one with: "
                f"python -m voip.cli prepare --image <file>"
            )
        return run
    if os.path.isdir(arg):
        return os.path.abspath(arg)
    candidate = os.path.join(root, arg)
    if os.path.isdir(candidate):
        return candidate
    raise VoipError(f"No run folder at '{arg}'.")


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------

def jsonable(obj):
    """Convert numpy scalars/arrays into things json.dump accepts.

    Not optional: waveform.global_stats and every np.mean in this package
    return np.float64, which json.dump refuses outright. Without this, writing
    a report is the most likely crash in the whole package.
    """
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return jsonable(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        # inf/nan are not valid JSON; PSNR is inf for a perfect match
        return None if (np.isnan(value) or np.isinf(value)) else value
    if isinstance(obj, float):
        return None if (np.isnan(obj) or np.isinf(obj)) else obj
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    if isinstance(obj, _dt.datetime):
        return obj.isoformat()
    if isinstance(obj, os.PathLike):
        return os.fspath(obj)
    return obj


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(jsonable(data), handle, indent=2, sort_keys=False)
        handle.write("\n")
    return path


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_manifest(run_dir, manifest):
    return write_json(os.path.join(run_dir, "manifest.json"), manifest)


def write_report(run_dir, report):
    return write_json(os.path.join(run_dir, "report.json"), report)


def write_call(run_dir, call):
    return write_json(os.path.join(run_dir, "call.json"), call)


def load_manifest(path_or_run_dir):
    """Accept either a manifest.json or the run folder holding one."""
    path = path_or_run_dir
    if os.path.isdir(path):
        path = os.path.join(path, "manifest.json")
    if not os.path.isfile(path):
        raise VoipError(
            f"No manifest.json at {path}. Without it the decoder still works, "
            f"it just cannot score the result against what was sent."
        )
    manifest = read_json(path)
    schema = manifest.get("schema")
    if schema != MANIFEST_SCHEMA:
        raise VoipError(
            f"{path} says schema '{schema}', expected '{MANIFEST_SCHEMA}'."
        )
    return manifest


def utc_now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

_VERDICT_LABEL = {
    "ok": "PASS",
    "rs-failed": "FAIL",
    "wrong-pin": "FAIL",
    "truncated": "FAIL",
    "bad-header": "FAIL",
    "no-sync": "FAIL",
}


def summary_line(report):
    """One line a person can read at a glance."""
    verdict = report.get("verdict", "?")
    summary = report.get("summary", {})
    label = _VERDICT_LABEL.get(verdict, verdict.upper())

    score = summary.get("preamble_score")
    offset = summary.get("offset_s")
    weak = summary.get("weak_symbols")
    bits = summary.get("payload_bits")

    parts = [f"{label} ({verdict})"]
    if score is not None:
        parts.append(f"preamble {score:.2f}")
    if offset is not None:
        parts.append(f"start {offset:.2f}s")
    if bits is not None:
        parts.append(f"{bits} bits")
    if weak is not None:
        parts.append(f"{weak} weak")
    scored = report.get("quality") or {}
    if scored.get("identical"):
        # psnr_db is None here because a perfect match has infinite PSNR, not
        # because nothing was measured -- say so rather than printing nothing
        parts.append("pixel-identical")
    elif scored.get("psnr_db") is not None:
        parts.append(f"PSNR {scored['psnr_db']:.1f} dB")
    elif scored.get("text_identical"):
        parts.append("text exact")
    return "  ".join(parts)
