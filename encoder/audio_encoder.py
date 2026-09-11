import numpy as np
from scipy.io.wavfile import write
from image_preprocessor import process_image 
from security import encrypt, generate_mask
import json

def encode_activation_matrix(frame_samples, row_frequencies, activation_to_encode, t):

    rows = activation_to_encode.shape[0]
    cols = activation_to_encode.shape[1]
    # 1 column = 1 frame = frame_samples
    # each row now contributes a sine scaled by that pixel's brightness
    # x[n] = a1*sin(2*pi*f1*t) + a2*sin(2*pi*f2*t) + ...

    frames = []
    window = np.hanning(frame_samples)

    for idx in range(cols):

        # amplitude of every row for this column (0.0 = white, 1.0 = black)
        amp = activation_to_encode[:, idx]
        frame = np.zeros_like(t, dtype=np.float64)

        for row in range(rows):
            if amp[row] > 0:
                frame += amp[row] * np.sin(2 * np.pi * row_frequencies[row] * t)

        # sharp transitions = Spectral Leakage
        # A Hann window smoothly changes frame amplitude
        frame *= window
        frames.append(frame)


    final_audio = np.concatenate(frames)
    return final_audio

def encode(image_path, target_width=16, target_height=16,
           output_file='output.wav',
           sampling_rate=44100, f_min=1000, f_max=8000,
           frame_duration=0.1, gray_levels=16,
           bin_snap=True,
           security_enabled=False,
           caller=None,
           receiver=None,
           pin=None,
           alpha = 0.1,
           mode="L"):
            # was: ..., threshold=128):

    # brightness -> amplitude map (float 0.0..1.0), instead of a 0/1 binary image
    activation = process_image(image_path, target_width=target_width,
                              target_height=target_height, gray_levels=gray_levels,mode=mode)

    rows = activation.shape[0]
    cols = activation.shape[1]

    # f_max should be less than sampling_rate / 2 - Nyquist frequency
    if f_max >= sampling_rate / 2:
        raise ValueError("f_max should be less than sampling_rate/2")

    row_frequencies = np.linspace(f_max,f_min,rows)

    # frame_duration = 0.1
    frame_samples = int(sampling_rate*frame_duration)

    # linspace er karone 7888.88 emon freq o hote pare , but fft er karone 7888.88 er kono bin nai,
    # 7880 , 7890 emon ache so snap to nearest bin nahole spectral leakage hobe & neighbouring pixel k affect korbe - light grey theke dark grey hoye jete pare
    # tai 7888.88 k 7890 kora holo 
    if bin_snap:
        bin_width = sampling_rate / frame_samples
        row_frequencies = np.round(row_frequencies / bin_width) * bin_width

    n = np.arange(frame_samples)
    t = n / sampling_rate


    if mode == "L":
        if security_enabled:

            if caller is None or receiver is None or pin is None:
                raise ValueError("Caller, receiver and PIN are required.")
            
            activation_to_encode = encrypt(caller, receiver, pin, activation)

        else:
            activation_to_encode = activation

        final_audio = encode_activation_matrix(frame_samples, row_frequencies, activation_to_encode, t)

    elif mode == "RGB":
        # split the channels
        red = activation[:, :, 0]
        green = activation[:, :, 1]
        blue = activation[:, :, 2]

        if security_enabled:
            if caller is None or receiver is None or pin is None:
                raise ValueError("Caller, receiver and PIN are required.")
            
            red = encrypt(caller, receiver, pin, red)
            green = encrypt(caller, receiver, pin, green)
            blue = encrypt(caller, receiver, pin, blue)

        audio_R = encode_activation_matrix(frame_samples, row_frequencies, red, t)
        audio_G = encode_activation_matrix(frame_samples, row_frequencies, green, t)
        audio_B = encode_activation_matrix(frame_samples, row_frequencies, blue, t)

        
        final_audio = np.concatenate([audio_R, audio_G, audio_B])
        
    else:
        raise ValueError("Unsupported mode. Use 'L' or 'RGB'.")

    peak = np.max(np.abs(final_audio))

    # Normalization 

    
 
    if(security_enabled):
        target_peak = 0.5  # Lower peak nahole encrypt er sathe mile >1 hoye clip hoye jete pare
    else:
        target_peak = 0.8  # Higher peak for non-secure audio


    if peak > 0:
        normalization_gain = target_peak / peak
        final_audio = normalization_gain * final_audio
    else:
        normalization_gain = 1.0


    if security_enabled:
            
            audio_length = len(final_audio)
            mask = generate_mask(audio_length, caller, receiver, pin)
            
            # alpha = noise mask er strength 
            
            # y[n] = x[n] + alpha * m[n]
            final_audio = final_audio + (alpha * mask)

    write(output_file, sampling_rate, final_audio)

    create_metadata_json(sampling_rate, rows, cols,
                         row_frequencies, f_min, f_max, frame_duration,
                         frame_samples, gray_levels, bin_snap, security_enabled, alpha,mode, normalization_gain)


def create_metadata_json(sampling_rate, rows, cols, row_frequencies,
                         f_min, f_max, frame_duration,
                         frame_samples, gray_levels, bin_snap, security_enabled, alpha, mode, normalization_gain):
    metadata = {
        "sample_rate": sampling_rate,
        "rows": rows,
        "columns": cols,
        "row_frequencies": row_frequencies.tolist(),

        "f_min": f_min,
        "f_max": f_max,
        "alpha": alpha,
        "frame_duration": frame_duration,
        "frame_samples": frame_samples,
        "mode": mode,
        "normalization_gain": normalization_gain,

        "encoding_mode": "rgb" if mode == "RGB" else "grayscale",
        "gray_levels": gray_levels,            
        "bin_snap": bin_snap,
        "window": "hann",
        "security_enabled": security_enabled,
        "frequency_mapping": "top_high to bottom_low",

        "channels": 3 if mode == "RGB" else 1,
        "frames_per_channel": cols

        }

    with open("metadata.json", "w") as file:
        json.dump(metadata,file,indent=4)


if __name__ == "__main__":

    caller = "12345678901"
    receiver = "10987654321"
    pin = "1234"
    encode("images/pepsi.jpg", target_width=64, target_height=64,security_enabled=False, caller=caller, receiver=receiver, pin=pin, alpha = .5, output_file='output_pepsi.wav', mode="RGB")