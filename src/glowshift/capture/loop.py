"""Screen sampling loop, extracted from the prototype with its timing unchanged."""
import time
from dataclasses import dataclass

import numpy as np

from glowshift.analysis.ambient import analyze_frame, dim
from glowshift.analysis.smoothing import ColorSmoother


@dataclass(frozen=True)
class CaptureOptions:
    monitor: int
    interval: float
    transition: float
    brightness: int
    multicolor: bool
    saturation_boost: float
    minimum_saturation: float
    minimum_value: float
    color_mode: str
    spatial_mapping: str


def open_screen():
    import mss
    return mss.mss()


def capture_loop(options, stop, on_colors, on_started=lambda: None, screen_factory=open_screen,
                 clock=time.monotonic):
    with screen_factory() as screen:
        monitors = screen.monitors[1:]
        if options.monitor >= len(monitors):
            raise RuntimeError('Selected monitor no longer exists')
        monitor = monitors[options.monitor]
        smoother = ColorSmoother(options.transition)
        target = None
        last_sample = -1e9
        last_tick = clock()
        on_started()
        while not stop.is_set():
            now = clock()
            if now - last_sample >= options.interval:
                frame = np.asarray(screen.grab(monitor))
                target = np.array(analyze_frame(
                    frame, options.multicolor, options.saturation_boost, options.minimum_saturation,
                    options.minimum_value, options.color_mode, options.spatial_mapping), dtype=np.float64)
                last_sample = now
            if target is not None:
                on_colors(dim(smoother.update(target, now - last_tick), options.brightness))
            last_tick = now
            stop.wait(.15)
