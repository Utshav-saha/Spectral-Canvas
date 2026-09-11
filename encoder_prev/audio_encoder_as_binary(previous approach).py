import numpy as np
from scipy.io.wavfile import write
from image_preprocessor import process
import json

def encode(image_path, target_width=16, target_height=16, output_file='output.wav',
            sampling_rate=44100, f_min=1000, f_max=8000,
             frame_duration=0.1, threshold=128):

    binary_image = process(image_path, target_width=target_width, target_height=target_height, threshold=threshold)
    rows = binary_image.shape[0]
    cols = binary_image.shape[1]

    # f_max should be less than sampling_rate / 2 - Nyquist frequency
    if f_max >= sampling_rate / 2:
        raise ValueError("f_max should be less than sampling_rate/2")

    row_frequencies = np.linspace(f_max,f_min,rows)

    # print(row_frequencies[0])
    # print(row_frequencies[-1])
    # print(row_frequencies[1]- row_frequencies[0])

    frame_duration = 0.1
    frame_samples = int(sampling_rate*frame_duration)

    n = np.arange(frame_samples)
    t = n / sampling_rate

    # 1 column = 1 frame = frame_samples
    # binary image er prottek row er jonno column er jekhane jekhane 1 ogula jog hobe 
    # like x[n] = Asin(2 * pi * f1 * t) + Asin(2 * pi * f2 * t) + ...

    frames = []
    window = np.hanning(frame_samples)

    for idx in range(cols):

        col = binary_image[:, idx]
        one_rows = np.where(col == 1)[0]
        frame = np.zeros_like(t, dtype=np.float64)

        for row in one_rows:

            freq = row_frequencies[row]
            frame += np.sin(2 * np.pi * freq * t)

        # sharp transitions = Spectral Leakage
        # A Hann window smoothly changes frame amplitude
        frame *= window
        frames.append(frame)

    final_audio = np.concatenate(frames)

    peak = np.max(np.abs(final_audio))
    # print(peak)

    # Normalization 
    
    if peak > 0:
        final_audio = 0.8 * final_audio / peak

    # print(np.max(np.abs(final_audio)))
    
    write(output_file, sampling_rate, final_audio)
    # print(binary_image.shape)
    # print(np.unique(binary_image))

    
    create_metadata_json(sampling_rate, rows, cols, 
                         row_frequencies, f_min, f_max, frame_duration, 
                         frame_samples, threshold)


def create_metadata_json(sampling_rate, rows, cols, row_frequencies, 
                         f_min, f_max, frame_duration, 
                         frame_samples, threshold):
    metadata = {
        "sample_rate": sampling_rate,
        "rows": rows,
        "columns": cols,
        "row_frequencies": row_frequencies.tolist(),
    
        "f_min": f_min,
        "f_max": f_max,
    
        "frame_duration": frame_duration,
        "frame_samples": frame_samples,
    
        "encoding_mode": "binary",
        "window": "hann",
    
        "active_pixel_value": 1,
        "threshold": threshold,
        "frequency_mapping": "top_high to bottom_low"
        }
    
    with open("metadata.json", "w") as file:
        json.dump(metadata,file,indent=4)


if __name__ == "__main__":
    encode("images/black1.png", target_width=64, target_height=64, output_file='output.wav',)