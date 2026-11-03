"""Deterministic connection scenarios on virtual time with a scripted fake device."""
import asyncio

import pytest

from glowshift.devices.govee.connection import (TRANSITIONS, Backoff, ConnectionManager, ConnectionState,
                                                DeviceNotFound, Failure, RadioOff, StaleCharacteristic,
                                                Timing, classify)
from glowshift.devices.govee.protocol import grouped_packets, power_packet
from simulated_device import HANG, FakeRadio, Step, run_virtual

COLORS = [(9, 0, 0)] * 3 + [(0, 9, 0)] * 3 + [(0, 0, 9)] * 3 + [(9, 9, 0)] * 3 + [(0, 9, 9)] * 3
S = ConnectionState


def scenario(steps, duration, colors=COLORS, before=None, timing=Timing(), feed=None):
    """Run the manager for ``duration`` virtual seconds, then stop it."""
    radio = FakeRadio(steps)
    events = []

    async def main():
        manager = ConnectionManager(radio.link_factory, events.append, timing)
        if colors:
            manager.publish(colors)
        if before:
            before(manager)
        task = asyncio.create_task(manager.run())
        if feed:
            await feed(manager, radio)
        await asyncio.sleep(duration)
        task.cancel()
        await task
        return manager

    return run_virtual(main), radio, events


def kinds(events, kind):
    return [event for event in events if event.kind == kind]


def states(events):
    return [event.fields['state'] for event in kinds(events, 'state')]


def failures(events):
    return [event.fields['failure'] for event in kinds(events, 'failure')]


def waits(events):
    return [event.fields['wait'] for event in kinds(events, 'retry_scheduled')]


# State machine

def test_every_state_has_a_path_to_disconnected():
    assert set(TRANSITIONS) == set(S)
    for state in S:
        if state is not S.DISCONNECTED:
            assert S.STOPPING in TRANSITIONS[state] or state is S.STOPPING
    assert TRANSITIONS[S.STOPPING] == {S.DISCONNECTED}


def test_invalid_transition_is_rejected():
    manager = ConnectionManager(None)
    with pytest.raises(RuntimeError, match='Invalid transition'):
        manager._set_state(S.CONNECTED)


def test_clean_run_state_sequence():
    manager, _, events = scenario([Step()], 5)
    assert states(events) == [S.SCANNING, S.CONNECTING, S.CONNECTED, S.STOPPING, S.DISCONNECTED]
    assert manager.state is S.DISCONNECTED


# Disconnects

def test_disconnect_callback_records_lifetime_and_last_write():
    _, _, events = scenario([Step(drop_after_seconds=7.0), Step()], 9)
    callback = kinds(events, 'disconnect_callback')[0].fields
    assert callback['lifetime'] == pytest.approx(7.0)
    assert callback['expected'] is False and callback['stale'] is False
    assert callback['seconds_since_last_write'] == pytest.approx(7.0 - .260)
    closed = kinds(events, 'connection_closed')[0].fields
    assert closed['cause'] is Failure.DISCONNECTED
    assert closed['packets_accepted_by_windows'] == 5
    assert closed['batches_accepted_by_windows'] == 1


def test_drop_without_callback_is_found_by_polling():
    _, _, events = scenario([Step(drop_after_seconds=2.0, fire_callback=False), Step()], 4)
    assert failures(events) == [Failure.DISCONNECTED]
    assert kinds(events, 'disconnect_callback') == []


def test_callback_alone_is_enough_to_detect_a_drop():
    async def feed(manager, radio):
        await asyncio.sleep(2)
        radio.links[0].on_disconnect()  # is_connected still reports True

    _, radio, events = scenario([Step(), Step(locate=HANG)], 3, feed=feed)
    assert failures(events) == [Failure.DISCONNECTED]


