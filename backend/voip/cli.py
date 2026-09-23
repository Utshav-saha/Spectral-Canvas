"""Command line for the VoIP transport. Run from backend/:

    python -m voip.cli check-env
    python -m voip.cli prepare --image cat.jpg --gen A --grid 24
    python -m voip.cli simulate --run latest --decode
    python -m voip.cli call --run latest --dial sip:someone@sip.linphone.org
    python -m voip.cli decode ~/Downloads/call.mka --run latest

Exit codes: 0 success, 1 a handled failure, 2 bad arguments.
"""

import argparse
import json
import os
import shutil
import sys

from voip.config import (
    DEFAULT_CODEC,
    DEFAULT_GRID,
    DEFAULT_LEAD_IN_S,
    DEFAULT_LEAD_OUT_S,
    DEFAULT_LEVELS,
    RUNS_ROOT,
    VoipError,
)

OK, FAIL, USAGE = 0, 1, 2


# --------------------------------------------------------------------------
# check-env
# --------------------------------------------------------------------------

def _module_version(name):
    try:
        module = __import__(name)
        return getattr(module, "__version__", "installed")
    except ImportError:
        return None


def cmd_check_env(args):
    from voip import _tel
    from voip.audio_io import ffmpeg_available, ffmpeg_has_libgsm, ffprobe_available
    from voip.call import sdk
    from voip.simulate import gsm_available, pjsua_available

    report = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "packages": {n: _module_version(n)
                     for n in ("numpy", "scipy", "PIL", "sounddevice")},
        "tools": {
            "ffmpeg": shutil.which("ffmpeg"),
            "ffprobe": shutil.which("ffprobe"),
            "pjsua": shutil.which("pjsua"),
            "toast": shutil.which("toast"),
        },
        "ffmpeg_has_libgsm": ffmpeg_has_libgsm(),
        "gsm_codec_available": gsm_available(),
        "tel_modules": {n: _tel.available(n)
                        for n in ("fsk_codec", "image_fsk", "channel_sim")},
        "linphone": {"available": sdk.available(), "version": sdk.version()},
        "runs_root": RUNS_ROOT,
        "runs_writable": _writable(RUNS_ROOT),
    }
    if sdk.available():
        report["linphone"]["probe"] = sdk.probe()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_check_env(report)

    decode_ready = (report["packages"]["numpy"] and report["packages"]["scipy"]
                    and report["packages"]["PIL"] and report["tel_modules"]["fsk_codec"]
                    and report["runs_writable"])
    return OK if decode_ready else FAIL


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".probe")
        with open(probe, "w") as handle:
            handle.write("x")
        os.unlink(probe)
        return True
    except OSError:
        return False


def _mark(ok):
    return "  ok  " if ok else " MISS "


def _print_check_env(r):
    print(f"python {r['python']} on {r['platform']}\n")

    print("packages")
    for name, version in r["packages"].items():
        note = "" if version else ("  (optional: only for the BlackHole fallback)"
                                   if name == "sounddevice" else "")
        print(f" [{_mark(version)}] {name:<12} {version or '-'}{note}")

    print("\ntools")
    for name, path in r["tools"].items():
        print(f" [{_mark(path)}] {name:<12} {path or '-'}")
    if r["tools"]["ffmpeg"] and not r["ffmpeg_has_libgsm"]:
        print("        note: this ffmpeg has no libgsm; channel_sim uses `toast` instead")

    print("\nspectral/tel modules")
    for name, ok in r["tel_modules"].items():
        print(f" [{_mark(ok)}] {name}")

    print("\nlinphone SDK")
    lp = r["linphone"]
    print(f" [{_mark(lp['available'])}] linphone      {lp.get('version') or '-'}")
    if lp.get("probe"):
        missing = [k for k, v in lp["probe"].items() if not v]
        print(f"        {len(lp['probe']) - len(missing)}/{len(lp['probe'])} "
              f"expected attributes present")
        if missing:
            print(f"        missing: {', '.join(missing[:8])}")
    else:
        print("        not installed. The BlackHole fallback needs no SDK:")
        print("        python -m voip.cli call --run latest --fallback")

    print(f"\nruns folder  {r['runs_root']}  [{_mark(r['runs_writable'])}]")

    decode_ready = all([r["packages"]["numpy"], r["packages"]["scipy"],
                        r["packages"]["PIL"], r["tel_modules"]["fsk_codec"],
                        r["runs_writable"]])
    print()
    print(f"decode path : {'READY' if decode_ready else 'NOT READY'}")
    print(f"call path   : {'SDK' if lp['available'] else ('fallback only' if r['packages']['sounddevice'] else 'needs SDK or sounddevice')}")
    print(f"rehearsal   : {'GSM + codec-free' if r['gsm_codec_available'] else 'codec-free only (--no-gsm)'}"
          f"{' + SIP loopback' if r['tools']['pjsua'] else ''}")


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------

