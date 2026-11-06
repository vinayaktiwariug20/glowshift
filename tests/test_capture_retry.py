"""Screen failures are retried with growing delays; other failures still end the capture."""
import numpy as np
import pytest

from glowshift.capture import loop
from legacy_loader import FakeScreen
from package_adapter import capture_options
from scenes import dark_blue_cave


class RecordingStop:
    """Records each retry wait and advances a fake clock. Stops after ``limit`` retry waits."""

    def __init__(self, limit=50):
        self.waits = []
        self.limit = limit
        self.stopped = False
        self.now = 0.0
        self.ticks = 0

    def is_set(self):
        return self.stopped

    def clock(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds
        if seconds == .15:  # The capture loop's own poll, not a retry.
            self.ticks += 1
            self.stopped = self.ticks > 200
            return
        self.waits.append(seconds)
        if len(self.waits) >= self.limit:
            self.stopped = True


class Screen(FakeScreen):
    """Each grab takes the next outcome: an exception to raise or a frame."""

    def __init__(self, outcomes):
        super().__init__([dark_blue_cave()])
        self.outcomes = list(outcomes)

    def grab(self, monitor):
        outcome = self.outcomes.pop(0) if self.outcomes else self.frames[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run(screen_factory, limit=50, started=None):
    stop = RecordingStop(limit)
    published, unavailable = [], []
    loop.capture_with_retry(capture_options(), stop, published.append,
                            on_started=(lambda: started.append(len(published))) if started is not None else lambda: None,
                            on_unavailable=lambda error, wait: unavailable.append((str(error), wait)),
                            screen_factory=screen_factory, clock=stop.clock)
    return stop, published, unavailable


def test_retry_delays_grow_and_then_repeat():
    stop, published, unavailable = run(Screen([OSError('gone')] * 7), limit=7)
    assert [wait for _, wait in unavailable] == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0]
    assert stop.waits == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0]
    assert published == []


def test_delay_starts_over_after_a_published_frame():
    outcomes = [OSError('a'), OSError('b'), dark_blue_cave(), OSError('c')]
    _, published, unavailable = run(Screen(outcomes), limit=3)
    assert [wait for _, wait in unavailable] == [1.0, 2.0, 1.0]
    assert published


def test_started_is_reported_on_the_first_frame_after_each_recovery():
    started = []
    outcomes = [OSError('a'), dark_blue_cave(), OSError('b'), dark_blue_cave()]
    _, published, unavailable = run(Screen(outcomes), limit=3, started=started)
    assert len(unavailable) == 2
    assert started[0] == 0 and len(started) == 2  # Before the first frame of each recovery.
    assert 0 < started[1] < len(published)


def test_failure_detail_names_the_error():
    _, _, unavailable = run(Screen([OSError('BitBlt failed')]), limit=2)
    assert unavailable[0][0] == 'Screen grab failed: OSError: BitBlt failed'


def test_opening_the_screen_is_retried():
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError('no desktop')
        return Screen([])

    _, published, unavailable = run(factory, limit=5)
    assert [wait for _, wait in unavailable] == [1.0, 2.0]
    assert published


def test_missing_monitor_is_retried():
    class OneMonitor(FakeScreen):
        monitors = [{'width': 640, 'height': 360}]

    _, _, unavailable = run(OneMonitor(), limit=2)
    assert unavailable[0][0] == 'Selected monitor no longer exists'


def test_analysis_errors_are_not_retried():
    with pytest.raises(Exception) as caught:
        run(Screen([np.zeros(3, np.uint8)]))
    assert not isinstance(caught.value, loop.ScreenUnavailable)


def test_stopped_while_waiting_ends_quietly():
    stop, _, unavailable = run(Screen([OSError('x')] * 10), limit=1)
    assert len(unavailable) == 1 and stop.waits == [1.0]
