#!/usr/bin/env bash
# A real SIP call, on your MacBook, with no account and no provider.
#
#   brew install pjproject        # NB: pjproject, not pjsip
#
# If `which pjsua` comes up empty after that, the formula did not ship the
# CLI binary - build from source instead:
#
#   git clone https://github.com/pjsip/pjproject && cd pjproject
#   echo '#include <pj/config_site_sample.h>' > pjlib/include/pj/config_site.h
#   ./configure && make dep && make
#   # binary lands in pjsip-apps/bin/ with an arch suffix, e.g.
#   # pjsua-arm-apple-darwin23.0.0 - symlink it as `pjsua`
#
# Two pjsua instances call each other over loopback. Real SIP signalling,
# real RTP packets, real GSM 06.10 encoding, real jitter buffer. The only
# thing missing versus a network call is the packet loss, which channel_sim
# already lets you add.
#
#   ./run_local_call.sh tx.wav rx.wav
#
# tx.wav must be 16-bit mono PCM at 8 kHz - use fsk_codec/to_int16_wav,
# NOT scipy's write() on a float64 array, which pjsua cannot open.
#
# macOS notes:
#   * zsh does NOT pass an unquoted * through, it aborts with "no matches
#     found". --dis-codec='*' must stay quoted.
#   * --null-audio means no CoreAudio device is opened, so you get no
#     microphone permission prompt. Do not drop it.
#   * Flag names drift between versions. If one is rejected, check
#     `pjsua --help | grep -i <thing>` and adjust. --auto-loop in
#     particular is a boolean in some builds.

set -euo pipefail

TX="${1:-tx.wav}"
RX="${2:-rx.wav}"
CODEC="${CODEC:-GSM}"          # try iLBC, speex/8000, PCMU for comparison

COMMON=(--null-audio --clock-rate=8000 --no-vad --no-tcp
        --dis-codec='*' --add-codec="$CODEC")

# let the whole file play, plus margin for call setup
DURATION=$(python3 -c "import wave,sys;w=wave.open('$TX');print(int(w.getnframes()/w.getframerate())+4)")

echo "playing  $TX  (${DURATION}s)"
echo "codec    $CODEC"
echo "recording to $RX"

# pjsua quits the moment its stdin closes ("Cannot switch back to console
# from file redirection"), and a backgrounded job's stdin is closed. So each
# instance gets a pipe that stays open for the length of the call and then
# types pjsua's own quit command - a clean exit, which also lets the recorder
# finish writing the WAV header (kill does not).
# No --duration: pjsua reads --duration=0 as "hang up after 0 s".

# Callee: answers automatically and plays the file into the call.
{ sleep $((DURATION + 3)); echo q; sleep 3; } | pjsua "${COMMON[@]}" \
      --local-port=5062 \
      --auto-answer=200 \
      --play-file="$TX" --auto-play \
      >/tmp/pjsua_callee.log 2>&1 &
CALLEE=$!
sleep 2

# Caller: dials, records everything it receives, hangs up, quits.
{ sleep "$DURATION"; echo h; sleep 1; echo q; sleep 3; } | pjsua "${COMMON[@]}" \
      --local-port=5060 \
      --rec-file="$RX" --auto-rec \
      sip:127.0.0.1:5062 \
      >/tmp/pjsua_caller.log 2>&1 &
CALLER=$!

wait $CALLER $CALLEE 2>/dev/null || true

echo "done -> $RX"
echo
echo "VERIFY THE CODEC. If GSM was not compiled in, pjsua falls back to PCMU"
echo "(G.711) without complaining. G.711 is nearly transparent, so your images"
echo "would decode perfectly and the experiment would prove nothing."
echo
grep -iE "sdp|codec|GSM|PCMU|iLBC" /tmp/pjsua_caller.log | head -20 || true
