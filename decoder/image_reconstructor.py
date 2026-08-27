import sys, os
# make sibling packages importable no matter where this script is run from
_HERE = os.path.dirname(os.path.abspath(__file__))          # .../decoder
_ROOT = os.path.dirname(_HERE)                               # project root
for _p in (_ROOT, os.path.join(_ROOT, "encoder"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
from PIL import Image
from synchronizer import load_audio, synchronize
from stft_decoder import load_metadata, decode
from decrypter import decrypt, remove_mask


def normalize(magnitude_matrix):

    lowest = magnitude_matrix.min()
    highest = magnitude_matrix.max()

    # a completely silent file would make us divide by zero
    if highest == lowest:
        return np.zeros_like(magnitude_matrix)

    # min-max stretch: quietest cell becomes 0.0, loudest becomes 1.0
    normalized = (magnitude_matrix - lowest) / (highest - lowest)

    return normalized


# ----------------------------------------------------------------------------
# GRAYSCALE mapping: recovered magnitude -> grey level.
# FFT magnitude is linear in tone amplitude, so after the min-max stretch the
# normalised value IS the pixel's brightness - just invert it back to grey.
# ----------------------------------------------------------------------------
def to_gray_image(normalized, gray_levels=16):

    # snap to the same G tones the encoder used (pass gray_levels=None to skip)
    if gray_levels:
        normalized = np.round(normalized * (gray_levels - 1)) / (gray_levels - 1)

    # loud(1.0) -> dark(0), silent(0.0) -> white(255); matches the encoder's invert
    gray = ((1.0 - normalized) * 255.0).astype(np.uint8)

    return gray


# ----------------------------------------------------------------------------
# BINARY thresholding - no longer used in the grayscale pipeline. Kept for
# reference; to_gray_image replaces both of these.
# ----------------------------------------------------------------------------
# def apply_threshold(normalized, threshold=0.2):
#     binary_image = np.where(normalized >= threshold, 1, 0)
#     return binary_image
#
# def otsu_threshold(normalized):
#     counts, edges = np.histogram(normalized, bins=256, range=(0.0, 1.0))
#     centres = (edges[:-1] + edges[1:]) / 2
#     weight_below = np.cumsum(counts)
#     weight_above = weight_below[-1] - weight_below
#     total_below = np.cumsum(counts * centres)
#     mean_below = np.divide(total_below, weight_below,
#                            out=np.zeros(256), where=weight_below > 0)
#     mean_above = np.divide(total_below[-1] - total_below, weight_above,
#                            out=np.zeros(256), where=weight_above > 0)
#     between_variance = weight_below * weight_above * (mean_below - mean_above) ** 2
#     return centres[np.argmax(between_variance)]


def save_image(gray_image, output_file="recovered.png"):

    # gray_image already holds 0..255 grey values from to_gray_image, so just save
    Image.fromarray(gray_image, mode="L").save(output_file)

    # was (binary): repaint 1->black, 0->white before saving
    # viewable = np.where(binary_image == 1, 0, 255).astype(np.uint8)
    # Image.fromarray(viewable, mode="L").save(output_file)


def gray_error(recovered_gray, source_gray):

    # mean absolute error in grey levels (0..255); lower is better
    return np.mean(np.abs(recovered_gray.astype(int) - source_gray.astype(int)))


# def pixel_accuracy(recovered, source):
#     # binary metric - fraction of pixels exactly right. Replaced by gray_error.
#     return np.mean(recovered == source)


def reconstruct(wav_path, metadata_path="metadata.json",
                output_file="recovered.png",
                caller=None, receiver=None, pin=None, decrypt_enabled=False):

    metadata = load_metadata(metadata_path)
    sample_rate, audio = load_audio(wav_path)
    aligned = synchronize(audio, metadata["frame_samples"], metadata["columns"])

    # audio-domain: strip the noise mask BEFORE the FFT sees it
    if decrypt_enabled:
        if metadata.get("security_enabled", True):
            if caller is None or receiver is None or pin is None:
                raise ValueError("Caller, receiver and PIN are required to decode a secured file.")
            aligned = remove_mask(aligned, caller, receiver, pin, alpha=metadata.get("alpha", 0.1))

    magnitude_matrix = decode(aligned, metadata)
    normalized = normalize(magnitude_matrix)

    # pixel-domain: undo the row/column scramble
    if decrypt_enabled:
        if metadata.get("security_enabled", True):
            normalized = decrypt(caller, receiver, pin, normalized)

    gray_image = to_gray_image(normalized, metadata.get("gray_levels", 16))
    save_image(gray_image, output_file)
    return gray_image


if __name__ == "__main__":

    from audio_encoder import encode
    from image_preprocessor import process_gray

    IMG, N = "images/pepsi.jpg", 64
    caller, receiver, pin = "12345678901", "10987654321", "1234"

    encode(IMG, target_width=N, target_height=N, security_enabled=True,
           caller=caller, receiver=receiver, pin=pin, alpha = .6, output_file="output_pepsi.wav")

    recovered = reconstruct("output_pepsi.wav", "metadata.json",
                             output_file="recovered.png",
                             caller=caller, receiver=receiver, pin=pin, decrypt_enabled=True)

    # ground truth grey image, straight from the preprocessor
    metadata = load_metadata("metadata.json")
    source_activation = process_gray(IMG, N, N, metadata["gray_levels"])
    source_gray = ((1.0 - source_activation) * 255.0).astype(np.uint8)

    print("grey MAE (expect 0.0):", round(gray_error(recovered, source_gray), 4))
    print("levels recovered:", np.unique(recovered).size, "of", metadata["gray_levels"])
    print("saved recovered.png")