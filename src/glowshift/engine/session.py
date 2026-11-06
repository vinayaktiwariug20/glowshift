"""One ambient run: capture and Bluetooth as independent workers.

Every event carries its run id. ``StatusModel`` is the single source of truth for
what a GUI shows, and ignores events from any run but the current one.
"""
import logging
import threading
import time
from dataclasses import dataclass, field

from glowshift.capture.loop import capture_with_retry, open_screen
from glowshift.devices.govee.connection import BluetoothWorker, ConnectionManager, ConnectionState

logger = logging.getLogger('glowshift.session')

SHUTDOWN_TIMEOUT = 8.0  # Above the 5-second bound on disconnecting.

BLUETOOTH_TEXT = {
    ConnectionState.DISCONNECTED: 'Stopped',
    ConnectionState.SCANNING: 'Scanning for H617A...',
    ConnectionState.CONNECTING: 'Connecting; no writes accepted yet',
    ConnectionState.CONNECTED: 'Connected; awaiting first accepted write',
    ConnectionState.RECOVERING: 'Connection lost; recovering. Preview continues',
    ConnectionState.RADIO_OFF: 'Bluetooth appears to be off or unavailable. Preview continues',
    ConnectionState.STOPPING: 'Stopping...',
}


@dataclass(frozen=True)
class SessionEvent:
    run_id: int
    kind: str
    data: dict = field(default_factory=dict)


class _Run:
    def __init__(self, run_id, options, connect):
        self.id = run_id
        self.options = options
        self.connect = connect
        self.stop = threading.Event()
        self.capture_thread = None
        self.worker = None

    def alive(self):
        threads = [self.capture_thread, self.worker and self.worker.thread]
        return any(thread is not None and thread.is_alive() for thread in threads)


class Session:
    def __init__(self, sink, link_factory=None, screen_factory=open_screen, on_connection_event=None):
        self._sink = sink
        self._link_factory = link_factory
        self._screen_factory = screen_factory
        self._on_connection_event = on_connection_event
        self._run = None
        self._next_id = 0

    @property
    def using_bluetooth(self):
        """True while the current run's Bluetooth worker is running, including while it stops."""
        run = self._run
        return run is not None and run.worker is not None and run.worker.thread.is_alive()

    def start(self, options, connect):
        """Start a new run. Returns its id, or None while the previous run is exiting."""
        current = self._run
        if current is not None and current.alive():
            self._sink(SessionEvent(current.id, 'start_refused'))
            return None
        self._next_id += 1
        run = self._run = _Run(self._next_id, options, connect and self._link_factory is not None)
        self._sink(SessionEvent(run.id, 'started', {'connect': run.connect}))
        run.capture_thread = threading.Thread(target=self._capture, args=(run,), name='glowshift-capture', daemon=True)
        if run.connect:
            run.worker = self._make_worker(run)
            run.worker.start()
        run.capture_thread.start()
        return run.id

    def _make_worker(self, run):
        def on_event(event):
            if self._on_connection_event is not None:
                self._on_connection_event(event)
            if event.kind == 'state':
                self._sink(SessionEvent(run.id, 'bluetooth', {'text': BLUETOOTH_TEXT[event.fields['state']]}))
            elif event.kind == 'batch_accepted_by_windows':
                self._sink(SessionEvent(run.id, 'bluetooth', {'text': (
                    f'Writes accepted by Windows at {time.strftime("%H:%M:%S")} '
                    f'({event.fields["packets"]} commands); device delivery not confirmed')}))
            elif event.kind == 'failure':
                self._sink(SessionEvent(run.id, 'bluetooth', {'text': (
                    f'{event.fields["failure"].value}: {event.fields["detail"][:85]}')}))

        def on_exit(error):
            self._sink(SessionEvent(run.id, 'worker_exit', {'worker': 'bluetooth', 'crashed': error is not None}))

        return BluetoothWorker(ConnectionManager(self._link_factory, on_event), on_exit)

    def _capture(self, run):
        def on_colors(colors):
            if run.worker is not None:
                run.worker.manager.publish(colors)
            self._sink(SessionEvent(run.id, 'preview', {'colors': colors}))

        streak = 0

        def on_started():
            nonlocal streak
            if streak:
                logger.info('Screen capture resumed after %d failed attempt(s)', streak)
                streak = 0
            self._sink(SessionEvent(run.id, 'capture', {'text': 'Capturing screen'}))

        def on_unavailable(error, wait):
            nonlocal streak
            streak += 1
            if streak == 1:  # One record per outage; a lock screen can last hours.
                logger.warning('Screen capture unavailable, retrying: %s', error)
            self._sink(SessionEvent(run.id, 'capture', {
                'text': f'Screen unavailable; retrying in {wait:g} s'}))

        try:
            capture_with_retry(run.options, run.stop, on_colors, on_started, on_unavailable, self._screen_factory)
        except Exception:
            logger.exception('Capture worker failed')
            self._sink(SessionEvent(run.id, 'capture_failed'))
            self._request_stop(run)
        finally:
            self._sink(SessionEvent(run.id, 'worker_exit', {'worker': 'capture', 'crashed': False}))

    def _request_stop(self, run):
        run.stop.set()
        if run.worker is not None:
            run.worker.request_stop()

    def stop(self):
        """Ask the current run to stop. Returns immediately; workers report their exit."""
        run = self._run
        if run is None or not run.alive():
            return
        self._sink(SessionEvent(run.id, 'stop_requested'))
        self._request_stop(run)

    def close(self, timeout=SHUTDOWN_TIMEOUT):
        """Stop and wait for every worker. Returns False if any outlived ``timeout``."""
        self.stop()
        run = self._run
        if run is None:
            return True
        deadline = time.monotonic() + timeout
        for thread in (run.capture_thread, run.worker and run.worker.thread):
            if thread is not None and thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))
        if run.alive():
            logger.error('Worker still running after %.1f seconds', timeout)
            self._sink(SessionEvent(run.id, 'shutdown_incomplete'))
            return False
        return True


