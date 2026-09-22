"""A deterministic test image, so a run is comparable with the last one.

Lived in test_pipeline.py until that file was cut to rejected/. demo.py is the
only shipping caller.
"""

import numpy as np


def test_image(rows, columns, levels):
    """Gradient, two blocks, a diagonal and a border - every kind of edge."""
    img = np.zeros((rows, columns))
    for r in range(rows):
        for c in range(columns):
            img[r, c] = (r / max(1, rows - 1)) * 0.5 + (c / max(1, columns - 1)) * 0.5
    img[rows // 4:rows // 2, columns // 4:columns // 2] = 1.0
    img[rows // 2:3 * rows // 4, columns // 2:3 * columns // 4] = 0.0
    for i in range(min(rows, columns)):
        img[i, i] = 1.0
    img[0, :] = img[-1, :] = img[:, 0] = img[:, -1] = 1.0
    return np.round(img * (levels - 1)) / (levels - 1)
