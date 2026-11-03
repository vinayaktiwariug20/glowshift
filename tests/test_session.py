"""Session lifecycle, worker shutdown and status. Runs real threads with fake devices."""
import threading
import time

import numpy as np
import pytest

from glowshift.devices.govee.connection import BluetoothWorker, ConnectionManager, RadioOff
from glowshift.engine.session import Session, SessionEvent, StatusModel
from legacy_loader import FakeScreen
from package_adapter import capture_options
from scenes import dark_blue_cave
from simulated_device import HANG, IGNORE_CANCEL, FakeRadio, Step


class FailingScreen(FakeScreen):
    def grab(self, monitor):
        raise RuntimeError('BitBlt')


class Harness:
    """A session whose events update a StatusModel, as a GUI's poll loop would."""

    def __init__(self, radio=None, screen=None):
        self.model = StatusModel()
        self.events = []
        self.radio = radio
        self.condition = threading.Condition()
        self.session = Session(self.sink, radio.link_factory if radio else None,
                               screen or FakeScreen([dark_blue_cave()]))

    def sink(self, event):
        with self.condition:
            self.events.append(event)
            self.model.apply(event)
            self.condition.notify_all()

    def wait_for(self, predicate, timeout=5):
        with self.condition:
            assert self.condition.wait_for(predicate, timeout), self.model.__dict__

    def count(self, kind):
        return sum(event.kind == kind for event in self.events)

    def start(self, connect=True):
        return self.session.start(capture_options(interval=.01), connect)

    def previews(self):
        return self.count('preview')

    def wait_for_more_previews(self, extra=3):
        target = self.previews() + extra
        self.wait_for(lambda: self.previews() >= target)


@pytest.fixture
def harness():
    created = []

    def make(*args, **kwargs):
        created.append(Harness(*args, **kwargs))
        return created[-1]

    yield make
    for item in created:
        if item.radio:
            item.radio.released.set()
        assert item.session.close(10)


def glowshift_threads():
    return [thread for thread in threading.enumerate() if thread.name.startswith('glowshift-')]


# Reported "Stopped while running" bugs

def test_status_is_running_after_stop_then_start(harness):
    item = harness()
    item.start()
    item.wait_for_more_previews()
    item.session.stop()
    item.wait_for(lambda: item.model.message == 'Stopped. You can start again.')
    item.wait_for(lambda: not item.session._run.alive())
    item.start()
    item.wait_for_more_previews()
    assert item.model.message == 'Screen sync running.'
    assert item.model.capture == 'Capturing screen'


def test_final_status_does_not_depend_on_thread_liveness():
    """A worker's exit event arrives while its thread is still finishing."""
    model = StatusModel()
    model.apply(SessionEvent(1, 'started', {'connect': False}))
    model.apply(SessionEvent(1, 'stop_requested'))
    assert model.message.startswith('Stopping')
    model.apply(SessionEvent(1, 'worker_exit', {'worker': 'capture', 'crashed': False}))
    assert model.message == 'Stopped. You can start again.'


def test_capture_failure_message_survives_the_exit_event(harness):
    item = harness(screen=FailingScreen())
    item.start(connect=False)
    item.wait_for(lambda: item.model.capture == 'Capture failed; see log' and not item.model._waiting)
    assert item.model.message == 'Screen capture failed. See diagnostic log.'


def test_waits_for_both_workers_before_reporting_stopped():
    model = StatusModel()
    model.apply(SessionEvent(1, 'started', {'connect': True}))
    model.apply(SessionEvent(1, 'stop_requested'))
    model.apply(SessionEvent(1, 'worker_exit', {'worker': 'capture', 'crashed': False}))
    assert model.message.startswith('Stopping')
    model.apply(SessionEvent(1, 'worker_exit', {'worker': 'bluetooth', 'crashed': False}))
    assert model.message.startswith('Stopped')


# Stale events

@pytest.mark.parametrize('event', [
    SessionEvent(1, 'worker_exit', {'worker': 'capture', 'crashed': False}),
    SessionEvent(1, 'worker_exit', {'worker': 'bluetooth', 'crashed': True}),
    SessionEvent(1, 'bluetooth', {'text': 'Stopped'}),
    SessionEvent(1, 'capture', {'text': 'Stopped'}),
    SessionEvent(1, 'capture_failed'),
    SessionEvent(1, 'stop_requested'),
    SessionEvent(1, 'preview', {'colors': [(1, 2, 3)]}),
], ids=['capture-exit', 'bluetooth-exit', 'bluetooth-text', 'capture-text', 'capture-failed', 'stop', 'preview'])
def test_stale_events_cannot_change_the_current_run(event):
    model = StatusModel()
    model.apply(SessionEvent(1, 'started', {'connect': True}))
    model.apply(SessionEvent(2, 'started', {'connect': True}))
    before = dict(model.__dict__)
    assert model.apply(event) is False
    assert model.ignored_events == 1
    assert {**model.__dict__, 'ignored_events': 0} == {**before, 'ignored_events': 0}


def test_a_late_started_event_cannot_replace_a_newer_run():
    model = StatusModel()
    model.apply(SessionEvent(5, 'started', {'connect': False}))
    assert model.apply(SessionEvent(4, 'started', {'connect': True})) is False
    assert model.run_id == 5


