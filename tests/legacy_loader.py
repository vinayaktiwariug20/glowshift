"""Load the unmodified prototypes from local legacy/ with hardware access stubbed.

The real ``mss`` and ``bleak`` modules are replaced while a prototype is imported,
so its globals can never reach the screen or a Bluetooth adapter.
"""
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np

LEGACY_DIR = Path(__file__).resolve().parent.parent / 'legacy'
AMBIENT = 'glowshift_ambient_v3_5_1'
STUDIO = 'govee_h617a_studio_v5'
ADAPTIVE = 'glowshift_adaptive_slices'


class FakeScreen:
    """Stands in for ``mss.mss()``. Serves queued synthetic frames, last one repeating."""
    monitors = [{'width': 640, 'height': 360}, {'width': 640, 'height': 360}]

    def __init__(self, frames=None):
        self.frames = list(frames or [np.zeros((360, 640, 4), np.uint8)])

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, monitor):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


class _ForbiddenBluetooth:
    def __init__(self, *args, **kwargs):
        raise AssertionError('tests must not create a real Bluetooth object')


def available():
    return all((LEGACY_DIR / f'{name}.py').exists() for name in (AMBIENT, STUDIO, ADAPTIVE))


def load(name):
    mss_stub = types.ModuleType('mss')
    mss_stub.mss = FakeScreen()
    bleak_stub = types.ModuleType('bleak')
    bleak_stub.BleakClient = _ForbiddenBluetooth
    bleak_stub.BleakScanner = _ForbiddenBluetooth
    saved = {key: sys.modules.get(key) for key in ('mss', 'bleak')}
    sys.modules.update(mss=mss_stub, bleak=bleak_stub)
    write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep legacy/ byte-for-byte untouched
    try:
        spec = importlib.util.spec_from_file_location(f'legacy_{name}', LEGACY_DIR / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = write_bytecode
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
