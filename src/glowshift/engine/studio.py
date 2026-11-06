"""Studio: designs, live painting, power, brightness and animations.

Every write goes through ``ConnectionManager``, so Studio and screen sync share one
Bluetooth path. Studio v5 kept its own client and command queue; here each action
sets the desired state instead, and the manager sends it (latest wins).

Differences from Studio v5, all deliberate:
- Live painting sends the whole design, grouped by color, instead of one packet for
  the clicked segment. Rapid clicks are coalesced rather than queued.
- The connection scans for the strip before connecting, retries on its own and
  resends the current state after a reconnect.
"""
import threading
import time
from dataclasses import dataclass, field

from glowshift.animations.effects import animation_colors, rgb
from glowshift.devices.govee.connection import BluetoothWorker, ConnectionManager, ConnectionState
from glowshift.engine.session import BLUETOOTH_TEXT

FRAME_RATE = 4.0  # Studio v5's animation frame rate.
STOP_TIMEOUT = 8.0


@dataclass(frozen=True)
class StudioEvent:
    kind: str  # 'bluetooth', 'animation' or 'worker_exit'
    data: dict = field(default_factory=dict)


class Studio:
    def __init__(self, link_factory, sink, on_connection_event=None, clock=time.monotonic):
        self._link_factory = link_factory
        self._sink = sink
        self._on_connection_event = on_connection_event
        self._clock = clock
        self._lock = threading.Lock()
        self._worker = None
        self._power = None
        self._brightness = None
        self._colors = None
        self._animated = False
        self._animation = None  # (thread, stop event)

    @property
    def active(self):
        """True while Studio's Bluetooth worker is running, including while it stops."""
        worker = self._worker
        return worker is not None and worker.thread.is_alive()

    @property
    def animating(self):
        return self._animation is not None and self._animation[0].is_alive()

    # Connection
    def connect(self):
        if self.active:
            return
        manager = ConnectionManager(self._link_factory, self._on_event)
        with self._lock:
            if self._power is not None:
                manager.set_power(self._power)
            if self._brightness is not None:
                manager.set_brightness(self._brightness)
            if self._colors is not None:
                manager.publish(self._colors, self._animated)
        worker = BluetoothWorker(manager, self._on_exit)
        self._worker = worker
        worker.start()

    def disconnect(self):
        """Stop animating and disconnect. Returns immediately."""
        self.stop_animation()
        if self._worker is not None:
            self._worker.request_stop()

    def close(self, timeout=STOP_TIMEOUT):
        """Disconnect and wait. Returns False if the worker outlived ``timeout``."""
        self.stop_animation()
        return self._worker is None or self._worker.stop(timeout)

    # Desired state. Each call connects if needed, as Studio v5 did.
    def send_colors(self, colors):
        """Show ``colors`` (15 ``#rrggbb`` strings). Ends any animation."""
        self.stop_animation()
        self._publish([rgb(color) for color in colors], animated=False)
        self.connect()

    def set_power(self, on):
        with self._lock:
            self._power = bool(on)
        self._manager_call('set_power', bool(on))
        self.connect()

    def set_brightness(self, level):
        level = max(1, min(100, int(level)))
        with self._lock:
            self._brightness = level
        self._manager_call('set_brightness', level)
        self.connect()

    def start_animation(self, config):
        """``config`` as Studio v5: kind, a, b (``#rrggbb``), speed, reverse."""
        self.stop_animation()
        stop = threading.Event()
        rate = config.get('fps', FRAME_RATE)
        thread = threading.Thread(target=self._animate, args=(dict(config), rate, stop),
                                  name='glowshift-animation', daemon=True)
        self._animation = (thread, stop)
        self._publish(self._frame(config, 0.0), animated=True)
        thread.start()
        self.connect()
        self._sink(StudioEvent('animation', {'text': 'Animation running'}))

    def stop_animation(self):
        """End the animation, leaving its last frame on the strip, as Studio v5 did."""
        animation, self._animation = self._animation, None
        if animation is None:
            return
        thread, stop = animation
        stop.set()
        thread.join(2)
        with self._lock:
            colors = self._colors
        if colors is not None:
            self._publish(colors, animated=False)
        self._sink(StudioEvent('animation', {'text': 'Animation stopped'}))

    # Internals
    @staticmethod
    def _frame(config, t):
        return [rgb(color) for color in animation_colors(config, t)]

    def _animate(self, config, rate, stop):
        start = self._clock()
        while not stop.wait(1.0 / rate):
            self._publish(self._frame(config, self._clock() - start), animated=True)

    def _publish(self, colors, animated):
        with self._lock:
            self._colors, self._animated = colors, animated
        self._manager_call('publish', colors, animated)

    def _manager_call(self, name, *args):
        worker = self._worker
        if worker is not None and worker.thread.is_alive():
            getattr(worker.manager, name)(*args)

    def _on_event(self, event):
        if self._on_connection_event is not None:
            self._on_connection_event(event)
        if event.kind == 'state':
            state = event.fields['state']
            text = 'Disconnected' if state is ConnectionState.DISCONNECTED else BLUETOOTH_TEXT[state]
            self._sink(StudioEvent('bluetooth', {'text': text}))
        elif event.kind == 'batch_accepted_by_windows':
            self._sink(StudioEvent('bluetooth', {'text': (
                f'Writes accepted by Windows at {time.strftime("%H:%M:%S")} '
                f'({event.fields["packets"]} commands); device delivery not confirmed')}))
        elif event.kind == 'failure':
            self._sink(StudioEvent('bluetooth', {'text': (
                f'{event.fields["failure"].value}: {event.fields["detail"][:85]}')}))

    def _on_exit(self, error):
        self._sink(StudioEvent('worker_exit', {'crashed': error is not None}))
