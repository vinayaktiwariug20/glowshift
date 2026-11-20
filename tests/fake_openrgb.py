"""A fake OpenRGB SDK server on a loopback port, with synthetic devices.

Device data is encoded here straight from OpenRGB's published protocol description,
independently of the client's decoder, so the two check each other.
"""
import socket
import struct
import threading
from dataclasses import dataclass, field

NO_LED = 0xFFFFFFFF
KEYBOARD = 5
MOUSE = 6


def text(value):
    raw = value.encode('utf-8') + b'\0'
    return struct.pack('<H', len(raw)) + raw


@dataclass
class FakeDevice:
    name: str = 'Synthetic Keyboard'
    type: int = KEYBOARD
    vendor: str = 'Synthetic'
    modes: tuple = ('Off', 'Direct')
    active_mode: int = 1
    matrix: list | None = field(default_factory=lambda: [[0, 1, 2], [3, NO_LED, 4]])
    led_count: int = 5
    extra_zone_leds: int = 0  # A second, non-matrix zone after the keys.

    def encode(self, version):
        data = struct.pack('<i', self.type) + text(self.name)
        if version >= 1:
            data += text(self.vendor)
        data += text('fake description') + text('1.0') + text('SERIAL-SYNTHETIC') + text('location-synthetic')
        data += struct.pack('<Hi', len(self.modes), self.active_mode)
        for name in self.modes:
            data += text(name) + struct.pack('<iIII', 0, 0, 0, 0)
            if version >= 3:
                data += struct.pack('<II', 0, 100)
            data += struct.pack('<III', 0, 0, 0)
            if version >= 3:
                data += struct.pack('<I', 100)
            data += struct.pack('<II', 0, 0) + struct.pack('<H', 1) + bytes((1, 2, 3, 0))
        zones = [('Keyboard', 2, self.led_count, self.matrix)]
        if self.extra_zone_leds:
            zones.append(('Logo', 1, self.extra_zone_leds, None))
        data += struct.pack('<H', len(zones))
        for name, zone_type, count, matrix in zones:
            data += text(name) + struct.pack('<iIII', zone_type, count, count, count)
            if matrix:
                height, width = len(matrix), len(matrix[0])
                data += struct.pack('<HII', 8 + 4 * height * width, height, width)
                data += struct.pack(f'<{height * width}I', *[value for row in matrix for value in row])
            else:
                data += struct.pack('<H', 0)
            if version >= 4:
                data += struct.pack('<H', 1) + text('segment') + struct.pack('<iII', 1, 0, count)
        total = self.led_count + self.extra_zone_leds
        data += struct.pack('<H', total)
        for index in range(total):
            data += text(f'Key {index}') + struct.pack('<I', index)
        data += struct.pack('<H', total) + b''.join(bytes((index, 2 * index, 3, 0)) for index in range(total))
        return struct.pack('<I', 4 + len(data)) + data


class FakeOpenRGB:
    """``protocol=None`` imitates a protocol 0 server, which ignores the version request."""

    def __init__(self, devices=None, protocol=4):
        self.devices = devices if devices is not None else [FakeDevice()]
        self.protocol = protocol
        self.received = []  # (packet id, device index, payload)
        self.connections = 0
        self.condition = threading.Condition()
        self._clients = []
        self._listener = socket.socket()
        self._listener.bind(('127.0.0.1', 0))
        self._listener.listen()
        self.port = self._listener.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def factory(self, timeout=.5):
        from glowshift.devices.openrgb.client import OpenRGBClient
        return lambda: OpenRGBClient(port=self.port, timeout=timeout)

    def _accept(self):
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            with self.condition:
                self.connections += 1
                self._clients.append(connection)
                self.condition.notify_all()
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        version = 0
        try:
            while True:
                header = self._exactly(connection, 16)
                magic, device, packet, size = struct.unpack('<4sIII', header)
                assert magic == b'ORGB'
                payload = self._exactly(connection, size)
                with self.condition:
                    self.received.append((packet, device, payload))
                    self.condition.notify_all()
                if packet == 40 and self.protocol is not None:
                    version = min(self.protocol, struct.unpack('<I', payload)[0])
                    self._reply(connection, 0, 40, struct.pack('<I', self.protocol))
                elif packet == 0:
                    self._reply(connection, 0, 0, struct.pack('<I', len(self.devices)))
                elif packet == 1:
                    requested = struct.unpack('<I', payload)[0] if payload else 0
                    assert requested == version
                    self._reply(connection, device, 1, self.devices[device].encode(requested))
        except (OSError, ConnectionError):
            pass

    @staticmethod
    def _exactly(connection, count):
        data = b''
        while len(data) < count:
            chunk = connection.recv(count - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    @staticmethod
    def _reply(connection, device, packet, payload):
        connection.sendall(struct.pack('<4sIII', b'ORGB', device, packet, len(payload)) + payload)

    def announce_device_list_change(self):
        for connection in list(self._clients):
            try:
                self._reply(connection, 0, 100, b'')
            except OSError:
                pass

    def packets(self, packet_id):
        with self.condition:
            return [(device, payload) for packet, device, payload in self.received if packet == packet_id]

    def wait_for(self, predicate, timeout=5):
        with self.condition:
            assert self.condition.wait_for(predicate, timeout), self.received[-5:]

    def drop_clients(self):
        for connection in list(self._clients):
            try:
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            except OSError:
                pass
        self._clients.clear()

    def close(self):
        self._listener.close()
        self.drop_clients()
