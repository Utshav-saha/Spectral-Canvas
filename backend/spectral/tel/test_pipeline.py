"""Encode -> simulated VoIP channel -> decode, and report the error.

This is Generation A over a voice call. It is lossy by construction - GSM's
8-pole LPC cannot hold N simultaneous tone amplitudes - and that is the point:
the loss it produces is what the restoration model is being trained against.
Run it to see where the baseline sits.
"""

import numpy as np

import tel_config as cfg
import tel_encoder
import tel_decoder
import channel_sim


def test_image(rows, columns, levels):
    """A deterministic test pattern: gradient, blocks, diagonal, border."""
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


def run(rows=cfg.ROWS, columns=cfg.COLUMNS, levels=cfg.GRAY_LEVELS,
        f_low=cfg.F_LOW, f_high=cfg.F_HIGH, loss=0.02, seed=0,
        per_column_sync=True, clean=False, verbose=True):

    source = test_image(rows, columns, levels)

    audio, meta = tel_encoder.encode(source, f_low=f_low, f_high=f_high)
    meta["gray_levels"] = levels

    received = audio if clean else channel_sim.channel(audio, loss_rate=loss, seed=seed)

    activation = tel_decoder.decode(received, meta, per_column_sync=per_column_sync)
    activation = tel_decoder.quantize(activation, levels)

    src_px = tel_decoder.to_pixels(source, levels).astype(float)
    out_px = tel_decoder.to_pixels(activation, levels).astype(float)

    mae = float(np.mean(np.abs(out_px - src_px)))
    mse = float(np.mean((out_px - src_px) ** 2))
    psnr = float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)
    exact = float(np.mean(np.isclose(out_px, src_px)))

    if verbose:
        print(f"  rows={rows:3d} levels={levels:2d} band={f_low:.0f}-{f_high:.0f}Hz "
              f"loss={loss:.0%}  MAE={mae:6.2f}  PSNR={psnr:5.1f}dB  "
              f"exact={exact:5.1%}  dur={meta['duration_seconds']:.1f}s")
    return dict(mae=mae, psnr=psnr, exact=exact,
                duration=meta["duration_seconds"])


if __name__ == "__main__":
    print("\nno channel (sanity check, should be near perfect):")
    run(clean=True)

    print("\nthrough simulated GSM call:")
    run()