def test_own_disconnect_is_marked_expected():
    async def main():
        steps = FakeRadio([Step()])
        recorded = []
        manager = ConnectionManager(steps.link_factory, recorded.append)
        task = asyncio.create_task(manager.run())
        await asyncio.sleep(1)
        original = steps.links[0].disconnect

        async def disconnect():
            steps.links[0].on_disconnect()
            await asyncio.sleep(0)
            await original()

        steps.links[0].disconnect = disconnect
        task.cancel()
        await task
        return recorded

    recorded = run_virtual(main)
    assert kinds(recorded, 'disconnect_callback')[0].fields['expected'] is True


def test_late_callback_from_an_old_link_cannot_drop_the_new_connection():
    async def feed(manager, radio):
        await asyncio.sleep(6)
        radio.links[0].on_disconnect()

    _, radio, events = scenario([Step(drop_after_seconds=1), Step()], 10, feed=feed)
    assert len(radio.links) == 2
    assert kinds(events, 'disconnect_callback')[-1].fields['stale'] is True
    assert len(kinds(events, 'connection_opened')) == 2
    assert failures(events) == [Failure.DISCONNECTED]


def test_accepted_writes_do_not_reset_backoff():
    _, _, events = scenario([Step(drop_after_writes=5)], 200)
    assert waits(events)[:6] == pytest.approx([3.0, 5.4, 9.72, 17.496, 60.0, 60.0])


def test_only_a_lasting_connection_resets_backoff():
    _, _, events = scenario([Step(drop_after_seconds=1), Step(drop_after_seconds=1),
                             Step(drop_after_seconds=31), Step(drop_after_seconds=1)], 60)
    assert waits(events)[:3] == pytest.approx([3.0, 5.4, 3.0])


def test_backoff_sequence_matches_prototype():
    backoff = Backoff(Timing())
    assert [backoff.next_wait() for _ in range(7)] == pytest.approx([3.0, 5.4, 9.72, 17.496, 60.0, 60.0, 60.0])


# Timeouts

def test_scan_timeout():
    _, _, events = scenario([Step(locate=HANG), Step()], 17)
    assert failures(events) == [Failure.SCAN_TIMEOUT]
    assert kinds(events, 'failure')[0].time - kinds(events, 'reconnect_attempt')[0].time == pytest.approx(16)


def test_connect_timeout():
    _, _, events = scenario([Step(connect=HANG), Step()], 25)
    assert failures(events) == [Failure.CONNECT_TIMEOUT]
    assert kinds(events, 'failure')[0].time - kinds(events, 'reconnect_attempt')[0].time == pytest.approx(24)


def test_write_timeout():
    _, _, events = scenario([Step(write_hang_at=2), Step()], 6)
    assert failures(events) == [Failure.WRITE_TIMEOUT]
    assert kinds(events, 'failure')[0].fields['phase'] == 'write'


def test_hanging_disconnect_is_bounded_and_reported():
    _, _, events = scenario([Step(drop_after_seconds=1, disconnect=HANG), Step()], 10)
    assert kinds(events, 'disconnect_failed')[0].fields['error'] == 'TimeoutError'
    assert len(kinds(events, 'reconnect_attempt')) == 2


# Failed writes, stale characteristics and service discovery

def test_unreachable_write_is_its_own_category():
    error = Exception('Could not write value to characteristic 0013: Unreachable')
    _, _, events = scenario([Step(write_error=error), Step()], 2)
    assert failures(events) == [Failure.UNREACHABLE]


def test_aborted_write_is_recorded_with_its_detail():
    error = OSError('[WinError -2147467260] Operation aborted')
    _, _, events = scenario([Step(write_error=error, write_error_at=3), Step()], 2)
    fields = kinds(events, 'failure')[0].fields
    assert fields['failure'] is Failure.WRITE_FAILED
    assert fields['error'] == 'OSError' and 'aborted' in fields['detail']
    assert kinds(events, 'connection_closed')[0].fields['packets_accepted_by_windows'] == 2


def test_failed_batch_is_not_counted_as_accepted():
    _, _, events = scenario([Step(write_error=OSError('x'), write_error_at=3), Step(locate=HANG)], 2)
    assert kinds(events, 'batch_accepted_by_windows') == []


