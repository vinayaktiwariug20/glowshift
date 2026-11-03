"""Stable color analysis modes: Atmospheric, Dominant colors and Spatial (5x3).

Moved verbatim from the prototype. Do not optimize or redesign this code: its
exact output is locked by tests/golden/baseline.json.
"""
import colorsys

import numpy as np

from glowshift.devices.govee.protocol import SEGMENTS


def analyze_frame(bgra, multicolor=True, saturation_boost=1.35, minimum_saturation=0.48,
                  minimum_value=0.48, color_mode='Atmospheric', spatial_mapping='Serpentine'):
    """Extract colorful environmental hues without averaging them into gray.

    Each screen zone gets a circular, saturation-weighted hue estimate.
    Bright neutral UI and dark pixels have little influence. Value is set
    independently of the screen's darkness, preserving hue rather than whitening.
    """
    arr = np.asarray(bgra, dtype=np.uint8)
    h, w = arr.shape[:2]
    arr = arr[int(h*.10):int(h*.88), int(w*.06):int(w*.94), :3]
    if not arr.size:
        return [(40, 55, 115)] * SEGMENTS
    arr = arr[::max(1, arr.shape[0]//110), ::max(1, arr.shape[1]//200), ::-1]

    def representative(region):
        x = region.reshape(-1, 3).astype(np.float32) / 255.0
        maximum = x.max(axis=1)
        minimum = x.min(axis=1)
        delta = maximum - minimum
        sat = delta / np.maximum(maximum, 1e-5)
        # Remove almost-black and near-neutral pixels, including white HUD text.
        good = (maximum > .075) & (sat > .20) & (maximum < .98)
        if np.count_nonzero(good) < 15:
            good = (maximum > .05) & (sat > .12)
        if np.count_nonzero(good) < 8:
            return (40, 55, 115)
        x, maximum, delta, sat = x[good], maximum[good], delta[good], sat[good]
        r, g, b = x[:, 0], x[:, 1], x[:, 2]
        hue = np.zeros(len(x), dtype=np.float32)
        nonzero = delta > 1e-6
        is_r = nonzero & (maximum == r)
        is_g = nonzero & (maximum == g) & ~is_r
        is_b = nonzero & ~(is_r | is_g)
        hue[is_r] = ((g[is_r] - b[is_r]) / delta[is_r]) % 6
        hue[is_g] = ((b[is_g] - r[is_g]) / delta[is_g]) + 2
        hue[is_b] = ((r[is_b] - g[is_b]) / delta[is_b]) + 4
        hue /= 6.0
        # Highly saturated pixels dominate, but highlights are capped.
        weights = (sat ** 2.2) * (np.clip(maximum, .1, .75) ** .65)
        angle = hue * (2 * np.pi)
        cx = np.sum(weights * np.cos(angle))
        cy = np.sum(weights * np.sin(angle))
        strength = np.hypot(cx, cy) / max(float(np.sum(weights)), 1e-6)
        if color_mode == 'Dominant colors':
            # Circular hue histogram: choose an actual color family rather than
            # averaging unrelated hues into an artificial yellow/olive.
            bins = 36
            hist, _ = np.histogram(hue, bins=bins, range=(0, 1), weights=weights)
            smooth = np.roll(hist, 1) + 2 * hist + np.roll(hist, -1)
            peak = int(np.argmax(smooth))
            distance = np.abs((hue - (peak + .5) / bins + .5) % 1 - .5)
            selected = distance <= (1.5 / bins)
            if np.any(selected):
                ww = weights[selected]
                aa = hue[selected] * (2 * np.pi)
                dominant = (np.arctan2(np.sum(ww*np.sin(aa)),
                                       np.sum(ww*np.cos(aa))) / (2*np.pi)) % 1
                sat_for_output = np.average(sat[selected], weights=ww)
            else:
                dominant = (peak + .5) / bins
                sat_for_output = np.average(sat, weights=weights)
        elif strength < .12:
            # Competing colors cancel: choose the most prominent hue bin.
            hist, edges = np.histogram(hue, bins=36, range=(0, 1), weights=weights)
            dominant = (np.argmax(hist) + .5) / 36
        else:
            dominant = (np.arctan2(cy, cx) / (2 * np.pi)) % 1
        if color_mode != 'Dominant colors':
            sat_for_output = np.average(sat, weights=weights)
        s = float(np.clip(max(minimum_saturation, sat_for_output
                              * saturation_boost), 0, 1))
        # Avoid dark LED output; this value is not a screen-brightness average.
        value = max(.0, min(1.0, minimum_value))
        return tuple(round(255*c) for c in colorsys.hsv_to_rgb(float(dominant), s, value))

    if color_mode == 'Spatial (5x3)':
        # Unlike the legacy full-height column modes, keep sky, middle scenery,
        # and ground separate.  The 15 cells map to 15 physical LED segments.
        # Use a representative RGB color rather than averaging unrelated hues.
        def spatial_color(region):
            pixels = region.reshape(-1, 3).astype(np.float32) / 255.0
            if not len(pixels):
                return (40, 55, 115)
            hi = pixels.max(axis=1)
            lo = pixels.min(axis=1)
            saturation = (hi - lo) / np.maximum(hi, 1e-5)
            # Exclude black, but retain pastel colors and blue sky.  Avoid
            # giving highly saturated grass an overwhelming advantage.
            eligible = hi > .07
            if not np.any(eligible):
                return (40, 55, 115)
            pixels = pixels[eligible]
            saturation = saturation[eligible]
            hi = hi[eligible]
            # Modest saturation weighting; large regions still count.
            weights = (.35 + .65 * saturation) * np.sqrt(np.clip(hi, .05, 1.0))
            rgb = np.average(pixels, axis=0, weights=weights)
            hue, sat, _ = colorsys.rgb_to_hsv(*[float(v) for v in rgb])
            sat = float(np.clip(max(minimum_saturation, sat * saturation_boost), 0, 1))
            value = float(np.clip(minimum_value, 0, 1))
            return tuple(round(255*c) for c in colorsys.hsv_to_rgb(hue, sat, value))

        if not multicolor:
            return [spatial_color(arr)] * SEGMENTS
        rows = np.array_split(arr, 3, axis=0)
        grid = [[spatial_color(cell) for cell in np.array_split(row, 5, axis=1)]
                for row in rows]
        if spatial_mapping == 'Serpentine':
            # Alternating direction maintains adjacency between row endpoints.
            return grid[0] + grid[1][::-1] + grid[2]
        return grid[0] + grid[1] + grid[2]

    if not multicolor:
        return [representative(arr)] * SEGMENTS
    groups = np.array_split(arr, 5, axis=1)
    palette = [representative(group) for group in groups]
    return [palette[min(4, i // 3)] for i in range(SEGMENTS)]


def dim(colors, brightness):
    # Brightness is applied in RGB to avoid an extra BLE write each update.
    factor = max(0.05, min(1.0, brightness / 100.0))
    return [tuple(round(channel * factor) for channel in color) for color in colors]
