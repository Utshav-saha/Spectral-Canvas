from PIL import Image, ImageDraw, ImageFont, ImageOps
import numpy as np
import io
import base64

def preprocess_image(image_path, mode="L"):
    
    if isinstance(image_path, Image.Image):
        image = image_path.convert(mode)
    else:
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

    # image onek choto hole round kore 1 ta dimension e 0 hoye jabe, tai max diye 1 ta dimension ke minimum 1 rakhte holo 
    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))

    resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS) 

    # Lancz05 resampling algorithm , grayscale resampling er jonno good

    if mode == "RGB":
        bg_color = (255, 255, 255)
    else:
        bg_color = 255

    # white bg er majhkhane image paste 
    background = Image.new(mode, (target_width, target_height), bg_color)
    x_padding = (target_width - new_width) // 2
    y_padding = (target_height - new_height) // 2
    background.paste(resized_image, (x_padding, y_padding))

    return background



# grayscale activation: brightness -> amplitude

def to_activation(image_array, invert=True):
    # 0..255 grey -> 0.0..1.0 amplitude
    activation = image_array.astype(np.float64) / 255.0

    # black(0)  -> 1.0 
    # white(255)-> 0.0  
    if invert:
        activation = 1.0 - activation

    # white bg padding pixel gulake 0.0 amplitude e convert kora holo, jate white bg er karone unwanted sound na hoy

    return activation


def quantize(activation, levels=16):
    # the paper uses G = 16 gray-tones; snap the amplitudes to that many steps.
    # pass levels=None to keep the full continuous grayscale.

    # 16 ta interval e vag kora amplitude , infinite gray shade --> 16 ta gray shade 
    if not levels:
        return activation
    return np.round(activation * (levels - 1)) / (levels - 1)



def stretch_contrast(image, cutoff=1):
    """Spread the histogram over the full 0..255 range before quantising.

    A photograph rarely uses the whole range - a pale cat on a pale floor sits
    in a narrow band of greys. Quantising that to a handful of levels throws
    most of the subject away, and at 4 levels it stops looking like anything.
    Stretching first means every level in the budget carries information.

    preserve_tone keeps the three channels scaled together, so a colour picture
    does not pick up a cast.
    """
    if image.mode == "RGB":
        return ImageOps.autocontrast(image, cutoff=cutoff, preserve_tone=True)
    return ImageOps.autocontrast(image, cutoff=cutoff)


def process_image(image_path, target_width=16, target_height=16, gray_levels=16,
                  mode="L", autocontrast=False):

    pre_image = preprocess_image(image_path, mode=mode)

    # Before the resize, so the stretch is computed from every pixel rather
    # than from whichever survived the downsample.
    if autocontrast:
        pre_image = stretch_contrast(pre_image)

    processed_image = resize_image(pre_image, target_width, target_height, mode)

    image_array = np.array(processed_image)

    # brightness -> amplitude, then quantise to G gray-tones
    activation = to_activation(image_array, invert=True)
    activation = quantize(activation, gray_levels)

    # print("After resize:", image_array.shape)
    # print("Unique gray levels:", np.unique(activation))

    return activation



# def process(image_path, target_width=16, target_height=16, threshold=128):
#     grayscale_image = preprocess_image(image_path)
#     processed_image = resize_image(grayscale_image, target_width, target_height)
#     image_array = np.array(processed_image)
#     binary_image = covert_to_binary2(image_array, threshold=threshold)
#     return binary_image




def activation_to_png_bytes(activation, gray_levels=16, scale=8):
    """Processed source -> a PNG preview the frontend can show next to the result."""

    # reverse activation -> brightness, then scale to 0..255
    if gray_levels:
        activation = np.round(activation * (gray_levels - 1)) / (gray_levels - 1)
    array = ((1.0 - activation) * 255.0).astype(np.uint8)

    # numpy array to image 
    image = Image.fromarray(array, mode="RGB" if array.ndim == 3 else "L")
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale),
                             Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Text -> image
# --------------------------------------------------------------------------

def _load_font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/System/Library/Fonts/Menlo.ttc",
                 "C:\\Windows\\Fonts\\consolab.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_text_image(text, target_width=64, target_height=64, font_size=None):
    """Lay short text out as a high-contrast black-on-white image.

    Wraps greedily to fit the aspect ratio rather than letting one long line
    shrink to nothing.
    """
    text = (text or "").strip()
    if not text:
        text = " "

    canvas_w, canvas_h = target_width * 12, target_height * 12
    lines = text.splitlines() or [text]

    # greedy wrap so long single lines don't become unreadable slivers
    max_chars = max(8, int(len(max(lines, key=len)) ** 0.5 * 3))
    wrapped = []
    for line in lines:
        while len(line) > max_chars:
            cut = line.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            wrapped.append(line[:cut])
            line = line[cut:].lstrip()
        wrapped.append(line)
    wrapped = [w for w in wrapped if w != ""] or [" "]

    size = font_size or max(10, int(canvas_h / (len(wrapped) * 1.6)))
    font = _load_font(size)

    image = Image.new("L", (canvas_w, canvas_h), 255)
    draw = ImageDraw.Draw(image)

    line_h = size * 1.25
    total_h = line_h * len(wrapped)
    y = (canvas_h - total_h) / 2

    for line in wrapped:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
        except Exception:
            w = len(line) * size * 0.6
        draw.text(((canvas_w - w) / 2, y), line, fill=0, font=font)
        y += line_h

    return ImageOps.invert(ImageOps.invert(image))


def decode_data_url(data_url: str) -> bytes:
    """'data:image/png;base64,iVBOR...' -> raw PNG bytes (for the doodle canvas)."""
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


if __name__ == "__main__":
    activation = process_image("images/pepsi.jpg", 64, 64)
    print("shape:", activation.shape)
    print("gray levels present:", np.unique(activation).size)