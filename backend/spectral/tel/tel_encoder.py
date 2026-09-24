"""
Telephony-band encoder.

Same idea as your audio_encoder.encode_activation_matrix: one column per
frame, one sinusoid per row, amplitude = activation. Three differences:

  1. narrowband frequency plan (see tel_config)
  2. two constant-amplitude pilot tones added to EVERY frame
  3. tukey window instead of hann

The pilots are the important one. They are transmitted at a known, fixed
amplitude, so whatever the channel does to their magnitude it did to the
data rows at nearby frequencies too. The decoder divides them out. After
that the decoder no longer needs normalization_gain, the window sum, or
any absolute level agreement with the encoder.
"""

import numpy as np
from scipy.signal.windows import tukey

import tel_config as cfg


def row_frequencies(rows=cfg.ROWS, f_low=cfg.F_LOW, f_high=cfg.F_HIGH,
                    bin_width=cfg.BIN_WIDTH):
    """Row 0 = highest frequency, same 'top_high to bottom_low' mapping you use.

    Snapped to the FFT bin grid so each tone lands dead centre in one bin,
    exactly as your bin_snap does.
    """
    freqs = np.linspace(f_high, f_low, rows)
    return np.round(freqs / bin_width) * bin_width


def snap(frequency, bin_width=cfg.BIN_WIDTH):
    return float(np.round(frequency / bin_width) * bin_width)


def _frame(amplitudes, freqs, t, window, pilot_freqs, pilot_amplitude):
    """One column -> one frame of audio."""
    frame = np.zeros_like(t)

    for amp, f in zip(amplitudes, freqs):
        if amp > 0:
            frame += amp * np.sin(2 * np.pi * f * t)

    # pilots go in unconditionally, at constant amplitude, every frame
    for f in pilot_freqs:
        frame += pilot_amplitude * np.sin(2 * np.pi * f * t)

    return frame * window


def encode(activation,
           sample_rate=cfg.SAMPLE_RATE,
           frame_samples=cfg.FRAME_SAMPLES,
           f_low=cfg.F_LOW,
           f_high=cfg.F_HIGH,
           pilot_low=cfg.PILOT_LOW,
           pilot_high=cfg.PILOT_HIGH,
           pilot_amplitude=cfg.PILOT_AMPLITUDE,
           tukey_alpha=cfg.TUKEY_ALPHA,
           preamble_frames=cfg.PREAMBLE_FRAMES,
           target_peak=0.7):
    """activation: (rows, columns) float in 0..1, or (rows, columns, 3) for RGB.

    Returns (audio float64 in -1..1, metadata dict).
    """
    if activation.ndim == 3:
        channels = [activation[:, :, i] for i in range(activation.shape[2])]
        mode = "RGB"
    else:
        channels = [activation]
        mode = "L"

    rows = channels[0].shape[0]
    columns = channels[0].shape[1]

    bin_width = sample_rate / frame_samples
    freqs = row_frequencies(rows, f_low, f_high, bin_width)
    p_lo = snap(pilot_low, bin_width)
    p_hi = snap(pilot_high, bin_width)

    t = np.arange(frame_samples) / sample_rate
    window = tukey(frame_samples, alpha=tukey_alpha)

    # Preamble: pilots only, no data. Gives the decoder something with a
    # known spectral signature to lock onto before the picture starts.
    preamble = [
        _frame(np.zeros(rows), freqs, t, window, (p_lo, p_hi), pilot_amplitude)
        for _ in range(preamble_frames)
    ]

    body = []
    for channel in channels:
        for c in range(columns):
            body.append(
                _frame(channel[:, c], freqs, t, window,
                       (p_lo, p_hi), pilot_amplitude)
            )

    audio = np.concatenate(preamble + body)

    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio * (target_peak / peak)

    metadata = describe(rows, columns, len(channels), sample_rate=sample_rate,
                        frame_samples=frame_samples, f_low=f_low, f_high=f_high,
                        pilot_low=pilot_low, pilot_high=pilot_high,
                        pilot_amplitude=pilot_amplitude, tukey_alpha=tukey_alpha,
                        preamble_frames=preamble_frames)
    return audio, metadata


def describe(rows, columns, channels=1,
             sample_rate=cfg.SAMPLE_RATE,
             frame_samples=cfg.FRAME_SAMPLES,
             f_low=cfg.F_LOW,
             f_high=cfg.F_HIGH,
             pilot_low=cfg.PILOT_LOW,
             pilot_high=cfg.PILOT_HIGH,
             pilot_amplitude=cfg.PILOT_AMPLITUDE,
             tukey_alpha=cfg.TUKEY_ALPHA,
             preamble_frames=cfg.PREAMBLE_FRAMES):
    """encode()'s metadata from the geometry alone, with no audio made.

    Everything else is a constant of this module, so a receiver that has read
    rows, columns and channels off the wire can rebuild the rest.
    """
    bin_width = sample_rate / frame_samples
    frames = preamble_frames + columns * channels
    return {
        "sample_rate": sample_rate,
        "frame_samples": frame_samples,
        "bin_width": bin_width,
        "rows": rows,
        "columns": columns,
        "mode": "RGB" if channels == 3 else "L",
        "channels": channels,
        "row_frequencies": row_frequencies(rows, f_low, f_high, bin_width).tolist(),
        "pilot_low": snap(pilot_low, bin_width),
        "pilot_high": snap(pilot_high, bin_width),
        "pilot_amplitude": pilot_amplitude,
        "tukey_alpha": tukey_alpha,
        "preamble_frames": preamble_frames,
        "gray_levels": cfg.GRAY_LEVELS,
        "duration_seconds": frames * frame_samples / sample_rate,
    }


def to_int16_wav(audio, path, sample_rate=cfg.SAMPLE_RATE):
    """pjsua's --play-file wants 16-bit mono PCM. scipy's write() on your
    float64 array produces a 64-bit float WAV, which pjsua refuses to open.
    """
    from scipy.io.wavfile import write
    pcm = np.clip(audio, -1.0, 1.0)
    write(path, sample_rate, (pcm * 32767.0).astype(np.int16))
