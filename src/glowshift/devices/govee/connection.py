"""Single owner of the H617A Bluetooth connection.

``ConnectionManager`` is the only code that writes to the strip, and it is an
explicit state machine. It knows nothing about bleak: it drives a link object with
``locate``, ``connect``, ``write``, ``disconnect`` and ``is_connected``.

Write-without-response gives no device acknowledgment. A completed write means only
that Windows accepted the data, and nothing here may treat it as delivery.
"""
import asyncio
import collections
import enum
import functools
import logging
import threading
from dataclasses import dataclass

from glowshift.devices.govee.protocol import grouped_packets

logger = logging.getLogger('glowshift.connection')


class ConnectionState(enum.Enum):
    DISCONNECTED = 'disconnected'
    SCANNING = 'scanning'
    CONNECTING = 'connecting'
    CONNECTED = 'connected'
    RECOVERING = 'recovering'
    RADIO_OFF = 'radio off'
    STOPPING = 'stopping'


_S = ConnectionState
TRANSITIONS = {
    _S.DISCONNECTED: {_S.SCANNING, _S.STOPPING},
    _S.SCANNING: {_S.CONNECTING, _S.RECOVERING, _S.RADIO_OFF, _S.STOPPING},
    _S.CONNECTING: {_S.CONNECTED, _S.RECOVERING, _S.RADIO_OFF, _S.STOPPING},
    _S.CONNECTED: {_S.RECOVERING, _S.RADIO_OFF, _S.STOPPING},
    _S.RECOVERING: {_S.SCANNING, _S.STOPPING},
    _S.RADIO_OFF: {_S.SCANNING, _S.STOPPING},
    _S.STOPPING: {_S.DISCONNECTED},
}


class Failure(enum.Enum):
    RADIO_OFF = 'radio off'
    NOT_FOUND = 'device not found'
    SCAN_TIMEOUT = 'scan timeout'
    CONNECT_FAILED = 'connect failed'
    CONNECT_TIMEOUT = 'connect timeout'
    SERVICE_DISCOVERY = 'service discovery failed'
    STALE_CHARACTERISTIC = 'stale characteristic'
    UNREACHABLE = 'unreachable'
    WRITE_FAILED = 'write failed'
    WRITE_TIMEOUT = 'write timeout'
    DISCONNECTED = 'disconnected'


class RadioOff(Exception):
    """The Bluetooth adapter is unavailable. Not a connection failure."""


class DeviceNotFound(Exception):
    pass


class ServiceDiscoveryFailed(Exception):
    pass


class StaleCharacteristic(Exception):
    pass


class _LinkLost(Exception):
    pass


def classify(error, phase):
    """Map an exception raised during ``phase`` (scan, connect or write) to a Failure."""
    if isinstance(error, RadioOff):
        return Failure.RADIO_OFF
    if isinstance(error, DeviceNotFound):
        return Failure.NOT_FOUND
    if isinstance(error, ServiceDiscoveryFailed):
        return Failure.SERVICE_DISCOVERY
    if isinstance(error, _LinkLost):
        return Failure.DISCONNECTED
    if isinstance(error, StaleCharacteristic) or (phase == 'write' and isinstance(error, (ValueError, KeyError))):
        return Failure.STALE_CHARACTERISTIC
    if 'unreachable' in str(error).lower():
        return Failure.UNREACHABLE
    if isinstance(error, TimeoutError):
        return {'scan': Failure.SCAN_TIMEOUT, 'connect': Failure.CONNECT_TIMEOUT}.get(phase, Failure.WRITE_TIMEOUT)
    return Failure.WRITE_FAILED if phase == 'write' else Failure.CONNECT_FAILED


@dataclass(frozen=True)
class Timing:
    """Values taken from the prototype, locked by tests/golden/ble_timeline.json."""
    scan_deadline: float = 16
    connect_deadline: float = 24
    write_timeout: float = 5
    disconnect_timeout: float = 5
    packet_gap: float = .065
    command_gap: float = .08        # Studio's gap between direct commands
    minimum_batch_interval: float = 1.2
    poll: float = .15
    retry_initial: float = 3.0
    retry_factor: float = 1.8
    retry_maximum: float = 30.0
    cooldown_after_failures: int = 5
    cooldown: float = 60.0
    # New in the package. Only lasting this long resets the retry delay, because an
    # accepted write proves nothing about the link.
    stable_connection: float = 30.0
    radio_retry: float = 60.0


