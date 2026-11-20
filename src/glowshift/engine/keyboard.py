"""Screen sync's keyboard output: a mini screen sent to a keyboard through OpenRGB.

It runs on its own thread, separate from Bluetooth, so the strip's pacing is never
affected. Capture hands it each sampled frame; it smooths towards that frame's grid
and sends changed colours up to ``RATE`` times a second. If OpenRGB is not reachable
it says so and tries again every ``RETRY_SECONDS``.
"""
import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

from glowshift.analysis.ambient import dim
from glowshift.analysis.grid import grid_colors
from glowshift.analysis.smoothing import ColorSmoother
from glowshift.devices.openrgb.client import OpenRGBClient, ProtocolError
from glowshift.devices.openrgb.keyboard import NoKeyboard, find_keyboard, led_colors

logger = logging.getLogger('glowshift.keyboard')

RATE = 10.0
RETRY_SECONDS = 5.0
DEFAULT_GRID = (6, 19)  # Used for the preview until a keyboard's layout is known.


@dataclass(frozen=True)
class KeyboardSource:
    """What the keyboard shows: part of the screen as a mini screen, or the strip's colours."""
    kind: str  # 'screen' or 'strip'
    band: tuple = (0.0, 1.0)


WHOLE_SCREEN = KeyboardSource('screen', (0.0, 1.0))
LOWER_HALF = KeyboardSource('screen', (0.5, 1.0))  # A keyboard usually sits below the screen.
MATCH_STRIP = KeyboardSource('strip')


def strip_grid(colors, rows, columns):
    """Spread the strip's colours across the columns, left to right, the same on every row."""
    line = [tuple(colors[column * len(colors) // columns]) for column in range(columns)]
    return [list(line) for _ in range(rows)]


def default_client_factory():
    return OpenRGBClient(name='GlowShift')


class KeyboardWorker:
    def __init__(self, options, on_status, on_preview, client_factory=default_client_factory,
                 clock=time.monotonic, source=WHOLE_SCREEN, on_exit=lambda crashed: None):
        self.options = options
        self.source = source
        self._on_status = on_status
        self._on_preview = on_preview
        self._on_exit = on_exit
        self._client_factory = client_factory
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame = None
        self._strip = None
        self.thread = threading.Thread(target=self._main, name='glowshift-keyboard', daemon=True)

    def start(self):
        self.thread.start()

    def request_stop(self):
        self._stop.set()

    def offer_frame(self, frame):
        """Called by capture with each sampled frame."""
        with self._lock:
            self._frame = frame

    def offer_strip(self, colors):
        """Called by capture with the strip's colours, already smoothed and dimmed."""
        with self._lock:
            self._strip = colors

    def _take_frame(self):
        with self._lock:
            frame, self._frame = self._frame, None
        return frame

    def _take_strip(self):
        with self._lock:
            colors, self._strip = self._strip, None
        return colors

    def _connect(self):
        client = self._client_factory()
        try:
            layout, controller = find_keyboard(client.controllers())
            if controller.active_mode_name.lower() != 'direct':
                client.set_custom_mode(layout.index)  # OpenRGB's documented switch to direct control.
                logger.info('Asked OpenRGB to switch %s to direct control', layout.name)
            return client, layout
        except BaseException:
            client.close()
            raise

    def _main(self):
        try:
            self._run()
        except Exception:
            logger.exception('Keyboard worker failed')
            self._on_exit(True)
        else:
            self._on_exit(False)

    def _run(self):
        options = self.options
        client = layout = None
        rows, columns = DEFAULT_GRID
        smoother = ColorSmoother(options.transition)
        target = sent = shown = grid = last_strip = None
        retry_at = self._clock()
        last_tick = self._clock()
        try:
            while not self._stop.is_set():
                now = self._clock()
                if client is None and now >= retry_at:
                    try:
                        client, layout = self._connect()
                    except (OSError, ProtocolError, NoKeyboard) as error:
                        retry_at = now + RETRY_SECONDS
                        self._status(f'{_reason(error)}; retrying in {RETRY_SECONDS:g} s', error)
                    else:
                        sent = None
                        self._status(f'Lighting {layout.name}')
                        if (layout.rows, layout.columns) != (rows, columns):
                            rows, columns = layout.rows, layout.columns
                            smoother, target = ColorSmoother(options.transition), None
                if self.source.kind == 'strip':
                    last_strip = self._take_strip() or last_strip
                    if last_strip is not None:
                        grid = strip_grid(last_strip, rows, columns)
                else:
                    frame = self._take_frame()
                    if frame is not None:
                        cells = grid_colors(frame, rows, columns, options.saturation_boost,
                                            options.minimum_saturation, options.minimum_value, *self.source.band)
                        target = np.array([color for row in cells for color in row], dtype=np.float64)
                    if target is not None:
                        flat = dim(smoother.update(target, now - last_tick), options.brightness)
                        grid = [flat[row * columns:(row + 1) * columns] for row in range(rows)]
                if grid is not None:
                    if grid != shown:
                        shown = grid
                        self._on_preview(grid)
                    if client is not None:
                        try:
                            client.poll()
                            if client.list_changed:
                                # Indexes may have moved: read the list again before sending.
                                client.close()
                                client = None
                                retry_at = now
                                self._status('OpenRGB device list changed; reconnecting')
                            else:
                                colors = led_colors(grid, layout)
                                if colors != sent:
                                    client.update_leds(layout.index, colors)
                                    sent = colors
                        except (OSError, ProtocolError) as error:
                            client.close()
                            client = None
                            retry_at = now + RETRY_SECONDS
                            self._status(f'{_reason(error)}; retrying in {RETRY_SECONDS:g} s', error)
                last_tick = now
                self._stop.wait(1 / RATE)
        finally:
            if client is not None:
                client.close()
            self._status('Stopped')

    def _status(self, text, error=None):
        if error is not None:
            logger.warning('Keyboard: %s', text)
        self._on_status(text)


def _reason(error):
    if isinstance(error, NoKeyboard):
        return 'OpenRGB lists no keyboard with a key layout'
    if isinstance(error, ConnectionRefusedError):
        return 'OpenRGB is not running or its SDK server is off'
    if isinstance(error, (ConnectionError, TimeoutError)):
        return 'Lost contact with OpenRGB'
    if isinstance(error, ProtocolError):
        return f'Unexpected reply from OpenRGB ({error})'
    return f'OpenRGB not reachable ({type(error).__name__})'