def test_each_run_has_its_own_id(harness):
    item = harness()
    first = item.start(connect=False)
    item.session.stop()
    item.wait_for(lambda: not item.model._waiting)
    item.wait_for(lambda: not item.session._run.alive())
    assert item.start(connect=False) == first + 1


def test_start_is_refused_while_the_previous_run_is_exiting(harness):
    radio = FakeRadio([Step(locate=IGNORE_CANCEL)])
    item = harness(radio)
    item.start()
    item.wait_for_more_previews()
    item.session.stop()
    assert item.start() is None
    item.wait_for(lambda: item.count('start_refused') == 1)
    assert 'still stopping' in item.model.message


# Capture stays independent of Bluetooth

@pytest.mark.parametrize('step', [
    Step(locate=RadioOff('off')), Step(locate=HANG), Step(connect=HANG), Step(connect=OSError('x')),
    Step(has_characteristic=False), Step(drop_after_writes=1), Step(write_error=OSError('x')),
], ids=['radio-off', 'scan-hang', 'connect-hang', 'connect-error', 'no-characteristic', 'drop', 'write-error'])
def test_preview_continues_through_bluetooth_failures(harness, step):
    item = harness(FakeRadio([step]))
    item.start()
    item.wait_for_more_previews(5)
    time.sleep(.2)
    item.wait_for_more_previews(5)
    assert item.model.message == 'Screen sync running.'


def test_crashed_bluetooth_worker_leaves_capture_running(harness):
    item = harness(FakeRadio([Step()]))
    original = item.session._make_worker

    def make_worker(run):
        worker = original(run)

        async def run_crash():
            raise RuntimeError('simulated crash')

        worker.manager.run = run_crash
        return worker

    item.session._make_worker = make_worker
    item.start()
    item.wait_for(lambda: item.model.bluetooth == 'Worker exited; see log')
    item.wait_for_more_previews()
    assert item.model.message == 'Screen sync running.'


def test_preview_only_never_touches_bluetooth(harness):
    radio = FakeRadio([Step()])
    item = harness(radio)
    item.start(connect=False)
    item.wait_for_more_previews()
    assert radio.links == []
    assert item.model.bluetooth == 'Preview only'


def test_status_text_never_claims_delivery(harness):
    item = harness(FakeRadio([Step()]))
    item.start()
    item.wait_for(lambda: 'accepted by Windows' in item.model.bluetooth)
    assert 'device delivery not confirmed' in item.model.bluetooth


def test_capture_failure_stops_bluetooth_as_the_prototype_did(harness):
    item = harness(FakeRadio([Step()]), FailingScreen())
    item.start()
    item.wait_for(lambda: not item.model._waiting)
    assert item.model.message == 'Screen capture failed. See diagnostic log.'


# Shutdown leaves no workers

@pytest.mark.parametrize('step', [
    Step(), Step(locate=HANG), Step(connect=HANG), Step(write_hang_at=1), Step(locate=RadioOff('off')),
], ids=['connected', 'scanning', 'connecting', 'writing', 'radio-off'])
def test_close_leaves_no_threads(harness, step):
    radio = FakeRadio([step])
    item = harness(radio)
    item.start()
    item.wait_for_more_previews()
    time.sleep(.1)
    started = time.monotonic()
    assert item.session.close() is True
    assert time.monotonic() - started < 2
    assert glowshift_threads() == []
    assert item.model.message.startswith('Stopped')


def test_close_disconnects_an_open_connection(harness):
    radio = FakeRadio([Step()])
    item = harness(radio)
    item.start()
    item.wait_for(lambda: 'accepted by Windows' in item.model.bluetooth)
    item.session.close()
    assert len(radio.operations('disconnect')) == 1


def test_close_reports_a_worker_that_ignores_cancellation(harness):
    radio = FakeRadio([Step(connect=IGNORE_CANCEL)])
    item = harness(radio)
    item.start()
    item.wait_for_more_previews()
    time.sleep(.1)
    assert item.session.close(timeout=.3) is False
    assert item.count('shutdown_incomplete') == 1
    assert 'did not exit' in item.model.message
    assert item.session._run.worker.thread.daemon is True  # Cannot block process exit.
    radio.released.set()
    item.session._run.worker.thread.join(5)
    assert glowshift_threads() == []


def test_stop_before_the_worker_starts_is_safe():
    radio = FakeRadio([Step()])
    worker = BluetoothWorker(ConnectionManager(radio.link_factory))
    worker.request_stop()
    worker.start()
    worker.thread.join(5)
    assert not worker.thread.is_alive()


def test_stop_returns_immediately(harness):
    item = harness(FakeRadio([Step(connect=IGNORE_CANCEL)]))
    item.start()
    item.wait_for_more_previews()
    started = time.monotonic()
    item.session.stop()
    assert time.monotonic() - started < .2


def test_close_without_a_run_is_harmless():
    assert Session(lambda event: None).close() is True


def test_worker_reports_exit_exactly_once(harness):
    item = harness(FakeRadio([Step()]))
    item.start()
    item.wait_for_more_previews()
    item.session.close()
    exits = [event.data['worker'] for event in item.events if event.kind == 'worker_exit']
    assert sorted(exits) == ['bluetooth', 'capture']