@pytest.mark.parametrize('error', [KeyError('char'), ValueError('char'), StaleCharacteristic('char')],
                         ids=['KeyError', 'ValueError', 'StaleCharacteristic'])
def test_stale_characteristic_reconnects_without_cached_services(error):
    _, radio, events = scenario([Step(write_error=error), Step(), Step()], 8)
    assert failures(events) == [Failure.STALE_CHARACTERISTIC]
    assert [link.cached_services for link in radio.links] == [True, False]


def test_missing_characteristic_reconnects_without_cached_services_once():
    _, radio, events = scenario([Step(has_characteristic=False), Step(drop_after_seconds=1), Step()], 15)
    assert failures(events)[0] is Failure.SERVICE_DISCOVERY
    assert [link.cached_services for link in radio.links] == [True, False, True]
    assert kinds(events, 'reconnect_attempt')[1].fields['uncached_services'] is True


def test_device_not_found():
    _, _, events = scenario([Step(locate=DeviceNotFound('none')), Step()], 4)
    assert failures(events) == [Failure.NOT_FOUND]


# Radio off

def test_radio_off_is_separate_from_connection_failures():
    manager, _, events = scenario([Step(locate=RadioOff('off'))], 130)
    assert set(failures(events)) == {Failure.RADIO_OFF}
    assert S.RADIO_OFF in states(events) and S.RECOVERING not in states(events)
    assert waits(events) == [60.0, 60.0, 60.0]
    assert manager.backoff.failures == 0


def test_radio_off_leaves_the_retry_delay_alone():
    _, _, events = scenario([Step(connect=OSError('x')), Step(locate=RadioOff('off')),
                             Step(connect=OSError('x')), Step()], 70)
    assert waits(events) == pytest.approx([3.0, 60.0, 5.4])


def test_radio_restored_reconnects():
    _, _, events = scenario([Step(locate=RadioOff('off')), Step()], 62)
    assert len(kinds(events, 'connection_opened')) == 1


# Cancellation

@pytest.mark.parametrize('step, when, expected_states', [
    (Step(locate=HANG), 1, [S.SCANNING, S.STOPPING, S.DISCONNECTED]),
    (Step(connect=HANG), 1, [S.SCANNING, S.CONNECTING, S.STOPPING, S.DISCONNECTED]),
    (Step(write_hang_at=2), .3, [S.SCANNING, S.CONNECTING, S.CONNECTED, S.STOPPING, S.DISCONNECTED]),
    (Step(), 3, [S.SCANNING, S.CONNECTING, S.CONNECTED, S.STOPPING, S.DISCONNECTED]),
], ids=['during-scan', 'during-connect', 'during-write', 'while-idle'])
def test_stop_is_prompt_and_always_disconnects(step, when, expected_states):
    _, radio, events = scenario([step], when)
    assert states(events) == expected_states
    assert len(radio.operations('disconnect')) == 1
    assert radio.operations('disconnect')[0][0] - 1000.0 == pytest.approx(when)
    assert len(kinds(events, 'stopped')) == 1


def test_stop_during_retry_wait_is_prompt():
    manager, _, events = scenario([Step(connect=OSError('x'))], 1)
    assert states(events)[-3:] == [S.RECOVERING, S.STOPPING, S.DISCONNECTED]
    assert kinds(events, 'stopped')[0].time - 1000.0 == pytest.approx(1)


def test_stop_during_radio_off_wait_is_prompt():
    _, _, events = scenario([Step(locate=RadioOff('off'))], 1)
    assert states(events)[-3:] == [S.RADIO_OFF, S.STOPPING, S.DISCONNECTED]


def test_stop_with_a_hanging_disconnect_is_bounded():
    _, radio, events = scenario([Step(disconnect=HANG)], 1)
    assert kinds(events, 'stopped')[0].time - 1000.0 == pytest.approx(6)
    assert kinds(events, 'disconnect_failed')


