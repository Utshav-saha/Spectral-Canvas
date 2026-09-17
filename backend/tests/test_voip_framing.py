"""The 16 header bits, and refusing to decode symbols that are not there."""

import numpy as np
import pytest

from voip import _tel, framing, sync
from voip.config import CODE_LEVELS, LEVEL_CODES, SAMPLE_RATE, FrameError
from voip.framing import GEN_B_FLOOR


# --------------------------------------------------------------------------
# Generation B descriptor
# --------------------------------------------------------------------------

def test_genb_header_round_trips_for_every_shape():
    for rows in range(1, 33):
        for cols in range(1, 33):
            for levels in LEVEL_CODES:
                parsed = framing.parse_header(framing.build_genb_header(rows, cols, levels))
                assert (parsed["generation"], parsed["rows"], parsed["cols"],
                        parsed["levels"]) == ("B", rows, cols, levels)


def test_genb_rejects_shapes_it_cannot_describe():
    with pytest.raises(FrameError, match="Generation B carries up to"):
        framing.build_genb_header(33, 16, 4)
    with pytest.raises(FrameError, match="Gray levels"):
        framing.build_genb_header(16, 16, 8)


def test_no_plausible_genc_packet_reads_as_genb():
    """The marker is only safe if a real byte count can never reach it."""
    for packet_bytes in list(range(1, 3000)) + list(range(58000, GEN_B_FLOOR)):
        parsed = framing.parse_header(framing.build_genc_header(packet_bytes))
        assert parsed["generation"] == "C"
        assert parsed["packet_bytes"] == packet_bytes


def test_the_genb_marker_is_out_of_reach_in_airtime():
    """61440 bytes is 82 minutes of call, so the collision cannot happen in practice."""
    fsk = _tel.fsk()
    minutes = GEN_B_FLOOR * 2 * fsk.SYMBOL_MS / 1000.0 / 60.0
    assert minutes > 60


def test_genc_header_refuses_an_oversized_packet():
    with pytest.raises(FrameError, match="16-bit header"):
        framing.build_genc_header(GEN_B_FLOOR + 1)


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rows,cols,levels", [
    (16, 16, 4), (24, 24, 4), (32, 32, 4), (8, 8, 2),
    (12, 20, 16), (5, 7, 4), (32, 32, 256), (1, 1, 2),
])
def test_genb_info_matches_what_the_modem_actually_produces(rows, cols, levels):
    """Every pad has to be derivable, or Generation B is not self-describing."""
    fsk = _tel.fsk()
    activation = np.random.default_rng(2).random((rows, cols))
    bits = _tel.image_fsk().activation_to_bits(activation, levels)
    _, info = fsk.modulate(bits, fec=True,
                           header=framing.build_genb_header(rows, cols, levels))

    derived = framing.genb_info(rows, cols, levels)
    for key in ("n_payload_bits", "n_symbols", "hamming_pad",
                "interleave_pad", "symbol_pad"):
        assert int(derived[key]) == int(info[key]), key


def test_genc_info_is_two_symbols_per_byte():
    info = framing.genc_info(500)
    assert info["n_symbols"] == 1000
    assert info["n_payload_bits"] == 4000
    assert info["fec"] is False


# --------------------------------------------------------------------------
# Reading a frame out of audio
# --------------------------------------------------------------------------

def test_genb_is_self_describing_from_audio_alone(genb_audio):
    """The receiver gets no info dict -- only the recording."""
    audio, activation, _ = genb_audio
    recording = np.concatenate([np.zeros(int(30 * SAMPLE_RATE)), audio,
                                np.zeros(SAMPLE_RATE)])

    found = sync.find_preamble(recording)
    frame = framing.read_frame(recording, found.offset)
    assert (frame.generation, frame.rows, frame.cols, frame.levels) == ("B", 16, 16, 4)

    bits, _, _, bounds = framing.demodulate_frame(recording, frame)
    assert not bounds["truncated"]
    rebuilt = _tel.image_fsk().bits_to_activation(bits, (frame.rows, frame.cols), frame.levels)
    assert np.array_equal(activation, rebuilt)


def test_truncation_is_reported_not_invented(genb_audio):
    """The modem zero-pads a short tail and says nothing. That must not survive here."""
    audio, _, _ = genb_audio
    recording = np.concatenate([np.zeros(SAMPLE_RATE), audio])
    short = recording[:len(recording) - int(3.0 * SAMPLE_RATE)]

    frame = framing.read_frame(short, sync.find_preamble(short).offset)
    bits, _, _, bounds = framing.demodulate_frame(short, frame)

    assert bounds["truncated"]
    assert bounds["truncated_symbols"] > 0
    assert bounds["available_symbols"] < bounds["expected_symbols"]
    assert len(bits) < frame.info["n_payload_bits"], \
        "a clamped decode must return fewer bits, not fabricated ones"


def test_truncation_can_be_made_fatal(genb_audio):
    audio, _, _ = genb_audio
    short = np.concatenate([np.zeros(SAMPLE_RATE), audio])[:-int(3.0 * SAMPLE_RATE)]
    frame = framing.read_frame(short, sync.find_preamble(short).offset)
    with pytest.raises(FrameError, match="short of"):
        framing.demodulate_frame(short, frame, clamp=False)


def test_a_packet_smaller_than_its_parity_is_not_a_transmission(genb_audio):
    """Generalises tel_pipeline._find_transmission's sanity check."""
    fsk = _tel.fsk()
    audio, _ = fsk.modulate(np.zeros(64, np.uint8), fec=False,
                            header=framing.build_genc_header(4))
    recording = np.concatenate([np.zeros(SAMPLE_RATE), audio, np.zeros(SAMPLE_RATE)])
    with pytest.raises(FrameError, match="not a transmission"):
        framing.read_frame(recording, sync.find_preamble(recording).offset, rs_parity=32)


def test_header_ending_mid_recording_is_an_error():
    fsk = _tel.fsk()
    audio, _ = fsk.modulate(np.zeros(64, np.uint8), header=framing.build_genc_header(100))
    clipped = audio[:len(fsk.PREAMBLE) * fsk.SYMBOL_SAMPLES + 200]
    with pytest.raises(FrameError):
        framing.read_frame(clipped, 0)


def test_forcing_the_generation_overrides_the_marker(genb_audio):
    """spectral/tel/demo.py writes a Gen-B wav whose header has no marker."""
    audio, _, _ = genb_audio
    recording = np.concatenate([np.zeros(SAMPLE_RATE), audio])
    offset = sync.find_preamble(recording).offset

    assert framing.read_frame(recording, offset, expect="auto").generation == "B"
    forced = framing.read_frame(recording, offset, expect="C", rs_parity=32)
    assert forced.generation == "C"
    assert forced.warnings


def test_frame_offset_round_trips(genb_audio):
    audio, _, _ = genb_audio
    recording = np.concatenate([np.zeros(int(2 * SAMPLE_RATE)), audio])
    offset = sync.find_preamble(recording).offset
    frame = framing.read_frame(recording, offset)
    assert framing.frame_offset(frame) == offset


def test_level_codes_are_a_bijection():
    assert {CODE_LEVELS[v]: v for v in CODE_LEVELS} == LEVEL_CODES
