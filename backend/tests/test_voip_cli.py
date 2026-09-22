"""The commands, run the way a person runs them.

Subprocess rather than calling main() directly, because exit codes and the
`python -m voip.cli` import path are part of what is being tested.
"""

import json
import os
import subprocess
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*args, expect=None):
    proc = subprocess.run([sys.executable, "-m", "voip.cli", *args],
                          cwd=BACKEND, capture_output=True, text=True, timeout=600)
    if expect is not None:
        assert proc.returncode == expect, (
            f"exit {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


# --------------------------------------------------------------------------
# check-env
# --------------------------------------------------------------------------

def test_check_env_reports_without_crashing():
    proc = run("check-env")
    assert "decode path" in proc.stdout
    assert "linphone SDK" in proc.stdout


def test_check_env_json_is_machine_readable():
    proc = run("check-env", "--json", expect=0)
    report = json.loads(proc.stdout)
    assert report["packages"]["numpy"]
    assert "linphone" in report
    assert isinstance(report["linphone"]["available"], bool)


def test_check_env_never_raises_over_a_missing_sdk():
    """This command exists to report on broken machines, so it cannot break."""
    proc = run("check-env")
    assert "Traceback" not in proc.stderr


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------

def test_prepare_dry_run_makes_no_audio(synthetic_image, tmp_path):
    proc = run("prepare", "--image", synthetic_image, "--gen", "A",
               "--grid", "24", "--dry-run", "--json", expect=0)
    plan = json.loads(proc.stdout)
    assert plan["generation"] == "A"
    assert plan["airtime_seconds"] > 0
    # the fixture image lives in tmp_path too, so check for the output instead
    assert not list(tmp_path.glob("**/tx.wav"))
    assert not list(tmp_path.glob("**/manifest.json"))


def test_prepare_writes_a_run_folder(synthetic_image, tmp_path):
    out = str(tmp_path / "run")
    run("prepare", "--image", synthetic_image, "--gen", "B", "--grid", "16",
        "--out", out, expect=0)

    for name in ("tx.wav", "sent.png", "manifest.json"):
        assert os.path.isfile(os.path.join(out, name)), name

    manifest = json.load(open(os.path.join(out, "manifest.json")))
    assert manifest["generation"] == "B"
    assert manifest["payload"]["payload_bits"] == 512


def test_prepare_text_writes_the_message(tmp_path):
    out = str(tmp_path / "run")
    run("prepare", "--text", "hello call", "--out", out, expect=0)
    assert open(os.path.join(out, "sent.txt")).read() == "hello call"


def test_prepare_needs_a_source():
    assert run("prepare").returncode == 2


def test_prepare_refuses_an_oversized_genb_grid(synthetic_image):
    proc = run("prepare", "--image", synthetic_image, "--gen", "B", "--grid", "64",
               "--dry-run")
    assert proc.returncode == 1
    assert "Generation B carries up to" in proc.stderr


# --------------------------------------------------------------------------
# simulate + decode
# --------------------------------------------------------------------------

def test_the_whole_loop_from_the_command_line(synthetic_image, tmp_path):
    out = str(tmp_path / "run")
    run("prepare", "--image", synthetic_image, "--gen", "B", "--grid", "16",
        "--out", out, expect=0)

    proc = run("simulate", "--run", out, "--no-gsm", "--lead", "30",
               "--decode", expect=0)
    assert "PASS (ok)" in proc.stdout

    report = json.load(open(os.path.join(out, "report.json")))
    assert report["verdict"] == "ok"
    assert report["summary"]["offset_s"] > 30.0
    assert os.path.isfile(os.path.join(out, "received.png"))


def test_decode_json_prints_the_whole_report(synthetic_image, tmp_path):
    out = str(tmp_path / "run")
    run("prepare", "--image", synthetic_image, "--gen", "B", "--grid", "16",
        "--out", out, expect=0)
    run("simulate", "--run", out, "--no-gsm", "--lead", "5", expect=0)

    proc = run("decode", os.path.join(out, "rx_sim.wav"), "--run", out,
               "--json", expect=0)
    report = json.loads(proc.stdout)
    assert report["verdict"] == "ok"
    assert set(report["summary"]) == {
        "preamble_score", "offset_s", "payload_bits", "weak_symbols"}


def test_decoding_noise_exits_nonzero_and_explains(tmp_path):
    import numpy as np

    from voip.audio_io import write_int16_wav

    path = str(tmp_path / "noise.wav")
    write_int16_wav(path, np.random.default_rng(0).normal(0, 0.1, 20 * 8000))

    proc = run("decode", path, "--run", str(tmp_path))
    assert proc.returncode == 1
    assert "FAIL (no-sync)" in proc.stdout
    assert "No preamble" in proc.stdout


def test_decoding_a_missing_file_fails_cleanly(tmp_path):
    proc = run("decode", str(tmp_path / "nope.wav"))
    assert proc.returncode == 1
    assert "No such recording" in proc.stderr


# --------------------------------------------------------------------------
# call
# --------------------------------------------------------------------------

def test_call_without_credentials_says_which_are_missing(synthetic_image, tmp_path):
    out = str(tmp_path / "run")
    run("prepare", "--image", synthetic_image, "--gen", "B", "--out", out, expect=0)

    env = dict(os.environ)
    for name in ("VOIP_SIP_IDENTITY", "VOIP_SIP_PASSWORD", "VOIP_SIP_DIAL"):
        env.pop(name, None)

    proc = subprocess.run(
        [sys.executable, "-m", "voip.cli", "call", "--run", out, "--dry-run"],
        cwd=BACKEND, capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 1
    assert "Missing SIP settings" in proc.stderr
    assert "VOIP_SIP_PASSWORD" in proc.stderr


def test_call_never_echoes_the_password(synthetic_image, tmp_path):
    out = str(tmp_path / "run")
    run("prepare", "--image", synthetic_image, "--gen", "B", "--out", out, expect=0)

    secret = "hunter2-should-not-appear"
    proc = subprocess.run(
        [sys.executable, "-m", "voip.cli", "call", "--run", out, "--dry-run",
         "--identity", "sip:me@sip.linphone.org", "--dial", "sip:you@sip.linphone.org",
         "--password", secret],
        cwd=BACKEND, capture_output=True, text=True, timeout=120)

    assert secret not in proc.stdout and secret not in proc.stderr
    call_json = os.path.join(out, "call.json")
    if os.path.isfile(call_json):
        assert secret not in open(call_json).read()


def test_help_lists_every_command():
    proc = run("--help", expect=0)
    for command in ("check-env", "prepare", "call", "decode", "simulate"):
        assert command in proc.stdout
