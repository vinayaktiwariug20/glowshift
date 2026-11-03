"""Simulated Bluetooth radio on virtual time. Touches no real hardware.

``VirtualTimeLoop`` jumps straight to the next timer, so a two-minute scenario runs
instantly and every timestamp is exact and repeatable.
"""
import asyncio
import selectors
import threading
from dataclasses import dataclass

from glowshift.devices.govee.connection import ServiceDiscoveryFailed

HANG = 'hang'
IGNORE_CANCEL = 'ignore-cancel'


class _VirtualSelector:
    def __init__(self):
        self._real = selectors.DefaultSelector()
        self.now = 1000.0

    def select(self, timeout=None):
        if timeout is None:
            raise RuntimeError('deadlock: nothing is scheduled')
        self.now += timeout
        return self._real.select(0)

    def __getattr__(self, name):
        return getattr(self._real, name)


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        self._virtual = _VirtualSelector()
        super().__init__(self._virtual)

    def time(self):
        return self._virtual.now


def run_virtual(main):
    with asyncio.Runner(loop_factory=VirtualTimeLoop) as runner:
        return runner.run(main())


@dataclass
class Step:
    """How one connection attempt behaves."""
    locate: object = None             # exception to raise, HANG, or None for success
    connect: object = None            # exception to raise, HANG, IGNORE_CANCEL, or None
    has_characteristic: bool = True
    drop_after_writes: int = None
    drop_after_seconds: float = None
    fire_callback: bool = True        # False: the link drops with no callback
    write_error: Exception = None
    write_error_at: int = 1
    write_hang_at: int = None
    disconnect: object = None         # exception to raise, HANG, or None


class FakeRadio:
    """Scripted attempts. The last step repeats. Logs every operation with its time."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.index = -1
        self.log = []
        self.links = []
        self.released = threading.Event()

    def clock(self):
        return asyncio.get_running_loop().time()

    def record(self, operation, detail=''):
        self.log.append((self.clock(), operation, detail))

    def next_step(self):
        self.index += 1
        return self.steps[min(self.index, len(self.steps) - 1)]

    def link_factory(self, on_disconnect, cached_services=True):
        link = FakeLink(self, self.next_step(), on_disconnect, cached_services)
        self.links.append(link)
        return link

    def operations(self, *names):
        return [entry for entry in self.log if entry[1] in names]

    def timeline(self, start):
        return [f'{moment - start:.3f} {operation} {detail}'.rstrip()
                for moment, operation, detail in self.operations('locate', 'connect', 'write')]


class FakeLink:
    def __init__(self, radio, step, on_disconnect, cached_services):
        self.radio, self.step = radio, step
        self.on_disconnect = on_disconnect
        self.cached_services = cached_services
        self.connected = False
        self.writes = 0

    async def _behave(self, behavior):
        if behavior == HANG:
            await asyncio.Event().wait()
        elif behavior == IGNORE_CANCEL:
            while not self.radio.released.is_set():
                try:
                    await asyncio.sleep(.01)
                except asyncio.CancelledError:
                    pass
        elif behavior is not None:
            raise behavior

    async def locate(self):
        self.radio.record('locate')
        await self._behave(self.step.locate)

    async def connect(self):
        self.radio.record('connect', '' if self.cached_services else 'uncached')
        await self._behave(self.step.connect)
        self.connected = True
        if not self.step.has_characteristic:
            raise ServiceDiscoveryFailed('characteristic missing')
        if self.step.drop_after_seconds is not None:
            asyncio.get_running_loop().call_later(self.step.drop_after_seconds, self.drop)

    @property
    def is_connected(self):
        return self.connected

    def drop(self):
        if self.connected:
            self.connected = False
            self.radio.record('dropped')
            if self.step.fire_callback:
                self.on_disconnect()

    async def write(self, data):
        self.writes += 1
        self.radio.record('write', data.hex())
        if self.writes == self.step.write_hang_at:
            await asyncio.Event().wait()
        if self.step.write_error is not None and self.writes == self.step.write_error_at:
            raise self.step.write_error
        if self.writes == self.step.drop_after_writes:
            self.drop()

    async def disconnect(self):
        self.radio.record('disconnect')
        await self._behave(self.step.disconnect)
        self.connected = False
