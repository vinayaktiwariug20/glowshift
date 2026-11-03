"""Synthetic BGRA test scenes. No real screenshots or gameplay frames.

Scenes use only basic integer arithmetic, not NumPy's random generators, so the
pixels stay identical across NumPy versions.
"""
import colorsys
import hashlib

import numpy as np


def _blank(height=360, width=640):
    frame = np.zeros((height, width, 4), np.uint8)
    frame[..., 3] = 255
    return frame


def _noise(height, width):
    """Deterministic pseudo-random bytes from a linear congruential step."""
    index = np.arange(height * width * 3, dtype=np.uint64)
    mixed = (index * np.uint64(1103515245) + np.uint64(12345)) & np.uint64(0x7fffffff)
    mixed = (mixed * np.uint64(1103515245) + np.uint64(12345)) & np.uint64(0x7fffffff)
    return ((mixed >> np.uint64(16)) & np.uint64(255)).astype(np.uint8).reshape(height, width, 3)


def dark_blue_cave():
    frame = _blank()
    frame[..., :3] = (90, 40, 15)
    return frame


def black():
    return _blank()


def white_hud():
    frame = _blank()
    frame[..., :3] = 255
    return frame


def red_green_split():
    frame = _blank()
    frame[:, :320, 2] = 220
    frame[:, 320:, 1] = 220
    return frame


def pure_red():
    frame = _blank()
    frame[..., 2] = 255
    return frame


def gradient():
    frame = _blank()
    frame[..., 2] = np.linspace(0, 255, 640, dtype=np.uint8)
    frame[..., 0] = 255 - frame[..., 2]
    frame[..., 1] = 60
    return frame


def noise():
    frame = _blank()
    frame[..., :3] = _noise(360, 640)
    return frame


def grass_sky():
    frame = _blank()
    frame[:180, :, :3] = (235, 170, 90)
    frame[180:, :, :3] = (40, 170, 60)
    return frame


def hud_overlay():
    frame = dark_blue_cave()
    frame[60:90, 60:580, :3] = 255
    frame[280:300, 60:580, :3] = 250
    return frame


def ember_on_dark():
    frame = _blank()
    frame[..., :3] = (8, 6, 6)
    frame[120:150, 100:130, :3] = (20, 90, 240)
    frame[200:220, 420:470, :3] = (200, 60, 40)
    return frame


def ultrawide():
    frame = _blank(360, 840)
    frame[:, :280, :3] = (180, 30, 120)
    frame[:, 280:560, :3] = (30, 160, 220)
    frame[:, 560:, :3] = (60, 200, 40)
    return frame


def small_frame():
    frame = _blank(4, 4)
    frame[..., :3] = (10, 200, 200)
    return frame


SCENES = {scene.__name__: scene for scene in (
    dark_blue_cave, black, white_hud, red_green_split, pure_red, gradient, noise,
    grass_sky, hud_overlay, ember_on_dark, ultrawide, small_frame)}


def _bgr(hue, saturation, value):
    red, green, blue = colorsys.hsv_to_rgb(hue / 360, saturation, value)
    return round(blue * 255), round(green * 255), round(red * 255)


def banded(first, second, band=10):
    """Fine horizontal bands, so two tones compete inside every analysis region."""
    frame = _blank()
    for start in range(0, 360, band * 2):
        frame[start:start + band, :, :3] = _bgr(*first)
        frame[start + band:start + band * 2, :, :3] = _bgr(*second)
    return frame


# (hue, saturation, value) pairs chosen so the output depends on the weighting
# constants: different brightness or saturation, and hues close enough not to cancel.
SENSITIVITY_SCENES = {
    'bright_orange_dim_blue': lambda: banded((30, .9, .95), (210, .9, .12)),
    'bright_red_dim_yellow': lambda: banded((0, .85, .9), (60, .85, .15)),
    'mid_green_dim_cyan': lambda: banded((120, .8, .6), (180, .8, .1)),
    'pale_magenta_vivid_blue': lambda: banded((300, .3, .7), (240, .95, .7)),
    'pale_red_vivid_green': lambda: banded((10, .25, .8), (100, .9, .6)),
    'near_threshold_tones': lambda: banded((40, .21, .5), (160, .6, .08)),
    'highlight_cap_tones': lambda: banded((200, .7, .97), (280, .7, .74)),
    'dark_floor_tones': lambda: banded((20, .7, .08), (90, .7, .3)),
}


def scene_hash(frame):
    return hashlib.sha256(frame.tobytes()).hexdigest()[:16]