class StatusModel:
    """What a GUI should display, built only from session events."""

    def __init__(self):
        self.run_id = 0
        self.message = 'Ready. Start with Preview only.'
        self.capture = 'idle'
        self.bluetooth = 'idle'
        self.colors = None
        self.ignored_events = 0
        self._waiting = set()
        self._stopping = False
        self._failed = False

    def apply(self, event):
        """Apply an event. Returns False if it belongs to an older run."""
        if event.kind == 'started' and event.run_id > self.run_id:
            self.run_id = event.run_id
            self._waiting = {'capture', 'bluetooth'} if event.data['connect'] else {'capture'}
            self._stopping = self._failed = False
            self.capture = 'Starting'
            self.bluetooth = 'Starting' if event.data['connect'] else 'Preview only'
            self.message = 'Screen sync running.'
            return True
        if event.run_id != self.run_id:
            self.ignored_events += 1
            return False
        getattr(self, f'_on_{event.kind}')(event.data)
        return True

    def _on_preview(self, data):
        self.colors = data['colors']

    def _on_capture(self, data):
        self.capture = data['text']

    def _on_bluetooth(self, data):
        self.bluetooth = data['text']

    def _on_start_refused(self, data):
        self.message = 'Previous run is still stopping; please wait.'

    def _on_stop_requested(self, data):
        self._stopping = True
        self.message = 'Stopping; waiting for workers to exit...'

    def _on_capture_failed(self, data):
        self._failed = True
        self.capture = 'Capture failed; see log'
        self.message = 'Screen capture failed. See diagnostic log.'

    def _on_shutdown_incomplete(self, data):
        self.message = 'A worker did not exit in time; see diagnostic log.'

    def _on_worker_exit(self, data):
        self._waiting.discard(data['worker'])
        if data['worker'] == 'capture':
            self.capture = 'Capture failed; see log' if self._failed else 'Stopped'
        elif data['crashed']:
            self.bluetooth = 'Worker exited; see log'
        else:
            self.bluetooth = 'Stopped'
        if self._waiting:
            return
        if not self._failed:
            self.message = 'Stopped. You can start again.'
