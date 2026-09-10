from PIL import Image
import numpy as np

def preprocess_image(image_path, mode="L"):
    image = Image.open(image_path).convert(mode)
    img_format = image.mode
    height = image.height
    width = image.width

    # print(img_format)
    # print(height)
    # print(width)

    return image

# def convert_to_binary1(image, threshold=128):
#     binary_image = np.where(image >= threshold, 1, 0)
#     return binary_image

# def convert_to_binary2(image, threshold=128):
#     binary_image = np.where(image < threshold, 1, 0)
#     return binary_image


def resize_image(image, target_width= 16, target_height = 16, mode="L"):
    width, height = image.size

    scale = min(target_width / width, target_height / height)

    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))

    resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS) 

    # Lancz05 resampling algorithm , grayscale desampling er jonno good

    background = Image.new(mode, (target_width, target_height), 255)
    x_padding = (target_width - new_width) // 2
    y_padding = (target_height - new_height) // 2
    background.paste(resized_image, (x_padding, y_padding))

    return background



# grayscale activation: brightness -> amplitude

def to_activation(image_array, invert=True):
    # 0..255 grey -> 0.0..1.0 amplitude
    activation = image_array.astype(np.float64) / 255.0

    #   black(0)  -> 1.0 
    #   white(255)-> 0.0  
    if invert:
        activation = 1.0 - activation

    return activation


def quantize(activation, levels=16):
    # the paper uses G = 16 gray-tones; snap the amplitudes to that many steps.
    # pass levels=None to keep the full continuous grayscale.

    # 16 ta interval e vag kora amplitude 
    if not levels:
        return activation
    return np.round(activation * (levels - 1)) / (levels - 1)



def process_image(image_path, target_width=16, target_height=16, gray_levels=16, mode="L"):

    pre_image = preprocess_image(image_path, mode=mode)

    processed_image = resize_image(pre_image, target_width, target_height, mode)

    image_array = np.array(processed_image)

    # brightness -> amplitude, then quantise to G gray-tones
    activation = to_activation(image_array, invert=True)
    activation = quantize(activation, gray_levels)

    # print("After resize:", image_array.shape)
    # print("Unique gray levels:", np.unique(activation))

    return activation


# ----------------------------------------------------------------------------
# OLD binary entry point - replaced by process_gray above.
# ----------------------------------------------------------------------------
# def process(image_path, target_width=16, target_height=16, threshold=128):
#     grayscale_image = preprocess_image(image_path)
#     processed_image = resize_image(grayscale_image, target_width, target_height)
#     image_array = np.array(processed_image)
#     binary_image = covert_to_binary2(image_array, threshold=threshold)
#     return binary_image

if __name__ == "__main__":
    activation = process_image("images/pepsi.jpg", 64, 64)
    print("shape:", activation.shape)
    print("gray levels present:", np.unique(activation).size)