"""Mini screen: one colour per cell of a rows x columns grid laid over the screen.

Each cell is coloured the way Spatial (5x3) colours its cells (ambient.py), so a
keyboard and the strip share one look: saturation-weighted average colour, saturation
raised by the boost, and a fixed intensity rather than the screen's brightness.
The same screen edges are left out as in screen sync, to skip HUD and taskbar.
"""
import colorsys

import numpy as np

FALLBACK = (40, 55, 115)  # Screen sync's colour for a cell with nothing to show.


def cell_color(region, saturation_boost, minimum_saturation, minimum_value):
    pixels = region.reshape(-1, 3).astype(np.float32) / 255.0
    if not len(pixels):
        return FALLBACK
    high = pixels.max(axis=1)
    low = pixels.min(axis=1)
    saturation = (high - low) / np.maximum(high, 1e-5)
    eligible = high > .07  # Exclude black.
    if not np.any(eligible):
        return FALLBACK
    pixels, saturation, high = pixels[eligible], saturation[eligible], high[eligible]
    weights = (.35 + .65 * saturation) * np.sqrt(np.clip(high, .05, 1.0))
    rgb = np.average(pixels, axis=0, weights=weights)
    hue, sat, _ = colorsys.rgb_to_hsv(*[float(v) for v in rgb])
    sat = float(np.clip(max(minimum_saturation, sat * saturation_boost), 0, 1))
    value = float(np.clip(minimum_value, 0, 1))
    return tuple(round(255 * c) for c in colorsys.hsv_to_rgb(hue, sat, value))


def grid_colors(bgra, rows, columns, saturation_boost, minimum_saturation, minimum_value, top=0.0, bottom=1.0):
    """Return ``rows`` lists of ``columns`` RGB tuples, top row first.

    ``top`` and ``bottom`` (0 to 1) choose a horizontal band of the screen, for
    example 0.5 and 1.0 for its lower half.
    """
    arr = np.asarray(bgra, dtype=np.uint8)
    height, width = arr.shape[:2]
    arr = arr[int(height * .10):int(height * .88), int(width * .06):int(width * .94), :3]
    arr = arr[int(arr.shape[0] * top):int(arr.shape[0] * bottom)]
    if not arr.size:
        return [[FALLBACK] * columns for _ in range(rows)]
    # About 20 x 12 samples per cell is plenty and keeps this fast.
    arr = arr[::max(1, arr.shape[0] // (rows * 20)), ::max(1, arr.shape[1] // (columns * 12)), ::-1]
    return [[cell_color(cell, saturation_boost, minimum_saturation, minimum_value)
             for cell in np.array_split(band, columns, axis=1)]
            for band in np.array_split(arr, rows, axis=0)]
