import numpy as np
from scipy.io.wavfile import read


def load_audio(wav_path):

    # read() returns the sample rate and the samples, dtype matching the file
    # (our encoder writes float64, a microphone recording is usually int16)
    sample_rate, audio = read(wav_path)

    # a stereo recording comes back as (samples, 2) - average it down to mono
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # int16 samples run from -32768 to 32767, so scale them into the
    # same +-1.0 range the encoder wrote
    if audio.dtype.kind == "i":
        audio = audio / np.iinfo(audio.dtype).max

    return sample_rate, audio.astype(np.float64)


def frame_energy(audio, frame_samples):

    # how many whole frames fit in this audio
    frames = len(audio) // frame_samples

    # cut the audio into non-overlapping blocks of frame_samples
    blocks = audio[:frames * frame_samples].reshape(frames, frame_samples)

    # RMS = root mean square = how loud each block is
    energy = np.sqrt(np.mean(blocks ** 2, axis=1))

    return energy


def find_start_by_energy(audio, frame_samples, energy_ratio=0.1):

    energy = frame_energy(audio, frame_samples)

    # a completely silent file has nothing to find
    if len(energy) == 0 or energy.max() == 0:
        return 0

    # anything above 10% of the loudest block counts as "signal, not silence"
    loud_blocks = np.where(energy >= energy_ratio * energy.max())[0]

    # the first loud block is where the picture begins
    start = loud_blocks[0] * frame_samples

    return start


def find_start_by_correlation(audio, reference):

    # slide the known sync waveform along the recording and multiply
    # the overlap at every position - the match peaks where they line up
    correlation = np.correlate(audio, reference, mode="valid")

    # the position of the biggest peak is where the sync tone starts
    start = int(np.argmax(np.abs(correlation)))

    return start


def synchronize(audio, frame_samples, columns, energy_ratio=0.1):

    # how many samples the picture actually occupies
    needed = columns * frame_samples

    # a WAV we encoded ourselves is exactly this long, so sample 0 is column 0
    # and no synchronisation is needed at all
    if len(audio) == needed:
        start = 0
    else:
        # a recording starts with unknown silence, so hunt for the first loud frame
        start = find_start_by_energy(audio, frame_samples, energy_ratio)

    aligned = audio[start:start + needed]

    # a truncated or corrupt file must not crash the decoder - pad it with silence
    if len(aligned) < needed:
        aligned = np.concatenate([aligned, np.zeros(needed - len(aligned))])

    return aligned


if __name__ == "__main__":

    sample_rate, audio = load_audio("black1.wav")

    print("sample rate:", sample_rate)
    print("samples:", len(audio), "(expect 282240 = 64 x 4410)")
    print("peak:", np.max(np.abs(audio)), "(expect 0.8)")
    print("detected start:", find_start_by_energy(audio, 4410), "(expect 0)")

    aligned = synchronize(audio, 4410, 64)
    print("aligned length:", len(aligned), "(expect 282240)")
