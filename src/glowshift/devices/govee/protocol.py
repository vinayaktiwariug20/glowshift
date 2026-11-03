"""Govee H617A packet format. Pure functions: no Bluetooth access.

Every packet is 19 bytes, zero-padded, plus one XOR checksum byte.
"""
WRITE_UUID = '00010203-0405-0607-0809-0a0b0c0d2b11'
SEGMENTS = 15
ALL_SEGMENTS = 0x7fff


def packet(values):
    data = list(values)
    if len(data) > 19:
        raise ValueError('Command too long')
    data += [0] * (19 - len(data))
    checksum = 0
    for byte in data:
        checksum ^= byte
    return bytes(data + [checksum])


def power_packet(on):
    return packet([0x33, 0x01, 1 if on else 0])


def brightness_packet(level):
    return packet([0x33, 0x04, max(1, min(100, level))])


def segment_packet(color, mask):
    return packet([0x33, 0x05, 0x15, 0x01, *color, 0, 0, 0, 0, 0,
                   mask & 255, (mask >> 8) & 255])


def grouped_packets(colors):
    masks = {}
    for i, c in enumerate(colors):
        masks[c] = masks.get(c, 0) | (1 << i)
    return [segment_packet(c, mask) for c, mask in masks.items()]
