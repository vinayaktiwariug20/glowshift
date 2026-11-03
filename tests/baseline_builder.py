"""Compute the behavior baseline from the prototypes.

Colors are stored as ``rrggbb`` strings, one entry per line in the JSON file, so
a future change appears as a small, readable diff.
"""
import queue
import types

import numpy as np

import legacy_loader
from scenes import SCENES, SENSITIVITY_SCENES, grass_sky, dark_blue_cave, scene_hash

# Current GUI tuning, and analyze_frame's own different keyword defaults.
GUI_TUNING = {'saturation_boost': 1.5, 'minimum_saturation': .60, 'minimum_value': .75}
FUNCTION_TUNING = {'saturation_boost': 1.35, 'minimum_saturation': .48, 'minimum_value': .48}
TUNINGS = {'gui': GUI_TUNING, 'function': FUNCTION_TUNING}
MODES = ['Atmospheric', 'Dominant colors', 'Spatial (5x3)']


def _hex(colors):
    return ' '.join('%02x%02x%02x' % tuple(int(channel) for channel in color) for color in colors)


def analysis(ambient):
    result = {}
    for scene_name, scene in SCENES.items():
        frame = scene()
        for mode in MODES:
            mappings = ['Serpentine', 'Row-major'] if mode == 'Spatial (5x3)' else ['Serpentine']
            for mapping in mappings:
                for multicolor in (True, False):
                    for tuning_name, tuning in TUNINGS.items():
                        key = f'{scene_name}|{mode}|{mapping}|multi={multicolor}|{tuning_name}'
                        result[key] = _hex(ambient.analyze_frame(
                            frame, multicolor, color_mode=mode, spatial_mapping=mapping, **tuning))
    return result


def sensitivity(ambient):
    result = {}
    for scene_name, scene in SENSITIVITY_SCENES.items():
        frame = scene()
        for mode in ('Atmospheric', 'Dominant colors'):
            for multicolor in (True, False):
                key = f'{scene_name}|{mode}|multi={multicolor}'
                result[key] = _hex(ambient.analyze_frame(frame, multicolor, color_mode=mode, **GUI_TUNING))
    return result


def build_sensitivity():
    ambient = legacy_loader.load(legacy_loader.AMBIENT)
    return {'scene_hashes': {name: scene_hash(scene()) for name, scene in SENSITIVITY_SCENES.items()},
            'analysis': sensitivity(ambient)}


def build_ble_timeline():
    import ble_scenarios
    return ble_scenarios.build_timeline_baseline(legacy_loader.load(legacy_loader.AMBIENT))


def dimming(ambient):
    colors = [(255, 128, 1), (40, 55, 115), (0, 191, 90)]
    return {str(level): _hex(ambient.dim(colors, level)) for level in (0, 1, 5, 33, 65, 100, 150)}


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class _FakeStop:
    """Replaces the Event: each wait advances the fake clock, then stops after N waits."""
    def __init__(self, clock, ticks):
        self.clock, self.ticks, self.stopped = clock, ticks, False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def clear(self):
        self.stopped = False

    def wait(self, seconds):
        self.clock.now += seconds
        self.ticks -= 1
        if self.ticks <= 0:
            self.stopped = True


def run_capture(ambient, frames, ticks, **overrides):
    """Run the real capture loop synchronously on a fake clock; return preview frames."""
    clock = _FakeClock()
    output = queue.Queue()
    engine = ambient.Engine(output)
    engine.stop_flag = _FakeStop(clock, ticks)
    engine.options = {'monitor': 0, 'interval': 1.0, 'transition': 3.0, 'brightness': 65,
                      'multicolor': True, 'color_mode': 'Atmospheric',
                      'spatial_mapping': 'Serpentine', **GUI_TUNING, **overrides}
    saved = ambient.time, ambient.mss
    ambient.time = types.SimpleNamespace(monotonic=clock.monotonic)
    ambient.mss = types.SimpleNamespace(mss=legacy_loader.FakeScreen(frames))
    try:
        engine._capture()
    finally:
        ambient.time, ambient.mss = saved
    previews = []
    while not output.empty():
        kind, payload = output.get()
        if kind == 'preview':
            previews.append(payload)
    return previews


