import numpy as np
import json
from synchronizer import load_audio, synchronize


def load_metadata(metadata_path="metadata.json"):

    # this file is the key to the cipher - the decoder learns every constant
    # from here and never imports anything from the encoder
    with open(metadata_path) as file:
        metadata = json.load(file)

    return metadata


def frequencies_to_bins(row_frequencies, frame_samples, sample_rate):

    # f_max must be under the Nyquist frequency, same guard the encoder uses
    if max(row_frequencies) >= sample_rate / 2:
        raise ValueError("row frequencies should be less than sample_rate/2")

    # an N-point FFT of a signal sampled at f_s spreads its bins f_s/N apart
    # here 44100 / 4410 = 10.0 Hz per bin
    bin_width = sample_rate / frame_samples

    # k_r = argmin |f[k] - f_r| , which is just f_r rounded to the nearest bin
    bins = np.round(np.array(row_frequencies) / bin_width).astype(int)

    return bins


def split_into_frames(audio, frame_samples, columns):

    # the encoder wrote frames back to back with no overlap, so column c is
    # exactly samples [c*N, (c+1)*N) - one reshape lines them all up
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

    # one real FFT per column - because window == hop == 4410 this IS the STFT,
    # just without any of the guessing a generic STFT has to do
    # rfft only returns the positive frequencies, which is all we need
    spectrum = np.abs(np.fft.rfft(frames, axis=1))

    bins = frequencies_to_bins(row_frequencies, frame_samples, sample_rate)

    # keep only the bins our rows live in, then transpose so the matrix
    # comes out shaped like the image: (rows, columns)
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

    # split the cells into "tone was on" and "tone was off" at the halfway mark
    # the gap between the two averages is the margin the threshold lives in
    active = magnitude_matrix[magnitude_matrix >= magnitude_matrix.max() / 2]
    silent = magnitude_matrix[magnitude_matrix < magnitude_matrix.max() / 2]

    print("active cells average:", round(active.mean(), 3), "(expect about 26.9)")
    print("silent cells average:", round(silent.mean(), 6), "(expect about 0.001)")
