import numpy as np
import hashlib

def derive_key(caller : str, receiver : str, pin: str):

    if(len(caller)!= 11 or len(receiver)!= 11):
        raise ValueError("Both numbers must be 11 digits long.")


    if(not caller.isdigit() or not receiver.isdigit() or not pin.isdigit()):
        raise ValueError("Both numbers and pin must contain only digits.")

    
    key = caller + "|" + receiver + "|" + pin
    # Convert to bytes as encryption functions typically require byte input
    encoded_key = key.encode('utf-8')

    # print(key)
    # print(type(key))
    # print(type(encoded_key))
    # print(encoded_key)

    return encoded_key

def scramble(data: bytes, activation):

    encoded_data = hashlib.sha256(data).digest()

    # using first 8 bytes of the hash to create a seed 
    # seed = int.from_bytes(encoded_data, 'big')
    # print(f"Seed: {seed}")
    # print(f"Encoded Data: {encoded_data.hex()}")

    encoded_rows = hashlib.sha256(encoded_data + b"rows").digest()
    encoded_cols = hashlib.sha256(encoded_data + b"cols").digest()

    col_seed = int.from_bytes(encoded_cols, 'big')
    row_seed = int.from_bytes(encoded_rows, 'big')

    # Random number generators for rows and columns
    row_rng = np.random.default_rng(row_seed)
    col_rng = np.random.default_rng(col_seed)

    rows, cols = activation.shape

    row_permutation = row_rng.permutation(rows)
    col_permutation = col_rng.permutation(cols)

    result = activation[row_permutation, :]
    result = result[:, col_permutation]

    return result

def encrypt(caller: str, receiver: str, pin: str, activation):

    encoded_key = derive_key(caller, receiver, pin)
    scrambled_activation = scramble(encoded_key, activation)

    return scrambled_activation

def generate_mask(length: int, caller: str, receiver: str, pin: str):

    encoded_key = derive_key(caller, receiver, pin)
    encoded_data = hashlib.sha256(encoded_key + b"mask").digest()

    # using first 8 bytes of the hash to create a seed 
    seed = int.from_bytes(encoded_data, 'big')

    rng = np.random.default_rng(seed)

    mask = rng.standard_normal(size=length)

    # [-1,1] er modhe scale kora
    peak = np.max(np.abs(mask))

    if peak > 0:
        mask = mask / peak

    return mask

if __name__ == "__main__":
    caller = "12345678901"
    receiver = "10987654321"
    pin = "1234"