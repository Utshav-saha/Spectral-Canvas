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



def test_the_genb_marker_is_out_of_reach_in_airtime():
    """61440 bytes is 82 minutes of call, so the collision cannot happen in practice."""
    fsk = _tel.fsk()
    minutes = GEN_B_FLOOR * 2 * fsk.SYMBOL_MS / 1000.0 / 60.0
    assert minutes > 60



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





def test_frame_offset_round_trips(genb_audio):
    audio, _, _ = genb_audio
    recording = np.concatenate([np.zeros(int(2 * SAMPLE_RATE)), audio])
    offset = sync.find_preamble(recording).offset
    frame = framing.read_frame(recording, offset)
    assert framing.frame_offset(frame) == offset


def test_level_codes_are_a_bijection():
    assert {CODE_LEVELS[v]: v for v in CODE_LEVELS} == LEVEL_CODES


def test_a_header_without_the_marker_is_not_one_of_ours():
    """Generation C used every value below the marker as a byte count. With it
    cut, anything down there is noise that happened to land on a valid symbol
    grid, and read_frame has to say so rather than invent a picture."""
    assert framing.parse_header(_to_bits(0x0100))["generation"] == "unknown"
    assert framing.parse_header(_to_bits(GEN_B_FLOOR - 1))["generation"] == "unknown"
    assert framing.parse_header(_to_bits(GEN_B_FLOOR))["generation"] == "B"


def _to_bits(value, width=16):
    import numpy as np
    return np.array([int(b) for b in format(int(value), f"0{width}b")], dtype=np.uint8)
