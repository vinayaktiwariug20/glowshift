"""Demonstrate the connection manager and session on a simulated device.

Run: python tests/demo_simulated_session.py
No Bluetooth adapter or screen capture is used. Part 1 is on virtual time, so it
shows exact timings. Part 2 uses real threads.
"""
import asyncio
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent / 'src')]

from glowshift.devices.govee.connection import ConnectionManager, RadioOff  # noqa: E402
from glowshift.engine.session import Session, StatusModel  # noqa: E402
from legacy_loader import FakeScreen  # noqa: E402
from package_adapter import capture_options  # noqa: E402
from scenes import dark_blue_cave  # noqa: E402
from simulated_device import HANG, FakeRadio, Step, run_virtual  # noqa: E402

COLORS = [(200, 40, 10)] * 8 + [(10, 40, 200)] * 7
UNREACHABLE = Exception('Could not write value to characteristic 0013: Unreachable')
STORY = [
    Step(drop_after_seconds=12),                     # connects, then drops by itself
    Step(locate=RadioOff('Bluetooth radio is not powered on')),
    Step(write_error=UNREACHABLE, write_error_at=2),  # the error seen on real hardware
    Step(has_characteristic=False),                  # stale service cache
    Step(connect=HANG),                              # connect never finishes
    Step(drop_after_seconds=40),                     # lasts long enough to reset the delay
    Step(write_hang_at=1),                           # write never finishes
    Step(),                                          # healthy until Stop
]


def show(event, start):
    fields = event.fields
    at = f'{event.time - start:8.3f}s'
    if event.kind == 'state':
        print(f'{at}  state      {fields["previous"].value} -> {fields["state"].value}')
    elif event.kind == 'failure':
        print(f'{at}  FAILURE    {fields["failure"].value} during {fields["phase"]}')
    elif event.kind == 'retry_scheduled':
        print(f'{at}  retry      in {fields["wait"]:.2f}s ({fields["consecutive_failures"]} consecutive failures)')
    elif event.kind == 'connection_closed':
        print(f'{at}  closed     lived {fields["lifetime"]:.2f}s, '
              f'{fields["batches_accepted_by_windows"]} batches accepted by Windows')
    elif event.kind == 'disconnect_callback':
        print(f'{at}  callback   disconnect, {fields["seconds_since_last_write"]:.2f}s after last write')
    elif event.kind == 'reconnect_attempt' and fields['uncached_services']:
        print(f'{at}  attempt    without cached services')


def part_one():
    print('PART 1: scripted failures on virtual time\n')
    radio = FakeRadio(STORY)

    async def main():
        start = asyncio.get_running_loop().time()
        manager = ConnectionManager(radio.link_factory, lambda event: show(event, start))
        manager.publish(COLORS)
        task = asyncio.create_task(manager.run())
        await asyncio.sleep(260)
        print(f'{260:8.3f}s  Stop requested')
        task.cancel()
        await task
        return manager

    manager = run_virtual(main)
    print(f'\nBatches accepted by Windows: {manager.batches_accepted_by_windows}')
    print(f'Confirmed by the device:     {manager.acknowledged_by_device} (no acknowledgment exists)')
    print(f'Links opened: {len(radio.links)}, disconnects issued: {len(radio.operations("disconnect"))}')


def part_two():
    print('\nPART 2: real threads, Stop then Start while Bluetooth is failing\n')
    model = StatusModel()
    kinds = Counter()
    lock = threading.Lock()

    def sink(event):
        with lock:
            kinds[event.kind] += 1
            model.apply(event)

    radio = FakeRadio([Step(locate=RadioOff('off')), Step(drop_after_seconds=.2), Step()])
    session = Session(sink, radio.link_factory, FakeScreen([dark_blue_cave()]))
    session.start(capture_options(interval=.05), True)
    time.sleep(.5)
    print(f'During radio-off: previews={kinds["preview"]}  "{model.message}"  Bluetooth: {model.bluetooth}')
    session.stop()
    while session._run.alive():
        time.sleep(.01)
    time.sleep(.05)
    print(f'After Stop:       "{model.message}"')
    previews = kinds['preview']
    session.start(capture_options(interval=.05), True)
    time.sleep(.5)
    print(f'After Start:      new previews={kinds["preview"] - previews}  "{model.message}"')
    started = time.monotonic()
    clean = session.close()
    names = [thread.name for thread in threading.enumerate() if thread.name.startswith('glowshift-')]
    print(f'After close:      clean={clean} in {time.monotonic() - started:.3f}s, workers left={names}')


if __name__ == '__main__':
    part_one()
    part_two()
