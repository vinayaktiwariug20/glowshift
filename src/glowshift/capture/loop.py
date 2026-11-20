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
    strip_band: tuple = (0.0, 1.0)  # Part of the screen the strip shows, top to bottom (0 to 1).


RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 10.0)  # The last delay repeats.
WHOLE_SCREEN = (0.0, 1.0)
UPPER_HALF = (0.0, 0.5)


def band(frame, rows):
    """The horizontal band ``rows`` (fractions, top to bottom) of a frame; the frame itself if whole."""
    if tuple(rows) == WHOLE_SCREEN:
        return frame
    height = frame.shape[0]
    return frame[int(height * rows[0]):int(height * rows[1])]


class ScreenUnavailable(RuntimeError):
    """Opening or grabbing the screen failed, as during a lock screen, a UAC prompt or
    a display change. Usually temporary."""


def open_screen():
    import mss
    return mss.mss()


def _open(screen_factory):
    try:
        return screen_factory()
    except Exception as error:
        raise ScreenUnavailable(f'Opening the screen failed: {type(error).__name__}: {error}') from error


def _grab(screen, monitor):
    try:
        return screen.grab(monitor)
    except Exception as error:
        raise ScreenUnavailable(f'Screen grab failed: {type(error).__name__}: {error}') from error


def capture_loop(options, stop, on_colors, on_started=lambda: None, screen_factory=open_screen,
                 clock=time.monotonic, on_frame=None):
    with _open(screen_factory) as screen:
        monitors = screen.monitors[1:]
        if options.monitor >= len(monitors):
            raise ScreenUnavailable('Selected monitor no longer exists')
        monitor = monitors[options.monitor]
        smoother = ColorSmoother(options.transition)
        target = None
        last_sample = -1e9
        last_tick = clock()
        on_started()
        while not stop.is_set():
            now = clock()
            if now - last_sample >= options.interval:
                frame = np.asarray(_grab(screen, monitor))
                if on_frame is not None:
                    on_frame(frame)  # Other outputs, such as a keyboard, analyze it on their own threads.
                target = np.array(analyze_frame(
                    band(frame, options.strip_band), options.multicolor, options.saturation_boost, options.minimum_saturation,
                    options.minimum_value, options.color_mode, options.spatial_mapping), dtype=np.float64)
                last_sample = now
            if target is not None:
                on_colors(dim(smoother.update(target, now - last_tick), options.brightness))
            last_tick = now
            stop.wait(.15)


def capture_with_retry(options, stop, on_colors, on_started=lambda: None, on_unavailable=lambda error, wait: None,
                       screen_factory=open_screen, clock=time.monotonic, on_frame=None):
    """Run ``capture_loop``, reopening the screen after ``ScreenUnavailable`` until stopped.

    Other errors, such as a failure in color analysis, still end the capture. While the
    screen is unavailable nothing is published, so the strip keeps its last colors.
    ``on_started`` is called on the first published colors after each (re)start, since
    opening the screen does not mean grabs will succeed.
    """
    failures = 0
    started = False

    def colors(values):
        nonlocal failures, started
        if not started:
            started = True
            on_started()
        failures = 0
        on_colors(values)

    while not stop.is_set():
        started = False
        try:
            capture_loop(options, stop, colors, screen_factory=screen_factory, clock=clock, on_frame=on_frame)
            return
        except ScreenUnavailable as error:
            wait = RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)]
            failures += 1
            on_unavailable(error, wait)
            stop.wait(wait)