def cmd_prepare(args):
    from voip import encode, report

    text = args.text
    if args.text_file:
        with open(os.path.expanduser(args.text_file), encoding="utf-8") as handle:
            text = handle.read()

    common = dict(source=args.image, text=text, generation=args.gen,
                  grid=args.grid, levels=args.levels, colour=args.colour,
                  lead_in=args.lead_in, lead_out=args.lead_out)

    if args.dry_run:
        plan = encode.plan(**common)
        print(json.dumps(plan, indent=2) if args.json else _format_plan(plan))
        return OK

    result = encode.prepare(as_image=args.as_image, locked=args.lock,
                            caller=args.caller, receiver=args.receiver,
                            pin=args.pin, **common)

    slug = args.run_name or _auto_slug(args, result)
    run_dir = args.out or report.new_run(slug)
    encode.write_run(result, run_dir)

    for warning in result.warnings:
        print(f"note: {warning}")

    wire = result.manifest["wire"]
    print(f"run       {run_dir}")
    sent = result.manifest["payload"]
    print(f"payload   {sent['rows']}x{sent['cols']} at {sent['levels']} levels"
          f"   generation {args.gen}")
    print(f"airtime   {wire['airtime_seconds']:.1f} s  "
          f"(+{wire['lead_in_seconds']:.0f}s lead-in, +{wire['lead_out_seconds']:.0f}s tail "
          f"= {wire['total_seconds']:.1f} s of call)")
    print(f"wav       {os.path.join(run_dir, 'tx.wav')}")
    print(f"\nnext: python -m voip.cli simulate --run {os.path.basename(run_dir)} --decode")
    return OK


def _auto_slug(args, result):
    payload = result.manifest["payload"]
    if payload["kind"] == "text":
        return f"text-{payload['characters']}c"
    return f"gen{args.gen.lower()}-{payload['rows']}x{payload['cols']}"


def _format_plan(plan):
    lines = [f"generation  {plan['generation']}"]
    for key in ("kind", "rows", "cols", "levels", "mode", "channels",
                "payload_bits", "symbols"):
        if key in plan:
            lines.append(f"{key:<12}{plan[key]}")
    lines.append(f"airtime     {plan['airtime_seconds']} s")
    lines.append(f"total call  {plan['total_seconds']} s")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# simulate
# --------------------------------------------------------------------------

def cmd_simulate(args):
    from voip import report, simulate
    from voip.audio_io import load_audio, write_int16_wav

    run_dir = report.resolve_run(args.run)
    tx = os.path.join(run_dir, "tx.wav")
    if not os.path.isfile(tx):
        raise VoipError(f"No tx.wav in {run_dir}. Run prepare first.")

    audio, _ = load_audio(tx)

    if args.pjsua:
        rx_path = os.path.join(run_dir, "rx_sip.wav")
        outcome = simulate.local_call(tx, rx_path, codec=args.codec)
        print(f"SIP loopback  codec requested {outcome['requested_codec']}, "
              f"negotiated {outcome['negotiated'] or 'unknown'}")
        if outcome["warning"]:
            print(f"WARNING: {outcome['warning']}")
        recording = rx_path
    else:
        received, meta = simulate.simulate(
            audio, loss=args.loss, noise_db=args.noise_db, gain_db=args.gain_db,
            lead_seconds=args.lead, seed=args.seed, gsm=not args.no_gsm)
        recording = os.path.join(run_dir, "rx_sim.wav")
        write_int16_wav(recording, received)
        print(f"simulated     {meta['path']}  codec={meta['codec'] or 'none'}  "
              f"loss={meta['loss']}  noise={meta['noise_db']}dB  "
              f"lead={meta['lead_seconds']:.1f}s")

    print(f"recording     {recording}")
    if not args.decode:
        print(f"\nnext: python -m voip.cli decode {recording} --run {os.path.basename(run_dir)}")
        return OK

    return _decode_into(recording, run_dir, args, locked=args.lock,
                        caller=args.caller, receiver=args.receiver, pin=args.pin)


# --------------------------------------------------------------------------
# decode
# --------------------------------------------------------------------------

def cmd_decode(args):
    from voip import report
    run_dir = report.resolve_run(args.run) if args.run else None
    return _decode_into(args.recording, run_dir, args, locked=args.lock,
                        caller=args.caller, receiver=args.receiver, pin=args.pin)


