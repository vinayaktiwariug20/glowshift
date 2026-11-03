"""Studio animation effects as pure functions of time.

Moved verbatim from the prototype. Colors are ``#rrggbb`` strings.
"""
import colorsys
import math
import random

from glowshift.devices.govee.protocol import SEGMENTS as N


def to_hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(x))) for x in rgb)

def rgb(hex_color):
    return tuple(int(hex_color[i:i+2], 16) for i in (1, 3, 5))

def blend(a, b, weight):
    return to_hex(tuple(x * (1-weight) + y * weight for x, y in zip(rgb(a), rgb(b))))

def animation_colors(cfg, t):
    kind, a, b = cfg['kind'], cfg['a'], cfg['b']
    speed = cfg['speed']
    reverse = -1 if cfg['reverse'] else 1
    phase = t * speed * reverse
    if kind == 'Moving gradient':
        return [blend(a, b, (1 + math.sin(2 * math.pi * (i/N - phase/5))) / 2) for i in range(N)]
    if kind == 'Rainbow wave':
        return [to_hex(tuple(v * 255 for v in colorsys.hsv_to_rgb((i/N - phase/5) % 1, 1, 1))) for i in range(N)]
    if kind == 'Breathing':
        intensity = 0.10 + 0.90 * (1 + math.sin(2 * math.pi * phase / 3)) / 2
        return [to_hex(tuple(v * intensity for v in rgb(a)))] * N
    if kind == 'Comet':
        head = (phase * 3) % N
        result = []
        for i in range(N):
            dist = (head - i) % N if reverse == 1 else (i - head) % N
            # Fade over four segments behind the head.
            intensity = max(0.0, 1 - dist / 4)
            result.append(blend(b, a, intensity))
        return result
    if kind == 'Random sparkle':
        # Deterministic time buckets avoid flickering faster than the frame rate.
        bucket = int(t * speed * 2)
        rng = random.Random(bucket)
        result = [b] * N
        for i in rng.sample(range(N), 3):
            result[i] = a
        return result
    return [a] * N