def test_stop_holds_even_if_a_call_swallows_cancellation():
    radio = FakeRadio([Step()])
    events = []

    async def main():
        manager = ConnectionManager(radio.link_factory, events.append)
        task = asyncio.create_task(manager.run())
        await asyncio.sleep(1)
        manager.request_stop()  # No task.cancel(), as if the cancellation was lost.
        await task

    run_virtual(main)
    assert kinds(events, 'stopped')
    assert len(radio.links) == 1


def test_stop_is_not_recorded_as_a_failure():
    _, _, events = scenario([Step()], 5)
    assert failures(events) == []
    assert kinds(events, 'connection_closed')[0].fields['cause'] is None


# Serialized writes

def test_writes_never_overlap_and_keep_their_gaps():
    def before(manager):
        manager.send([power_packet(True), power_packet(True)])

    _, radio, _ = scenario([Step()], 3, before=before)
    moments = [entry[0] for entry in radio.operations('write')]
    gaps = [later - earlier for earlier, later in zip(moments, moments[1:])]
    assert gaps[0] == pytest.approx(.08)
    assert all(gap >= .065 - 1e-9 for gap in gaps)


def test_commands_go_first_and_in_order():
    first, second = power_packet(True), power_packet(False)

    def before(manager):
        manager.send([first])
        manager.send([second])

    _, radio, _ = scenario([Step()], 1, before=before)
    written = [entry[2] for entry in radio.operations('write')]
    assert written[:2] == [first.hex(), second.hex()]
    assert written[2:] == [item.hex() for item in grouped_packets(COLORS)]


def test_only_the_latest_colors_are_sent():
    other = [(1, 2, 3)] * 15

    def before(manager):
        manager.publish(other)

    _, radio, _ = scenario([Step()], 1, before=before)
    assert [entry[2] for entry in radio.operations('write')] == [item.hex() for item in grouped_packets(other)]


def test_reconnect_sends_current_colors_again():
    _, radio, _ = scenario([Step(drop_after_seconds=1), Step()], 6)
    assert len(radio.operations('write')) == 10


# Acceptance is not acknowledgment

def test_acceptance_is_never_reported_as_acknowledgment():
    manager, _, events = scenario([Step()], 5)
    assert manager.batches_accepted_by_windows == 1
    assert manager.acknowledged_by_device is None
    assert kinds(events, 'batch_accepted_by_windows')[0].fields['acknowledged_by_device'] is False


def test_writes_use_exact_packets():
    _, radio, _ = scenario([Step()], 1)
    assert [entry[2] for entry in radio.operations('write')] == [item.hex() for item in grouped_packets(COLORS)]


def test_no_packets_are_sent_while_idle():
    _, radio, _ = scenario([Step()], 120)
    assert len(radio.operations('write')) == 5


# Classification and reporting

@pytest.mark.parametrize('error, phase, expected', [
    (RadioOff('off'), 'scan', Failure.RADIO_OFF),
    (RadioOff('off'), 'connect', Failure.RADIO_OFF),
    (TimeoutError(), 'scan', Failure.SCAN_TIMEOUT),
    (TimeoutError(), 'connect', Failure.CONNECT_TIMEOUT),
    (TimeoutError(), 'write', Failure.WRITE_TIMEOUT),
    (OSError('x'), 'connect', Failure.CONNECT_FAILED),
    (OSError('x'), 'write', Failure.WRITE_FAILED),
    (ValueError('x'), 'connect', Failure.CONNECT_FAILED),
    (Exception('Could not get GATT services: Unreachable'), 'connect', Failure.UNREACHABLE),
])
def test_classify(error, phase, expected):
    assert classify(error, phase) is expected


def test_failing_event_handler_does_not_stop_the_manager():
    radio = FakeRadio([Step()])

    def handler(event):
        raise RuntimeError('handler bug')

    async def main():
        manager = ConnectionManager(radio.link_factory, handler)
        manager.publish(COLORS)
        task = asyncio.create_task(manager.run())
        await asyncio.sleep(1)
        task.cancel()
        await task

    run_virtual(main)
    assert len(radio.operations('write')) == 5
