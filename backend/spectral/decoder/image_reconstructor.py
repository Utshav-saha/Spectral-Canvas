import os
import sys
import numpy as np
from PIL import Image
import io



_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

for _p in (
    _ROOT,
    os.path.join(_ROOT, "encoder"),
    _HERE
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


from synchronizer import load_audio, synchronize
from stft_decoder import load_metadata, decode
from decrypter import decrypt, remove_mask


def normalize(magnitude_matrix):
   
    lowest = magnitude_matrix.min()
    highest = magnitude_matrix.max()

    if highest == lowest:
        return np.zeros_like(magnitude_matrix)

    return (magnitude_matrix - lowest) / (highest - lowest)

def recover_activation(magnitude_matrix, metadata):
    """
    Recover the transmitted 0..1 activation amplitudes from FFT magnitudes.

    Encoder for one carrier:
        frame[n] = A * sin(...) * hann[n]

    Then the COMPLETE transmission was multiplied by:
        normalization_gain = g

    For a bin-centred sinusoid:
        |X[k]| approximately A * g * sum(hann) / 2

        A approximately 2*|X[k]| / (g*sum(hann))
    """
    gain = metadata.get("normalization_gain")

    if gain is None or gain == 0:
        return normalize(magnitude_matrix)

    frame_samples = metadata["frame_samples"]
    window = np.hanning(frame_samples)
    window_sum = np.sum(window)

    scale = gain * window_sum / 2.0

    if scale == 0:
        return np.zeros_like(magnitude_matrix)

    activation = magnitude_matrix / scale

    # Numerical leakage/error can produce tiny values outside the valid range.
    return np.clip(activation, 0.0, 1.0)


def quantize_activation(activation, levels=16):
    
    if not levels:
        return activation

    return (np.round(activation * (levels - 1))/ (levels - 1))


def activation_to_pixels(activation, gray_levels=16):
    """
    Encoder used:
        activation = 1 - pixel/255

    Therefore:
        pixel = 255*(1 - activation)

    This works for an L channel and individually for R/G/B.
    """
    activation = quantize_activation(activation,gray_levels)

    pixels = ((1.0 - activation) * 255.0)

    return np.clip(np.rint(pixels),0,255).astype(np.uint8)


def to_image_array(activation, gray_levels=16):
    """The same 0..1 -> 0..255 map the decoder applies, run on the SENT
    activation. That gives the reference to measure a recovered image against,
    so the comparison is scheme against scheme and not against the quantiser."""
    return activation_to_pixels(activation, gray_levels)


def split_rgb_audio(audio, metadata):
    
    columns = metadata["columns"]
    frame_samples = metadata["frame_samples"]

    channel_samples = metadata.get("samples_per_channel",columns * frame_samples)

    needed = 3 * channel_samples

    if len(audio) < needed:
        raise ValueError(f"RGB audio is too short: got {len(audio)} samples, "f"need {needed}.")

    audio_R = audio[0:channel_samples]
    audio_G = audio[channel_samples:2 * channel_samples]
    audio_B = audio[2 * channel_samples:3 * channel_samples]

    return audio_R, audio_G, audio_B


def save_image(image_array, output_file="recovered.png"):
    if image_array.ndim == 2:
        mode = "L"
    elif (image_array.ndim == 3 and image_array.shape[2] == 3):
        mode = "RGB"
    else:
        raise ValueError(f"Unsupported reconstructed image shape: {image_array.shape}")

    Image.fromarray(image_array,mode=mode).save(output_file)


def image_mae(recovered, source):
    
    return np.mean(np.abs(recovered.astype(np.float64)- source.astype(np.float64)))

def gray_error(recovered_gray, source_gray):
    return image_mae(recovered_gray,source_gray)

def check_credentials(security_enabled,decrypt_enabled,caller,receiver,pin):
    if security_enabled and decrypt_enabled:
        if (caller is None or receiver is None or pin is None):
            raise ValueError(
                "Caller, receiver and PIN are required "
                "to decode a secured file."
            )


def reconstruct(audio, metadata, caller=None, receiver=None, pin=None, decrypt_enabled=False):
    # metadata = load_metadata(metadata_path)

    # sample_rate, audio = load_audio(wav_path)

    # if sample_rate != metadata["sample_rate"]:
    #     raise ValueError(
    #         f"WAV sample rate is {sample_rate}, "
    #         f"metadata says {metadata['sample_rate']}."
    #     )

    channels = metadata.get("channels", 1)
    mode = metadata.get("mode","RGB" if channels == 3 else "L")

    security_enabled = metadata.get("security_enabled",False)

    check_credentials(security_enabled,decrypt_enabled,caller,receiver,pin)

    aligned = synchronize(audio,metadata["frame_samples"],metadata["columns"],channels=channels)

    if security_enabled and decrypt_enabled:
        aligned = remove_mask(aligned,caller,receiver,pin,alpha=metadata.get("alpha", 0.1))

    gray_levels = metadata.get("gray_levels",16)


    if mode == "L" or channels == 1:
        magnitude = decode(aligned,metadata)

        activation = recover_activation(magnitude,metadata)

        if security_enabled and decrypt_enabled:
            activation = decrypt(caller,receiver,pin,activation)

        image = activation_to_pixels(activation,gray_levels)

  
    elif mode == "RGB" and channels == 3:
        audio_R, audio_G, audio_B = split_rgb_audio(aligned,metadata)

        # FFT decoding: one call per colour channel.
        mag_R = decode(audio_R, metadata)
        mag_G = decode(audio_G, metadata)
        mag_B = decode(audio_B, metadata)

        
        act_R = recover_activation(mag_R, metadata)
        act_G = recover_activation(mag_G, metadata)
        act_B = recover_activation(mag_B, metadata)

        # Undo the same spatial permutation independently on each channel.
        if security_enabled and decrypt_enabled:
            act_R = decrypt(caller, receiver, pin, act_R)
            act_G = decrypt(caller, receiver, pin, act_G)
            act_B = decrypt(caller, receiver, pin, act_B)

        # Activation -> actual R/G/B pixel values.
        red = activation_to_pixels(act_R,gray_levels)
        green = activation_to_pixels(act_G,gray_levels)
        blue = activation_to_pixels(act_B,gray_levels)

        image = np.stack([red, green, blue],axis=-1)

    else:
        raise ValueError(
            f"Unsupported decoder configuration: "
            f"mode={mode}, channels={channels}"
        )

    # save_image(image,output_file)

    return image


def to_png_bytes(image_array, scale=8):
    image = Image.fromarray(image_array,
                            mode="RGB" if image_array.ndim == 3 else "L")
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale),
                             Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