class Backoff:
    def __init__(self, timing):
        self.timing = timing
        self.reset()

    def reset(self):
        self.failures = 0
        self.delay = self.timing.retry_initial

    def next_wait(self):
        self.failures += 1
        over_limit = self.failures >= self.timing.cooldown_after_failures
        wait = self.timing.cooldown if over_limit else self.delay
        self.delay = min(self.delay * self.timing.retry_factor, self.timing.retry_maximum)
        return wait


@dataclass(frozen=True)
class ConnectionEvent:
    kind: str
    time: float
    fields: dict


@dataclass
class _Connection:
    number: int
    opened: float
    batches: int = 0
    packets: int = 0
    last_write: float = None


class ConnectionManager:
    """Run with ``await manager.run()``; cancel the task to stop. Never raises."""

    def __init__(self, link_factory, on_event=None, timing=Timing()):
        self._link_factory = link_factory
        self._on_event = on_event
        self.timing = timing
        self.state = ConnectionState.DISCONNECTED
        self.backoff = Backoff(timing)
        self.acknowledged_by_device = None  # Nothing can confirm this, so it stays None.
        self.batches_accepted_by_windows = 0
        self._lock = threading.Lock()
        self._latest = None
        self._commands = collections.deque()
        self._attempts = 0
        self._connections = 0
        self._token = 0
        self._link_lost = False
        self._closing = False
        self._connection = None
        self._refresh_services = False
        self._stop_requested = False
        self._loop = None

    # Thread-safe inputs. Colors are latest-wins; commands are sent once, in order.
    def publish(self, colors):
        with self._lock:
            self._latest = colors

    def send(self, commands):
        with self._lock:
            self._commands.append(list(commands))

    def request_stop(self):
        """Mark the manager as stopping. Backs up cancellation, which a call can swallow."""
        self._stop_requested = True

    def _check_stop(self):
        if self._stop_requested:
            raise asyncio.CancelledError

    def _now(self):
        return self._loop.time()

    def _emit(self, kind, **fields):
        if self._on_event is None:
            return
        try:
            self._on_event(ConnectionEvent(kind, self._now(), fields))
        except Exception:
            logger.exception('Connection event handler failed')

    def _set_state(self, state):
        if state == self.state:
            return
        if state not in TRANSITIONS[self.state]:
            raise RuntimeError(f'Invalid transition: {self.state.value} to {state.value}')
        previous, self.state = self.state, state
        self._emit('state', state=state, previous=previous)

    async def run(self):
        self._loop = asyncio.get_running_loop()
        try:
            while True:
                failure, lifetime = await self._attempt()
                self._check_stop()
                await asyncio.sleep(self._plan_retry(failure, lifetime))
                self._set_state(ConnectionState.SCANNING)
        except asyncio.CancelledError:
            self._set_state(ConnectionState.STOPPING)
        finally:
            self._set_state(ConnectionState.STOPPING)
            self._set_state(ConnectionState.DISCONNECTED)
            self._emit('stopped')

    async def _attempt(self):
        self._attempts += 1
        self._token += 1
        self._link_lost = self._closing = False
        self._connection = None
        self._set_state(ConnectionState.SCANNING)
        self._emit('reconnect_attempt', attempt=self._attempts, uncached_services=self._refresh_services)
        link, failure, phase = None, None, 'scan'
        try:
            link = self._link_factory(on_disconnect=functools.partial(self._disconnect_signal, self._token),
                                      cached_services=not self._refresh_services)
            await asyncio.wait_for(link.locate(), self.timing.scan_deadline)
            self._check_stop()
            phase = 'connect'
            self._set_state(ConnectionState.CONNECTING)
            await asyncio.wait_for(link.connect(), self.timing.connect_deadline)
            self._check_stop()
            self._refresh_services = False
            self._connections += 1
            self._connection = _Connection(self._connections, self._now())
            phase = 'write'
            self._set_state(ConnectionState.CONNECTED)
            self._emit('connection_opened', connection=self._connections)
            await self._pump(link)
        except asyncio.CancelledError:
            self._set_state(ConnectionState.STOPPING)
            raise
        except Exception as error:
            failure = classify(error, phase)
            self._emit('failure', failure=failure, phase=phase, error=type(error).__name__,
                       detail=str(error)[:160], connection=self._connection and self._connection.number)
        finally:
            lifetime = await self._close(link, failure)
        return failure, lifetime

    async def _pump(self, link):
        last_colors = None
        last_sent = float('-inf')
        while True:
            self._check_stop()
            if self._link_lost or not link.is_connected:
                raise _LinkLost('Device disconnected')
            await self._send_commands(link)
            with self._lock:
                colors = self._latest
            if colors is not None and colors != last_colors \
                    and self._now() - last_sent >= self.timing.minimum_batch_interval:
                commands = grouped_packets(colors)
                for command in commands:
                    await self._write(link, command)
                    await asyncio.sleep(self.timing.packet_gap)
                last_colors = colors
                last_sent = self._now()
                self._connection.batches += 1
                self.batches_accepted_by_windows += 1
                self._emit('batch_accepted_by_windows', packets=len(commands),
                           connection=self._connection.number, acknowledged_by_device=False)
            await asyncio.sleep(self.timing.poll)

    async def _send_commands(self, link):
        while True:
            with self._lock:
                if not self._commands:
                    return
                commands = self._commands.popleft()
            for command in commands:
                await self._write(link, command)
                await asyncio.sleep(self.timing.command_gap)

    async def _write(self, link, data):
        await asyncio.wait_for(link.write(data), self.timing.write_timeout)
        self._connection.packets += 1
        self._connection.last_write = self._now()

    def _disconnect_signal(self, token):
        # bleak may call this from another thread.
        self._loop.call_soon_threadsafe(self._on_disconnect, token)

    def _on_disconnect(self, token):
        current = token == self._token
        connection = self._connection if current else None
        self._emit('disconnect_callback', stale=not current, expected=current and self._closing,
                   connection=connection and connection.number,
                   lifetime=connection and self._now() - connection.opened,
                   seconds_since_last_write=self._since_last_write(connection))
        if current and not self._closing:
            self._link_lost = True

    def _since_last_write(self, connection):
        if connection is None or connection.last_write is None:
            return None
        return self._now() - connection.last_write

    async def _close(self, link, failure):
        self._closing = True
        connection = self._connection
        lifetime = None
        if connection is not None:
            lifetime = self._now() - connection.opened
            self._emit('connection_closed', connection=connection.number, lifetime=lifetime,
                       batches_accepted_by_windows=connection.batches, packets_accepted_by_windows=connection.packets,
                       seconds_since_last_write=self._since_last_write(connection), cause=failure)
        if link is not None:
            try:
                await asyncio.wait_for(link.disconnect(), self.timing.disconnect_timeout)
            except (Exception, asyncio.CancelledError) as error:
                self._emit('disconnect_failed', error=type(error).__name__, detail=str(error)[:160])
        return lifetime

    def _plan_retry(self, failure, lifetime):
        if failure is Failure.RADIO_OFF:
            # Kept apart from connection failures: it leaves the retry delay untouched.
            self._set_state(ConnectionState.RADIO_OFF)
            wait = self.timing.radio_retry
        else:
            self._set_state(ConnectionState.RECOVERING)
            if lifetime is not None and lifetime >= self.timing.stable_connection:
                self.backoff.reset()
            wait = self.backoff.next_wait()
        if failure in (Failure.SERVICE_DISCOVERY, Failure.STALE_CHARACTERISTIC):
            self._refresh_services = True
        self._emit('retry_scheduled', failure=failure, wait=wait, consecutive_failures=self.backoff.failures)
        return wait


