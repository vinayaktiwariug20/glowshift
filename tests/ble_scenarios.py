"""Bluetooth timing scenarios, runnable against the prototype or the package."""
import asyncio
import logging
import queue
import time
import types

from simulated_device import FakeRadio, Step, run_virtual


def _frames(count, spacing, distinct):
    """Colors that change every ``spacing`` seconds, in ``distinct`` groups."""
    result = []
    for index in range(count):
        colors = [((index * 7 + group) % 256, group * 13, 40)
                  for group in range(distinct) for _ in range(15 // distinct)]
        result.append((index * spacing, colors))
    return result


SCENARIOS = {
    'steady_five_groups': ([Step()], _frames(24, .5, 5), 12.05),
    'steady_fifteen_groups': ([Step()], _frames(16, .5, 15), 8.05),
    'unchanged_colors': ([Step()], _frames(1, .5, 5), 6.05),
    'connect_failures': ([Step(connect=OSError('simulated'))], _frames(1, .5, 5), 150.05),
}

# Recorded for reference only: the package deliberately differs here. See test_ble_timing.py.
LEGACY_DROP_SCENARIO = ([Step(drop_after_writes=5)], _frames(40, .5, 5), 60.05)


async def _feed(frames, publish):
    moment = 0.0
    for at, colors in frames:
        await asyncio.sleep(at - moment)
        moment = at
        publish(colors)


class _LegacyClient:
    def __init__(self, link):
        self.link = link
        self.services = types.SimpleNamespace(get_characteristic=lambda uuid: (
            'characteristic' if link.step.has_characteristic else None))

    @property
    def is_connected(self):
        return self.link.is_connected

    async def connect(self):
        await self.link.connect()

    async def disconnect(self):
        await self.link.disconnect()

    async def write_gatt_char(self, characteristic, data, response):
        assert response is False
        await self.link.write(data)


def run_legacy(ambient, steps, frames, duration):
    radio = FakeRadio(steps)

    async def find_device(address, timeout):
        link = radio.link_factory(lambda: None)
        await link.locate()
        return link

    async def main():
        loop = asyncio.get_running_loop()
        start = loop.time()
        saved = ambient.BleakClient, ambient.BleakScanner, ambient.time
        ambient.BleakClient = lambda device, timeout: _LegacyClient(device)
        ambient.BleakScanner = types.SimpleNamespace(find_device_by_address=find_device)
        ambient.time = types.SimpleNamespace(monotonic=loop.time, strftime=time.strftime)
        engine = ambient.Engine(queue.Queue())

        def publish(colors):
            engine.latest = colors

        task = asyncio.create_task(engine._ble_loop())
        feeder = asyncio.create_task(_feed(frames, publish))
        try:
            await asyncio.sleep(duration)
            engine.stop_flag.set()
            await task
        finally:
            feeder.cancel()
            ambient.BleakClient, ambient.BleakScanner, ambient.time = saved
        return radio.timeline(start)

    logging.disable(logging.CRITICAL)
    try:
        return run_virtual(main)
    finally:
        logging.disable(logging.NOTSET)


def scenarios(run):
    return {name: run(steps, frames, duration) for name, (steps, frames, duration) in SCENARIOS.items()}


def build_timeline_baseline(ambient):
    result = scenarios(lambda *args: run_legacy(ambient, *args))
    result['legacy_drop_reference'] = run_legacy(ambient, *LEGACY_DROP_SCENARIO)
    return {'timelines': result}
