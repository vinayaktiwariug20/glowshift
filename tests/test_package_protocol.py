"""H617A packet format, stated independently of the baseline. Needs no legacy/ directory."""
import functools

import pytest

from glowshift.devices.govee import protocol


def test_constants():
    assert protocol.WRITE_UUID == '00010203-0405-0607-0809-0a0b0c0d2b11'
    assert protocol.SEGMENTS == 15
    assert protocol.ALL_SEGMENTS == 0x7fff


@pytest.mark.parametrize('built, expected', [
    (protocol.power_packet(True), '3301010000000000000000000000000000000033'),
    (protocol.power_packet(False), '3301000000000000000000000000000000000032'),
    (protocol.brightness_packet(50), '3304320000000000000000000000000000000005'),
    (protocol.segment_packet((255, 0, 0), protocol.ALL_SEGMENTS), '33051501ff00000000000000ff7f00000000005d'),
    (protocol.segment_packet((0, 0, 255), 1 << 14), '330515010000ff0000000000004000000000009d'),
])
def test_known_packets(built, expected):
    assert built.hex() == expected


def test_length_and_checksum():
    for item in (protocol.power_packet(True), protocol.segment_packet((1, 2, 3), 0x2a55)):
        assert len(item) == 20
        assert functools.reduce(lambda total, byte: total ^ byte, item, 0) == 0


def test_payload_over_19_bytes_is_rejected():
    assert len(protocol.packet([0] * 19)) == 20
    with pytest.raises(ValueError):
        protocol.packet([0] * 20)


@pytest.mark.parametrize('level, sent', [(-5, 1), (0, 1), (1, 1), (100, 100), (250, 100)])
def test_brightness_is_clamped_like_the_studio(level, sent):
    assert protocol.brightness_packet(level)[2] == sent


def test_equal_colors_share_one_packet_in_first_seen_order():
    colors = [(1, 1, 1), (2, 2, 2)] * 7 + [(1, 1, 1)]
    assert protocol.grouped_packets(colors) == [
        protocol.segment_packet((1, 1, 1), 0b101010101010101),
        protocol.segment_packet((2, 2, 2), 0b010101010101010),
    ]
