import numpy as np 

# We are implementing Multiple Frequency-Shift Keying
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


def data_to_text(data):
    bytes_list = []

    for upper, lower in data:
        byte = (upper << 4) | lower
        bytes_list.append(byte)

    return bytes(bytes_list).decode("utf-8")


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


def frequency_to_audio(frequency, fs=44100, Ts=0.05):

    # Synthesis equation actually
    N = int(fs * Ts)
    n = np.arange(N)
    t = n / fs
    samples = np.sin(2 * np.pi * frequency * t)

    return samples


def tone_to_freq(tone, fs = 44100, Ts = 0.05):

    # And this is analysis equation 
    N = int(fs * Ts)
    spectrum = np.abs(np.fft.rfft(tone))

    bin_width = fs / N
    num_bins = (N//2)+ 1

    freq_axis = np.arange(num_bins) * bin_width

    peak_bin = np.argmax(spectrum)
    peak_frequency = freq_axis[peak_bin]

    return peak_frequency


def transmit(mapped_freqs, fs=44100, Ts=0.05):
    frames = []
    
    # using Hann window as in image_encoder.py to reduce spectral leakage
    window = np.hanning(int(fs * Ts))
    
    for freq in mapped_freqs:
        frame = frequency_to_audio(freq, fs, Ts)
        
        # smooth the edges
        frame *= window
        
        frames.append(frame)
        
    return np.concatenate(frames)


def receive(audio, fs=44100, Ts=0.05):
    N = int(fs * Ts)
    total_frames = len(audio) // N
    
    recovered_freqs = []
    
    for i in range(total_frames):
        start = i * N
        end = start + N
        chunk = audio[start:end]
        
        freq = tone_to_freq(chunk, fs, Ts)
        recovered_freqs.append(freq)
        
    return recovered_freqs



if __name__ == "__main__":
    
    original_message = "Hello, Audio World!"
    print(f"Original Message: '{original_message}'")
    
    # --- TRANSMITTER SIDE ---
    data = text_to_data(original_message)
    freqs = bits_to_frequencies(data)
    audio_track = transmit(freqs)
    
    print(f"Generated audio track with {len(audio_track)} samples.")
    
   
    # --- RECEIVER SIDE ---
    recovered_freqs = receive(audio_track)
    recovered_data = frequencies_to_bits(recovered_freqs)
    recovered_message = data_to_text(recovered_data)
    
    print(f"Recovered Message:  '{recovered_message}'")
    
    if original_message == recovered_message:
        print("\nSUCCESS! The transmission round-trip is perfect.")
    else:
        print("\nFAILURE! Something got lost in translation.")