def _decode_into(recording, run_dir, args, locked=False, caller=None,
                 receiver=None, pin=None):
    from PIL import Image

    from voip import decode as decoder
    from voip import report

    manifest = None
    sent_image = None
    if run_dir and os.path.isfile(os.path.join(run_dir, "manifest.json")):
        manifest = report.load_manifest(run_dir)
        manifest["_run_dir"] = run_dir
        sent = os.path.join(run_dir, (manifest.get("artifacts") or {}).get("sent_png", ""))
        if os.path.isfile(sent):
            sent_image = Image.open(sent)

    result = decoder.decode(
        recording, generation=getattr(args, "gen", "auto") or "auto",
        locked=locked, caller=caller, receiver=receiver, pin=pin,
        manifest=manifest,
        refine=not getattr(args, "no_refine", False),
        drift_scan=getattr(args, "drift_scan", False),
        search_seconds=getattr(args, "search_seconds", None),
        weak_threshold=getattr(args, "weak_threshold", None) or 1.6,
    )

    target = run_dir or report.new_run("decode")
    if recording and os.path.isfile(recording) and os.path.dirname(os.path.abspath(recording)) != os.path.abspath(target):
        shutil.copy2(recording, os.path.join(target, os.path.basename(recording)))
    decoder.write_run(result, target, recording_path=recording, sent_image=sent_image)

    if getattr(args, "json", False):
        print(json.dumps(report.jsonable(result.report), indent=2))
    else:
        _print_decode(result, target)
    return OK if result.ok else FAIL


def _print_decode(result, run_dir):
    from voip.report import summary_line

    rep = result.report
    print(f"\n{summary_line(rep)}")

    frame = rep.get("frame")
    if frame:
        if frame.get("header_hex"):
            detail = (f"generation {frame['generation']}  header 0x{frame['header_hex']}  "
                      f"{frame['available_symbols']}/{frame['expected_symbols']} symbols")
        else:
            # Generation A: pilot-aligned, no header, no symbol decisions
            detail = (f"generation {frame['generation']}  "
                      f"{frame['rows']}x{frame['cols']} at {frame['levels']} levels  "
                      f"{'CUT SHORT' if frame.get('truncated') else 'complete'}")
        print(f"frame     {detail}")

    payload = rep.get("payload") or {}
    if payload.get("opened"):
        if payload["kind"] == "text":
            print(f"text      {payload['characters']} characters")
            print(f"\n  {result.text}\n")
        else:
            print(f"picture   {payload['rows']}x{payload['cols']} at "
                  f"{payload['levels']} gray levels")
        if payload.get("repaired_bytes") is not None:
            print(f"repaired  {payload['repaired_bytes']} bytes "
                  f"(budget {payload.get('rs_limit_total', '?')})")
    elif payload.get("reason"):
        print(f"failed    {payload['reason']}")

    audio = rep.get("audio") or {}
    if audio:
        print(f"level     peak {audio['peak_dbfs']} dBFS, rms {audio['rms_dbfs']} dBFS, "
              f"{audio['clipped_samples']} clipped")

    for hint in rep.get("hints", []):
        print(f"\n  -> {hint}")
    print(f"\nreport    {os.path.join(run_dir, 'report.json')}")


# --------------------------------------------------------------------------
# call
# --------------------------------------------------------------------------

