"""Studio design files, extracted from the prototype's save and load handlers."""
import json
from dataclasses import dataclass

from glowshift.devices.govee.protocol import SEGMENTS


@dataclass
class Design:
    colors: list
    brightness: int


def save_design(path, design):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'model': 'H617A', 'colors': design.colors, 'brightness': design.brightness}, f, indent=2)


def load_design(path):
    """Read and validate a design. Raises on invalid content, as the prototype did."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    colors = data['colors']
    if len(colors) != SEGMENTS or any(not isinstance(c, str) or len(c) != 7 or not c.startswith('#') or any(x not in '0123456789abcdefABCDEF' for x in c[1:]) for c in colors):
        raise ValueError('Invalid segment colors')
    return Design(colors, max(1, min(100, int(data.get('brightness', 50)))))
