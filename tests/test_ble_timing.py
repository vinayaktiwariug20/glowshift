"""The package must reproduce the prototype's recorded Bluetooth timeline exactly."""
import asyncio

import pytest

import ble_scenarios
from glowshift.devices.govee.connection import ConnectionManager
from simulated_device import FakeRadio, run_virtual


def run_package(steps, frames, duration):
    radio = FakeRadio(steps)

    async def main():
        start = asyncio.get_running_loop().time()
        manager = ConnectionManager(radio.link_factory)
        task = asyncio.create_task(manager.run())
        feeder = asyncio.create_task(ble_scenarios._feed(frames, manager.publish))
        await asyncio.sleep(duration)
        task.cancel()
        await task
        feeder.cancel()
        return radio.timeline(start)

    return run_virtual(main)


@pytest.mark.parametrize('name', ble_scenarios.SCENARIOS)
def test_package_matches_recorded_prototype_timeline(ble_timeline, name):
    assert run_package(*ble_scenarios.SCENARIOS[name]) == ble_timeline[name]


@pytest.mark.parametrize('name', ble_scenarios.SCENARIOS)
def test_prototype_still_matches_its_recording(ambient, ble_timeline, name):
    assert ble_scenarios.run_legacy(ambient, *ble_scenarios.SCENARIOS[name]) == ble_timeline[name]


def test_recorded_timing_values(ble_timeline):
    moments = [float(line.split()[0]) for line in ble_timeline['steady_five_groups'] if ' write ' in line]
    gaps = {round(later - earlier, 3) for earlier, later in zip(moments, moments[1:])}
    assert gaps == {.065, 1.415}
    attempts = [float(line.split()[0]) for line in ble_timeline['connect_failures'] if ' locate' in line]
    waits = [round(later - earlier, 3) for earlier, later in zip(attempts, attempts[1:])]
    assert waits == [3.0, 5.4, 9.72, 17.496, 60.0]


def test_unchanged_colors_are_written_once(ble_timeline):
    assert sum(' write ' in line for line in ble_timeline['unchanged_colors']) == 5


def test_drops_after_accepted_writes_back_off_unlike_the_prototype(ble_timeline):
    """Deliberate difference: an accepted write does not reset the retry delay."""
    steps, frames, duration = ble_scenarios.LEGACY_DROP_SCENARIO
    package = [line for line in run_package(steps, frames, duration) if ' locate' in line]
    legacy = [line for line in ble_timeline['legacy_drop_reference'] if ' locate' in line]
    assert len(legacy) == 18
    assert len(package) == 5
