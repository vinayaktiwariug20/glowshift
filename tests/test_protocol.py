"""H617A packet format, stated independently of the recorded baseline."""
import asyncio
import functools
import types

import pytest

WRITE_UUID = '00010203-0405-0607-0809-0a0b0c0d2b11'


def xor(data):
    return functools.reduce(lambda total, byte: total ^ byte, data, 0)


@pytest.mark.parametrize('name, expected', [
    ('power_on', '3301010000000000000000000000000000000033'),
    ('power_off', '3301000000000000000000000000000000000032'),
    ('segment_red_all', '33051501ff00000000000000ff7f00000000005d'),
    ('segment_blue_15', '330515010000ff0000000000004000000000009d'),
    ('studio_brightness_50', '3304320000000000000000000000000000000005'),
])
def test_known_packets(baseline, name, expected):
    assert baseline['packets'][name] == expected


def test_every_baseline_packet_is_20_bytes_with_xor_checksum(baseline):
    for value in baseline['packets'].values():
        for item in value.split():
            data = bytes.fromhex(item)
            assert len(data) == 20
            assert xor(data) == 0


def test_constants(ambient):
    assert ambient.WRITE_UUID == WRITE_UUID
    assert ambient.SEGMENTS == 15


def test_payload_over_19_bytes_is_rejected(ambient, studio):
    with pytest.raises(ValueError):
        ambient.packet([0] * 20)
    with pytest.raises(ValueError):
        studio.frame(*[0] * 20)


def test_ambient_and_studio_build_identical_segment_packets(ambient, studio):
    assert ambient.segment_packet((18, 52, 86), 0x2a55) == studio.segment_frame('#123456', 0x2a55)


def test_equal_colors_share_one_packet_in_first_seen_order(ambient):
    colors = [(1, 1, 1), (2, 2, 2)] * 7 + [(1, 1, 1)]
    assert ambient.grouped_packets(colors) == [
        ambient.segment_packet((1, 1, 1), 0b101010101010101),
        ambient.segment_packet((2, 2, 2), 0b010101010101010),
    ]


class _FakeClient:
    def __init__(self, device, timeout):
        self.writes = []
        self.is_connected = True
        self.services = types.SimpleNamespace(get_characteristic=lambda uuid: f'char:{uuid}')

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def write_gatt_char(self, characteristic, data, response):
        self.writes.append((characteristic, data, response))
        if len(self.writes) == _FakeClient.stop_after:
            _FakeClient.engine.stop_flag.set()


async def _find_device(address, timeout):
    return object()


def test_ble_loop_writes_grouped_packets_without_response(ambient, monkeypatch):
    """Locks the write path before the connection code is restructured."""
    import queue
    created = []

    def make_client(device, timeout):
        created.append(_FakeClient(device, timeout))
        return created[-1]

    monkeypatch.setattr(ambient, 'BleakClient', make_client)
    monkeypatch.setattr(ambient, 'BleakScanner',
                        types.SimpleNamespace(find_device_by_address=_find_device))
    colors = [color for color in ((9, 0, 0), (0, 9, 0), (0, 0, 9), (9, 9, 0), (0, 9, 9))
              for _ in range(3)]
    engine = ambient.Engine(queue.Queue())
    engine.latest = colors
    _FakeClient.engine = engine
    _FakeClient.stop_after = 5

    asyncio.run(asyncio.wait_for(engine._ble_loop(), timeout=10))

    assert [write[1] for write in created[0].writes] == ambient.grouped_packets(colors)
    assert {write[0] for write in created[0].writes} == {f'char:{WRITE_UUID}'}
    assert {write[2] for write in created[0].writes} == {False}
