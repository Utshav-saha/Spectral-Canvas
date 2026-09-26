import numpy as np
import json
from synchronizer import load_audio, synchronize


def load_metadata(metadata_path="metadata.json"):

    with open(metadata_path) as file:
        metadata = json.load(file)

    return metadata


def frequencies_to_bins(row_frequencies, frame_samples, sample_rate):

    # f_max-ke Nyquist frequency-r niche thakte hobe, encoder-o eki guard use kore
    if max(row_frequencies) >= sample_rate / 2:
        raise ValueError("row frequencies should be less than sample_rate/2")

    
    # ekhane 44100 / 4410 = proti bin-e 10.0 Hz
    bin_width = sample_rate / frame_samples

    #f_r-ke kachakachi bin-e round kora
    bins = np.round(np.array(row_frequencies) / bin_width).astype(int)

    return bins


def split_into_frames(audio, frame_samples, columns):

    # encoder frame-gulo kono overlap chara por por ache, tai column c holo
    needed = columns * frame_samples

    if len(audio) < needed:
        raise ValueError( f"Audio channel is too short: got {len(audio)} samples, "f"need at least {needed}.")

    frames = audio[:needed].reshape(columns, frame_samples)

    return frames


def decode(audio, metadata):

    sample_rate = metadata["sample_rate"]
    columns = metadata["columns"]
    frame_samples = metadata["frame_samples"]
    row_frequencies = metadata["row_frequencies"]

    frames = split_into_frames(audio, frame_samples, columns)

    # proti column-e ekta kore real FFT - window == hop == 4410 
    # rfft shudhu positive frequency return kore, otai dorkar
    spectrum = np.abs(np.fft.rfft(frames, axis=1))

    bins = frequencies_to_bins(row_frequencies, frame_samples, sample_rate)

    # shudhu amader row-gulo je bin-e ache segulo tarpor transpose jate
    # matrix image er moto hoy: (rows, columns)
    magnitude_matrix = spectrum[:, bins].T

    return magnitude_matrix


if __name__ == "__main__":

    metadata = load_metadata("metadata.json")

    sample_rate, audio = load_audio("black1.wav")
    aligned = synchronize(audio, metadata["frame_samples"], metadata["columns"])

    magnitude_matrix = decode(aligned, metadata)

    print("matrix shape:", magnitude_matrix.shape, "(expect (64, 64))")
    print("loudest cell:", round(magnitude_matrix.max(), 3))
    print("quietest cell:", round(magnitude_matrix.min(), 6))

    active = magnitude_matrix[magnitude_matrix >= magnitude_matrix.max() / 2]
    silent = magnitude_matrix[magnitude_matrix < magnitude_matrix.max() / 2]

    print("active cells average:", round(active.mean(), 3), "(expect about 26.9)")
    print("silent cells average:", round(silent.mean(), 6), "(expect about 0.001)")
