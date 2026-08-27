import importlib.util
import sys

module_path = './encoder/security.py'
module_name = 'security'

spec = importlib.util.spec_from_file_location(module_name, module_path)
security = importlib.util.module_from_spec(spec)
sys.modules[module_name] = security
spec.loader.exec_module(security)   

# ======================== ABOVE IS IMPORTING SECURITY MODULE ================
import numpy as np
import hashlib
from security import derive_key


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


def decrypt(caller: str, receiver: str, pin: str, scrambled_activation):

    # same caller/receiver/pin the encoder was given -> same key -> same
    # permutations -> unscramble() puts every row and column back in place
    encoded_key = derive_key(caller, receiver, pin)
    activation = unscramble(encoded_key, scrambled_activation)

    return activation