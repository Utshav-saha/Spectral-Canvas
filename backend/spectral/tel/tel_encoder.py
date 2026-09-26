"""
Telephony-band encoder.
This is for gen A

Three differences:

  1. narrowband frequency plan 
  2. two constant-amplitude pilot tones added to EVERY frame
  3. tukey window instead of hann

The pilots are transmitted at a known, fixed
amplitude, so whatever the channel does to their magnitude it did to the
data rows at nearby frequencies too. The decoder divides them out. After
that the decoder no longer needs normalization_gain, the window sum, or
any absolute level agreement with the encoder.
"""

import numpy as np
from scipy.signal.windows import tukey

import tel_config as config


def row_frequencies(rows=config.ROWS, f_low=config.F_LOW, f_high=config.F_HIGH,
                    bin_width=config.BIN_WIDTH):
    
    freqs = np.linspace(f_high, f_low, rows)
    return np.round(freqs / bin_width) * bin_width


def snap(frequency, bin_width=config.BIN_WIDTH):
    return float(np.round(frequency / bin_width) * bin_width)


def generate_frame(amplitudes, freqs, t, window, pilot_freqs, pilot_amplitude):
    frame = np.zeros_like(t)

    for amp, f in zip(amplitudes, freqs):
        if amp > 0:
            frame += amp * np.sin(2 * np.pi * f * t)

    # This pilot addition is new, at constant amplitude, every frame
    for f in pilot_freqs:
        frame += pilot_amplitude * np.sin(2 * np.pi * f * t)

    return frame * window


def encode(activation,
           sample_rate=config.SAMPLE_RATE,
           frame_samples=config.FRAME_SAMPLES,
           f_low=config.F_LOW,
           f_high=config.F_HIGH,
           pilot_low=config.PILOT_LOW,
           pilot_high=config.PILOT_HIGH,
           pilot_amplitude=config.PILOT_AMPLITUDE,
           tukey_alpha=config.TUKEY_ALPHA,
           preamble_frames=config.PREAMBLE_FRAMES,
           target_peak=0.7):
    
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

    # Preamble: pilots only, no data.
    preamble = [
        generate_frame(np.zeros(rows), freqs, t, window, (p_lo, p_hi), pilot_amplitude)
        for _ in range(preamble_frames)
    ]

    body = []
    for channel in channels:
        for c in range(columns):
            body.append(
                generate_frame(channel[:, c], freqs, t, window,
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
             sample_rate=config.SAMPLE_RATE,
             frame_samples=config.FRAME_SAMPLES,
             f_low=config.F_LOW,
             f_high=config.F_HIGH,
             pilot_low=config.PILOT_LOW,
             pilot_high=config.PILOT_HIGH,
             pilot_amplitude=config.PILOT_AMPLITUDE,
             tukey_alpha=config.TUKEY_ALPHA,
             preamble_frames=config.PREAMBLE_FRAMES):

    
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
        "gray_levels": config.GRAY_LEVELS,
        "duration_seconds": frames * frame_samples / sample_rate,
    }


def to_int16_wav(audio, path, sample_rate=config.SAMPLE_RATE):
    """pjsua's --play-file wants 16-bit mono PCM. scipy's write() on your
    float64 array produces a 64-bit float WAV, which pjsua refuses to open.
    """
    from scipy.io.wavfile import write
    pcm = np.clip(audio, -1.0, 1.0)
    write(path, sample_rate, (pcm * 32767.0).astype(np.int16))
