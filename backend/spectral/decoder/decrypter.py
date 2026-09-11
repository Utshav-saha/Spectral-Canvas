import importlib.util
import sys
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# spec = importlib.util.spec_from_file_location(module_name, module_path)
# security = importlib.util.module_from_spec(spec)
# sys.modules[module_name] = security
# spec.loader.exec_module(security)   

# ======================== ABOVE IS IMPORTING SECURITY MODULE ================
import numpy as np
import hashlib
from common.security import derive_key, generate_mask


def unscramble(data: bytes, scrambled_activation):

    # identical hash chain to security.scramble() - same key, same hashes,
    # same seeds, so the two RNGs regenerate the exact same permutations
    encoded_data = hashlib.sha256(data).digest()

    encoded_rows = hashlib.sha256(encoded_data + b"rows").digest()
    encoded_cols = hashlib.sha256(encoded_data + b"cols").digest()

    row_seed = int.from_bytes(encoded_rows, 'big')
    col_seed = int.from_bytes(encoded_cols, 'big')

    row_rng = np.random.default_rng(row_seed)
    col_rng = np.random.default_rng(col_seed)

    rows, cols = scrambled_activation.shape

    row_permutation = row_rng.permutation(rows)
    col_permutation = col_rng.permutation(cols)

    # scramble() did activation[row_permutation, :][:, col_permutation]
    # argsort of a permutation is its inverse: applying it undoes the reorder
    inv_rows = np.argsort(row_permutation)
    inv_cols = np.argsort(col_permutation)

    result = scrambled_activation[inv_rows, :]
    result = result[:, inv_cols]

    return result

def remove_mask(audio, caller: str, receiver: str, pin: str, alpha: float = 0.1):

    # y[n] = x[n] + alpha * m[n]  ->  x[n] = y[n] - alpha * m[n]
    # generate_mask is seeded from the same caller|receiver|pin key, so calling
    # it with the same length regenerates the identical noise the encoder added
    # - this is an exact cancellation, not a denoising approximation
    mask = generate_mask(len(audio), caller, receiver, pin)

    return audio - (alpha * mask)

def decrypt(caller: str, receiver: str, pin: str, scrambled_activation):

    # same caller/receiver/pin the encoder was given -> same key -> same
    # permutations -> unscramble() puts every row and column back in place
    encoded_key = derive_key(caller, receiver, pin)
    activation = unscramble(encoded_key, scrambled_activation)

    return activation