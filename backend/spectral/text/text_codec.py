

def text_to_data(message: str):
    encoded = message.encode("utf-8")

    data = []

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