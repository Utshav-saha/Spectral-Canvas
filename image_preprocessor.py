from PIL import Image
import numpy as np

def preprocess_image(image_path):
    image = Image.open(image_path).convert("L")
    img_format = image.mode
    height = image.height
    width = image.width

    print(img_format)
    print(height)
    print(width)

    return image

def covert_to_binary1(image, threshold=128):
    binary_image = np.where(image >= threshold, 1, 0)
    return binary_image

def covert_to_binary2(image, threshold=128):
    binary_image = np.where(image < threshold, 1, 0)
    return binary_image

def resize_image(image, target_width= 16, target_height = 16):
    width, height = image.size

    scale = min(target_width / width, target_height / height)

    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))

    resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS) 

    # Lancz05 resampling algorithm , grayscale desampling er jonno good

    background = Image.new("L", (target_width, target_height), 255)
    x_padding = (target_width - new_width) // 2
    y_padding = (target_height - new_height) // 2
    background.paste(resized_image, (x_padding, y_padding))

    return background

def main():

    image_path = "cat2.jpg"

    grayscale_image = preprocess_image(image_path)

    processed_image = resize_image(
        grayscale_image,64,64)

    image_array = np.array(processed_image)

    print("After resize:")
    print(image_array.shape)

    binary_image = covert_to_binary2(image_array,threshold=128)

    print("Binary shape:")
    print(binary_image.shape)

    print("Unique values:")
    print(np.unique(binary_image))

    print(binary_image)
    np.savetxt("binary_image.txt", binary_image, fmt="%d")


if __name__ == "__main__":
    main()