def mean_absolute_error(recovered, source):
    return float(np.mean(np.abs(recovered.astype(int) - source.astype(int))))


def mse(recovered, source):
    return float(np.mean((recovered.astype(float) - source.astype(float)) ** 2))


def psnr(recovered, source):
    error = mse(recovered, source)
    if error == 0:
        return float("inf")
    return float(10 * np.log10((255.0 ** 2) / error))



if __name__ == "__main__":

    from audio_encoder import encode
    from image_preprocessor import process_image

    IMG = "images/pepsi.jpg"
    N = 64
    MODE = "RGB"
    SECURED = True

    caller = "12345678901"
    receiver = "10987654321"
    pin = "1234"

    encode(IMG,target_width=N,target_height=N,mode=MODE,security_enabled=SECURED,caller=caller,receiver=receiver,pin=pin,alpha=0.5,output_file="output_pepsi.wav")

    recovered = reconstruct("output_pepsi.wav","metadata.json",output_file="recovered.png",caller=caller,receiver=receiver,pin=pin,decrypt_enabled=SECURED)

    metadata = load_metadata("metadata.json")

    source_activation = process_image(IMG,N,N,metadata.get("gray_levels", 16),mode=MODE)

    source_image = ((1.0 - source_activation)* 255.0)

    source_image = np.clip(np.rint(source_image),0,255).astype(np.uint8)

    print(
        "mode:",
        metadata.get("mode")
    )

    print(
        "channels:",
        metadata.get("channels")
    )

    print(
        "recovered shape:",
        recovered.shape
    )

    print(
        "image MAE:",
        round(
            image_mae(
                recovered,
                source_image
            ),
            4
        )
    )

    print(
        "saved recovered.png"
    )
