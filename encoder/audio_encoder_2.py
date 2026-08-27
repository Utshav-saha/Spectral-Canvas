import numpy as np
from scipy.io.wavfile import write
from image_preprocessor_2 import process_gray   # was: process (binary)
import json

def encode(image_path, target_width=16, target_height=16, output_file='output.wav',
            sampling_rate=44100, f_min=1000, f_max=8000,
             frame_duration=0.1, gray_levels=16, bin_snap=True):
            # was: ..., threshold=128):

    # brightness -> amplitude map (float 0.0..1.0), instead of a 0/1 binary image
    activation = process_gray(image_path, target_width=target_width,
                              target_height=target_height, gray_levels=gray_levels)
    rows = activation.shape[0]
    cols = activation.shape[1]

    # f_max should be less than sampling_rate / 2 - Nyquist frequency
    if f_max >= sampling_rate / 2:
        raise ValueError("f_max should be less than sampling_rate/2")

    row_frequencies = np.linspace(f_max,f_min,rows)

    frame_duration = 0.1
    frame_samples = int(sampling_rate*frame_duration)

    # Snap every row tone onto an exact FFT-bin centre (multiple of sample_rate/N).
    # Grayscale is far more crosstalk-sensitive than binary was: leakage from a
    # loud neighbour adds straight onto a pixel's recovered value and muddies the
    # grey. On-bin tones are orthogonal, so neighbours don't bleed. The decoder
    # reads these same snapped frequencies back from metadata, so nothing else
    # needs to change. Set bin_snap=False to keep the raw linspace tones.
    if bin_snap:
        bin_width = sampling_rate / frame_samples
        row_frequencies = np.round(row_frequencies / bin_width) * bin_width

    n = np.arange(frame_samples)
    t = n / sampling_rate

    # 1 column = 1 frame = frame_samples
    # each row now contributes a sine SCALED by that pixel's brightness:
    # x[n] = a1*sin(2*pi*f1*t) + a2*sin(2*pi*f2*t) + ...

    frames = []
    window = np.hanning(frame_samples)

    for idx in range(cols):

        # amplitude of every row for this column (0.0 = white/silent, 1.0 = black/loud)
        amp = activation[:, idx]

        # was (binary): only "on" rows, all at unit amplitude
        # col = binary_image[:, idx]
        # one_rows = np.where(col == 1)[0]
        frame = np.zeros_like(t, dtype=np.float64)

        # was:
        # for row in one_rows:
        #     freq = row_frequencies[row]
        #     frame += np.sin(2 * np.pi * freq * t)
        for row in range(rows):
            if amp[row] > 0:
                frame += amp[row] * np.sin(2 * np.pi * row_frequencies[row] * t)

        # sharp transitions = Spectral Leakage
        # A Hann window smoothly changes frame amplitude
        frame *= window
        frames.append(frame)

    final_audio = np.concatenate(frames)

    peak = np.max(np.abs(final_audio))

    # Normalization - a single global scalar, so the RATIOS between pixel
    # amplitudes are preserved (which is all grayscale recovery needs).
    if peak > 0:
        final_audio = 0.8 * final_audio / peak

    write(output_file, sampling_rate, final_audio)

    create_metadata_json(sampling_rate, rows, cols,
                         row_frequencies, f_min, f_max, frame_duration,
                         frame_samples, gray_levels, bin_snap)


def create_metadata_json(sampling_rate, rows, cols, row_frequencies,
                         f_min, f_max, frame_duration,
                         frame_samples, gray_levels, bin_snap):
    metadata = {
        "sample_rate": sampling_rate,
        "rows": rows,
        "columns": cols,
        "row_frequencies": row_frequencies.tolist(),

        "f_min": f_min,
        "f_max": f_max,

        "frame_duration": frame_duration,
        "frame_samples": frame_samples,

        "encoding_mode": "grayscale",          # was: "binary"
        "gray_levels": gray_levels,            # was: "active_pixel_value": 1 + "threshold"
        "bin_snap": bin_snap,
        "window": "hann",
        "frequency_mapping": "top_high to bottom_low"
        }

    with open("metadata.json", "w") as file:
        json.dump(metadata,file,indent=4)


if __name__ == "__main__":
    encode("images/pepsi.jpg", target_width=64, target_height=64, output_file='output_pepsi.wav')