import numpy as np 
# amra 16 ta frequency use korbo, so 4 ta bit represent kora jabe ekta freq diye
# encoded byte = 8 bit , tai dui part e vag kore ogulo freq diye represent kora hobe


def text_to_data(message: str):

    # byte e convert
    encoded = message.encode("utf-8")

    data = []

    # prottek byte k 2 ta 4-bit value e convert 
    for byte in encoded:
        upper = byte >> 4
        lower = byte & 0x0F

        data.append((upper, lower))

    return data


def bits_to_frequencies(data, fs=44100, Ts=0.05, f_min=2000, f_max=5000, bits=4):
    
    # samples per symbol
    N = int(fs * Ts)
    
    #16 carrier frequencies
    freqs = np.linspace(f_min, f_max, 2**bits)

    # snap to nearest FFT bin to prevent spectral leakage, same as in audio_encoder.py
    bin_width = fs / N
    freqs = np.round(freqs / bin_width) * bin_width

    mapped_freqs = []
    # Map the upper and lower nibbles to their physical frequencies
    for upper, lower in data:
        mapped_freqs.append(freqs[upper])
        mapped_freqs.append(freqs[lower])

    return mapped_freqs

def frequencies_to_bits(frequencies, fs=44100, Ts=0.05, f_min=2000, f_max=5000, bits=4):
    
    # samples per symbol
    N = int(fs * Ts)
    
    #16 carrier frequencies
    freqs = np.linspace(f_min, f_max, 2**bits)

    # snap to nearest FFT bin to prevent spectral leakage, same as in audio_encoder.py
    bin_width = fs / N
    freqs = np.round(freqs / bin_width) * bin_width

    data = []
    for i in range(0, len(frequencies), 2):
        upper_freq = frequencies[i]
        lower_freq = frequencies[i + 1]

        # kon frequency ta nearest kon bin e ache ta ber kora
        upper_index = np.argmin(np.abs(freqs - upper_freq))
        lower_index = np.argmin(np.abs(freqs - lower_freq))

        data.append((upper_index, lower_index))

    return data


def data_to_text(data):
    bytes_list = []

    for upper, lower in data:
        byte = (upper << 4) | lower
        bytes_list.append(byte)

    return bytes(bytes_list).decode("utf-8")