def cmd_call(args):
    from voip import report
    from voip.call import fallback, session

    run_dir = report.resolve_run(args.run)
    wav = args.wav or os.path.join(run_dir, "tx.wav")
    if not os.path.isfile(wav):
        raise VoipError(f"No {wav}. Run prepare first.")

    if args.fallback:
        outcome = fallback.play_to_device(wav, device=args.device,
                                          countdown=args.countdown)
        report.write_call(run_dir, outcome)
        print(f"\ncall.json  {os.path.join(run_dir, 'call.json')}")
        return OK if outcome.get("ok") else FAIL

    config = session.CallConfig.from_args(args, wav)
    with session.CallSession(config, run_dir=run_dir) as call:
        outcome = call.run(dry_run=args.dry_run)

    report.write_call(run_dir, outcome)
    print(f"\ncall.json  {os.path.join(run_dir, 'call.json')}")
    if not args.dry_run:
        print("next: copy the phone's recording back, then")
        print(f"      python -m voip.cli decode <recording> --run {os.path.basename(run_dir)}")
    return OK if outcome.get("ok") else FAIL


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def _add_lock_flags(parser):
    parser.add_argument("--lock", action="store_true",
                        help="scramble with the two phone numbers and a PIN")
    parser.add_argument("--caller", help="11-digit sending number")
    parser.add_argument("--receiver", help="11-digit receiving number")
    parser.add_argument("--pin", help="4-8 digit PIN")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m voip.cli",
        description="Send a picture or a message over a real VoIP call, and decode "
                    "the recording that comes back.")
    subs = parser.add_subparsers(dest="command", required=True)

    # check-env
    env = subs.add_parser("check-env", help="what is installed and what is missing")
    env.add_argument("--json", action="store_true")
    env.set_defaults(func=cmd_check_env)

    # prepare
    prep = subs.add_parser("prepare", help="picture or text -> tx.wav + manifest")
    source = prep.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="picture to send")
    source.add_argument("--text", help="message to send")
    source.add_argument("--text-file", help="file holding the message")
    prep.add_argument("--gen", choices=["A", "B"], default="A",
                      help="B = raw pixels, small and quick; C = WebP, real colour (default)")
    prep.add_argument("--colour", "--color", action="store_true",
                      dest="colour", help="send in colour (Generation A only)")
    prep.add_argument("--grid", type=int, default=DEFAULT_GRID, help="Gen-B square side")
    prep.add_argument("--levels", type=int, default=DEFAULT_LEVELS,
                      choices=[2, 4, 16, 256], help="Gen-B gray levels")
    prep.add_argument("--as-image", action="store_true",
                      help="render --text as a picture instead of sending it as bytes")
    prep.add_argument("--lead-in", type=float, default=DEFAULT_LEAD_IN_S)
    prep.add_argument("--lead-out", type=float, default=DEFAULT_LEAD_OUT_S)
    prep.add_argument("--run-name")
    prep.add_argument("--out", help="write here instead of a new run folder")
    prep.add_argument("--dry-run", action="store_true", help="print the plan, make no audio")
    prep.add_argument("--json", action="store_true")
    _add_lock_flags(prep)
    prep.set_defaults(func=cmd_prepare)

    # simulate
    sim = subs.add_parser("simulate", help="rehearse without a phone")
    sim.add_argument("--run", default="latest")
    sim.add_argument("--loss", type=float, default=0.02)
    sim.add_argument("--noise-db", type=float, default=-45.0)
    sim.add_argument("--gain-db", type=float, default=-6.0)
    sim.add_argument("--lead", type=float, default=30.0,
                     help="seconds of silence before the transmission")
    sim.add_argument("--seed", type=int, default=0)
    sim.add_argument("--no-gsm", action="store_true",
                     help="skip the GSM codec (works without ffmpeg/libgsm)")
    sim.add_argument("--pjsua", action="store_true",
                     help="place a real SIP loopback call instead")
    sim.add_argument("--codec", default="GSM", help="codec for --pjsua")
    sim.add_argument("--decode", action="store_true", help="decode the result immediately")
    sim.add_argument("--gen", choices=["auto", "A", "B"], default="auto")
    sim.add_argument("--json", action="store_true")
    _add_lock_flags(sim)
    sim.set_defaults(func=cmd_simulate)

    # decode
    dec = subs.add_parser("decode", help="recording -> picture + report")
    dec.add_argument("recording", help=".wav .mka .mkv .m4a .caf .opus ...")
    dec.add_argument("--run", help="run folder to score against and write into")
    dec.add_argument("--gen", choices=["auto", "A", "B"], default="auto")
    dec.add_argument("--search-seconds", type=float,
                     help="limit the preamble search (default: the whole file)")
    dec.add_argument("--no-refine", action="store_true")
    dec.add_argument("--drift-scan", action="store_true",
                     help="search for laptop/phone clock drift")
    dec.add_argument("--weak-threshold", type=float, default=1.6)
    dec.add_argument("--json", action="store_true")
    _add_lock_flags(dec)
    dec.set_defaults(func=cmd_decode)

    # call
    call = subs.add_parser("call", help="place the real Linphone call")
    call.add_argument("--run", default="latest")
    call.add_argument("--wav", help="default: <run>/tx.wav")
    call.add_argument("--dial", help="sip:number@domain to call")
    call.add_argument("--identity", help="your own sip:user@domain")
    call.add_argument("--password", help="prefer the VOIP_SIP_PASSWORD env var")
    call.add_argument("--domain")
    call.add_argument("--codec", default=DEFAULT_CODEC,
                      choices=["PCMU", "PCMA", "GSM", "any"])
    call.add_argument("--stun")
    call.add_argument("--no-stun", action="store_true")
    call.add_argument("--no-ice", action="store_true")
    call.add_argument("--keep-dsp", action="store_true",
                      help="leave echo cancellation and AGC on (for comparison runs)")
    call.add_argument("--play-file", action="store_true",
                      help="use core.play_file instead of call.player")
    call.add_argument("--ready", default="enter",
                      help="'enter' to wait for a keypress, or 'delay:8'")
    call.add_argument("--answer-timeout", type=float, default=60.0)
    call.add_argument("--fallback", action="store_true",
                      help="play into a virtual audio device; no SDK needed")
    call.add_argument("--device", default="BlackHole",
                      help="output device for --fallback")
    call.add_argument("--countdown", type=float, default=5.0)
    call.add_argument("--dry-run", action="store_true",
                      help="register and probe, place no call")
    call.set_defaults(func=cmd_call)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except VoipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return FAIL
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return FAIL


if __name__ == "__main__":
    sys.exit(main())
