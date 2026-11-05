"""The package must reproduce the prototype's recorded Bluetooth timeline exactly."""
import asyncio

import pytest

import ble_scenarios
from glowshift.devices.govee.connection import ConnectionManager, Timing
from simulated_device import FakeRadio, run_virtual


PROTOTYPE_TIMING = Timing(refresh_after_idle=None)  # The prototype never resent unchanged colors.


def run_package(steps, frames, duration, timing=PROTOTYPE_TIMING):
    radio = FakeRadio(steps)

    async def main():
        start = asyncio.get_running_loop().time()
        manager = ConnectionManager(radio.link_factory, timing=timing)
        task = asyncio.create_task(manager.run())
        feeder = asyncio.create_task(ble_scenarios._feed(frames, manager.publish))
        await asyncio.sleep(duration)
        task.cancel()
        await task
        feeder.cancel()
        return radio.timeline(start)

    return run_virtual(main)


@pytest.mark.parametrize('name', ble_scenarios.UNTIED_SCENARIOS)
def test_package_matches_recorded_prototype_timeline(ble_timeline_untied, name):
    # Compared on the untied scenarios: with exact ties, the bounded calls take a
    # different number of loop steps than the prototype's wait_for, which only changes
    # which task the loop runs first at the same instant. See ble_scenarios.UNTIE_OFFSET.
    assert run_package(*ble_scenarios.UNTIED_SCENARIOS[name]) == ble_timeline_untied[name]


def test_untied_recording_has_the_same_timing_as_the_original(ble_timeline, ble_timeline_untied):
    for name in ('unchanged_colors', 'connect_failures', 'steady_fifteen_groups'):
        assert ble_timeline_untied[name] == ble_timeline[name]
    moments = [float(line.split()[0]) for line in ble_timeline_untied['steady_five_groups'] if ' write ' in line]
    assert {round(later - earlier, 3) for earlier, later in zip(moments, moments[1:])} == {.065, 1.415}


@pytest.mark.parametrize('name', ble_scenarios.SCENARIOS)
def test_prototype_still_matches_its_recording(ambient, ble_timeline, name):
    assert ble_scenarios.run_legacy(ambient, *ble_scenarios.SCENARIOS[name]) == ble_timeline[name]


@pytest.mark.parametrize('name', ble_scenarios.UNTIED_SCENARIOS)
def test_prototype_still_matches_its_untied_recording(ambient, ble_timeline_untied, name):
    assert ble_scenarios.run_legacy(ambient, *ble_scenarios.UNTIED_SCENARIOS[name]) == ble_timeline_untied[name]


def test_recorded_timing_values(ble_timeline):
    moments = [float(line.split()[0]) for line in ble_timeline['steady_five_groups'] if ' write ' in line]
    gaps = {round(later - earlier, 3) for earlier, later in zip(moments, moments[1:])}
    assert gaps == {.065, 1.415}
    attempts = [float(line.split()[0]) for line in ble_timeline['connect_failures'] if ' locate' in line]
    waits = [round(later - earlier, 3) for earlier, later in zip(attempts, attempts[1:])]
    assert waits == [3.0, 5.4, 9.72, 17.496, 60.0]


def test_unchanged_colors_are_written_once(ble_timeline):
    assert sum(' write ' in line for line in ble_timeline['unchanged_colors']) == 5


def test_resending_is_the_only_difference_from_the_prototype(ble_timeline_untied):
    """Deliberate difference: unchanged colors are resent 5 s after the last write."""
    steps, frames, duration = ble_scenarios.UNTIED_SCENARIOS['unchanged_colors']
    package = run_package(steps, frames, 15.95, timing=Timing())  # Ends before a fourth batch.
    prototype = ble_timeline_untied['unchanged_colors']
    assert package[:len(prototype)] == prototype
    batches = [package[len(prototype):][index:index + 5] for index in range(0, len(package) - len(prototype), 5)]
    first_write = float(prototype[2].split()[0])
    for number, batch in enumerate(batches, start=1):
        # The same five packets, with the same 65 ms gaps, 5 s after the previous batch ended.
        assert [line.split()[2] for line in batch] == [line.split()[2] for line in prototype[2:]]
        moments = [float(line.split()[0]) for line in batch]
        assert [round(later - earlier, 3) for earlier, later in zip(moments, moments[1:])] == [.065] * 4
        start = moments[0]
        assert start == pytest.approx(first_write + number * (4 * .065 + 5), abs=.16)
    assert len(batches) == 2


def test_drops_after_accepted_writes_back_off_unlike_the_prototype(ble_timeline):
    """Deliberate difference: an accepted write does not reset the retry delay."""
    steps, frames, duration = ble_scenarios.LEGACY_DROP_SCENARIO
    package = [line for line in run_package(steps, frames, duration) if ' locate' in line]
    legacy = [line for line in ble_timeline['legacy_drop_reference'] if ' locate' in line]
    assert len(legacy) == 18
    assert len(package) == 5