def smoothing_entries(run):
    # Scene changes after two samples; keep every fourth preview to bound file size.
    frames = [dark_blue_cave(), dark_blue_cave(), grass_sky()]
    result = {}
    for name, overrides in {'default': {}, 'transition_0': {'transition': 0.0},
                            'brightness_20': {'brightness': 20}}.items():
        previews = run(frames, 60, **overrides)
        for index in range(0, len(previews), 4):
            result[f'{name}|{index:02d}'] = _hex(previews[index])
    return result


def smoothing(ambient):
    return smoothing_entries(lambda frames, ticks, **overrides:
                             run_capture(ambient, frames, ticks, **overrides))


def packets(ambient, studio):
    rainbow = [(i * 17, 255 - i * 17, 3) for i in range(15)]
    five = [color for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (9, 9, 9), (1, 2, 3))
            for _ in range(3)]
    vectors = {
        'power_on': ambient.packet([0x33, 0x01, 0x01]),
        'power_off': ambient.packet([0x33, 0x01, 0x00]),
        'studio_brightness_1': studio.frame(0x33, 0x04, 1),
        'studio_brightness_50': studio.frame(0x33, 0x04, 50),
        'studio_brightness_100': studio.frame(0x33, 0x04, 100),
        'segment_red_all': ambient.segment_packet((255, 0, 0), 0x7fff),
        'segment_blue_15': ambient.segment_packet((0, 0, 255), 1 << 14),
        'segment_mixed_first': ambient.segment_packet((18, 52, 86), 1),
        'studio_segment_mixed': studio.segment_frame('#123456', 0x2a55),
    }
    result = {name: value.hex() for name, value in vectors.items()}
    for name, colors in {'grouped_one_color': [(10, 20, 30)] * 15, 'grouped_five': five,
                         'grouped_fifteen': rainbow}.items():
        result[name] = ' '.join(item.hex() for item in ambient.grouped_packets(colors))
    return result


def animations(studio):
    result = {}
    for kind in ('Moving gradient', 'Rainbow wave', 'Breathing', 'Comet', 'Random sparkle', 'Unknown'):
        for reverse in (False, True):
            for speed in (1.0, 2.5):
                for moment in (0.0, 0.37, 1.0, 2.5, 7.3, 61.0):
                    config = {'kind': kind, 'a': '#ff6030', 'b': '#102040',
                              'speed': speed, 'reverse': reverse}
                    colors = studio.animation_colors(config, moment)
                    key = f'{kind}|reverse={reverse}|speed={speed}|t={moment}'
                    result[key] = ' '.join(color.lstrip('#') for color in colors)
    return result


def adaptive(module):
    from PIL import Image
    result = {}
    for scene_name, scene in SCENES.items():
        if scene_name == 'small_frame':
            # Fewer pixels than slices casts NaN to int, which is undefined and
            # differs between NumPy builds. See test_baseline.py for the known defect.
            continue
        image = Image.fromarray(scene()[..., [2, 1, 0]])
        for slices in (9, 15):
            result[f'{scene_name}|slices={slices}'] = _hex(module.adaptive_palette(image.copy(), slices))
    return result


def gui_defaults(ambient):
    root = legacy_loader.tk_window()
    try:
        window = ambient.Window(root)
        fields = ('color_mode', 'spatial_mapping', 'interval', 'transition', 'brightness',
                  'saturation_boost', 'minimum_saturation', 'minimum_value', 'multicolor', 'connect')
        return {field: getattr(window, field).get() for field in fields}
    finally:
        root.destroy()


def build():
    ambient = legacy_loader.load(legacy_loader.AMBIENT)
    studio = legacy_loader.load(legacy_loader.STUDIO)
    adaptive_module = legacy_loader.load(legacy_loader.ADAPTIVE)
    return {
        'scene_hashes': {name: scene_hash(scene()) for name, scene in SCENES.items()},
        'gui_defaults': gui_defaults(ambient),
        'packets': packets(ambient, studio),
        'analysis': analysis(ambient),
        'dimming': dimming(ambient),
        'smoothing': smoothing(ambient),
        'animations': animations(studio),
        'adaptive': adaptive(adaptive_module),
    }


def environment():
    import platform
    from importlib.metadata import version
    return {'python': platform.python_version(), 'numpy': np.__version__,
            'pillow': version('pillow')}