class BluetoothWorker:
    """Runs one manager on its own thread with a bounded, observable shutdown."""

    def __init__(self, manager, on_exit=None):
        self.manager = manager
        self._on_exit = on_exit
        self._lock = threading.Lock()
        self._loop = None
        self._task = None
        self._stop_requested = False
        self.thread = threading.Thread(target=self._run, name='glowshift-bluetooth', daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        error = None
        try:
            asyncio.run(self._main())
        except Exception as caught:
            error = caught
            logger.exception('Bluetooth worker terminated')
        finally:
            if self._on_exit is not None:
                self._on_exit(error)

    async def _main(self):
        task = asyncio.create_task(self.manager.run())
        with self._lock:
            self._loop, self._task = asyncio.get_running_loop(), task
            stop_now = self._stop_requested
        if stop_now:
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Cancelled before its first step, so there was nothing to clean up.

    def request_stop(self):
        """Ask the worker to stop. Returns immediately and is safe from any thread."""
        self.manager.request_stop()
        with self._lock:
            self._stop_requested = True
            loop, task = self._loop, self._task
        if loop is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The loop has already finished.

    def stop(self, timeout):
        """Stop and wait. Returns False if the thread is still alive after ``timeout``."""
        self.request_stop()
        self.thread.join(timeout)
        return not self.thread.is_alive()
