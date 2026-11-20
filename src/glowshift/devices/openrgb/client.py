"""A minimal client for the OpenRGB SDK server.

Written from OpenRGB's published protocol description (Documentation/OpenRGBSDK.md),
for protocol versions 0 to 4. Integers are little-endian; a colour is red, green, blue
and one padding byte; the data_size fields count their own four bytes.

It sends only these packets: set client name, request protocol version, request
controller count, request controller data, set custom mode and update LEDs. It never
stores a device's serial number or location.
"""
import select
import socket
import struct
from dataclasses import dataclass

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 6742
CLIENT_PROTOCOL = 4
TIMEOUT = 2.0

MAGIC = b'ORGB'
HEADER = struct.Struct('<4sIII')

REQUEST_CONTROLLER_COUNT = 0
REQUEST_CONTROLLER_DATA = 1
REQUEST_PROTOCOL_VERSION = 40
SET_CLIENT_NAME = 50
DEVICE_LIST_UPDATED = 100
UPDATE_LEDS = 1050
SET_CUSTOM_MODE = 1100

DEVICE_TYPE_KEYBOARD = 5
NO_LED = 0xFFFFFFFF
MAX_PAYLOAD = 16 * 1024 * 1024


class ProtocolError(Exception):
    """The server sent something this client cannot read."""


@dataclass(frozen=True)
class Zone:
    name: str
    type: int
    start: int  # Index of the zone's first LED within the device.
    led_count: int
    matrix: tuple | None  # Rows of zone-relative LED indexes, NO_LED where there is no key.


@dataclass(frozen=True)
class Controller:
    index: int
    type: int
    name: str
    vendor: str
    modes: tuple
    active_mode: int
    zones: tuple
    led_count: int
    colors: tuple = ()  # The colours OpenRGB last set, one per LED.

    @property
    def active_mode_name(self):
        return self.modes[self.active_mode] if 0 <= self.active_mode < len(self.modes) else ''


class _Reader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def take(self, fmt):
        layout = struct.Struct('<' + fmt)
        try:
            values = layout.unpack_from(self.data, self.position)
        except struct.error as error:
            raise ProtocolError(f'Controller data ends early: {error}') from None
        self.position += layout.size
        return values if len(values) > 1 else values[0]

    def skip(self, count):
        if self.position + count > len(self.data):
            raise ProtocolError('Controller data ends early')
        self.position += count

    def string(self):
        length = self.take('H')
        self.skip(length)
        return self.data[self.position - length:self.position].split(b'\0', 1)[0].decode('utf-8', 'replace')


def parse_controller(index, data, version):
    """Read a REQUEST_CONTROLLER_DATA response sent for protocol ``version`` (0 to 4)."""
    reader = _Reader(data)
    reader.take('I')  # data_size
    device_type = reader.take('i')
    name = reader.string()
    vendor = reader.string() if version >= 1 else ''
    for _ in range(4):  # Description, version, serial and location: not kept.
        reader.skip(reader.take('H'))
    modes = []
    mode_count = reader.take('H')
    active_mode = reader.take('i')
    for _ in range(mode_count):
        modes.append(reader.string())
        reader.take('iIII')  # value, flags, speed minimum and maximum
        if version >= 3:
            reader.take('II')  # brightness minimum and maximum
        reader.take('III')  # colours minimum and maximum, speed
        if version >= 3:
            reader.take('I')  # brightness
        reader.take('II')  # direction, colour mode
        reader.skip(4 * reader.take('H'))
    zones = []
    start = 0
    for _ in range(reader.take('H')):
        zone_name = reader.string()
        zone_type, _minimum, _maximum, led_count = reader.take('iIII')
        matrix = None
        matrix_size = reader.take('H')
        if matrix_size:
            height, width = reader.take('II')
            if matrix_size != 8 + 4 * height * width:
                raise ProtocolError('Matrix size does not match its dimensions')
            values = reader.take('I' * (height * width)) if height * width else ()
            values = values if isinstance(values, tuple) else (values,)
            matrix = tuple(tuple(values[row * width:(row + 1) * width]) for row in range(height))
        if version >= 4:
            for _ in range(reader.take('H')):
                reader.string()
                reader.take('iII')
        zones.append(Zone(zone_name, zone_type, start, led_count, matrix))
        start += led_count
    led_count = reader.take('H')
    for _ in range(led_count):
        reader.string()
        reader.take('I')
    colors = []
    for _ in range(reader.take('H')):
        red, green, blue, _pad = reader.take('BBBB')
        colors.append((red, green, blue))
    return Controller(index, device_type, name, vendor, tuple(modes), active_mode, tuple(zones), led_count,
                      tuple(colors))


def led_payload(colors):
    """UPDATE_LEDS payload: data_size, colour count, then R, G, B, 0 per LED."""
    body = struct.pack('<H', len(colors)) + b''.join(bytes((r, g, b, 0)) for r, g, b in colors)
    return struct.pack('<I', 4 + len(body)) + body


class OpenRGBClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, name='GlowShift', timeout=TIMEOUT,
                 connect=socket.create_connection):
        self.timeout = timeout
        self.list_changed = False
        self._socket = connect((host, port), timeout)
        try:
            self._socket.settimeout(timeout)
            self._send(0, SET_CLIENT_NAME, name.encode('utf-8') + b'\0')
            self.protocol = self._negotiate()
        except BaseException:
            self.close()
            raise

    def _send(self, device, packet, payload=b''):
        self._socket.sendall(HEADER.pack(MAGIC, device, packet, len(payload)) + payload)

    def _exactly(self, count):
        data = bytearray()
        while len(data) < count:
            chunk = self._socket.recv(count - len(data))
            if not chunk:
                raise ConnectionError('OpenRGB closed the connection')
            data += chunk
        return bytes(data)

    def _packet(self):
        magic, device, packet, size = HEADER.unpack(self._exactly(HEADER.size))
        if magic != MAGIC or size > MAX_PAYLOAD:
            raise ProtocolError('Not an OpenRGB packet')
        payload = self._exactly(size)
        if packet == DEVICE_LIST_UPDATED:
            self.list_changed = True
        return device, packet, payload

    def _receive(self, expected):
        while True:
            _device, packet, payload = self._packet()
            if packet == expected:
                return payload

    def _negotiate(self):
        self._send(0, REQUEST_PROTOCOL_VERSION, struct.pack('<I', CLIENT_PROTOCOL))
        try:
            payload = self._receive(REQUEST_PROTOCOL_VERSION)
        except TimeoutError:
            return 0  # Servers before protocol 1 do not answer.
        if len(payload) < 4:
            raise ProtocolError('Short protocol version')
        return min(CLIENT_PROTOCOL, struct.unpack_from('<I', payload)[0])

    def controllers(self):
        self._send(0, REQUEST_CONTROLLER_COUNT)
        payload = self._receive(REQUEST_CONTROLLER_COUNT)
        if len(payload) < 4:
            raise ProtocolError('Short controller count')
        result = []
        for index in range(struct.unpack_from('<I', payload)[0]):
            request = struct.pack('<I', self.protocol) if self.protocol >= 1 else b''
            self._send(index, REQUEST_CONTROLLER_DATA, request)
            result.append(parse_controller(index, self._receive(REQUEST_CONTROLLER_DATA), self.protocol))
        self.list_changed = False
        return result

    def set_custom_mode(self, index):
        self._send(index, SET_CUSTOM_MODE)

    def update_leds(self, index, colors):
        self._send(index, UPDATE_LEDS, led_payload(colors))

    def poll(self):
        """Read packets the server sent unasked, such as a device list change."""
        while select.select([self._socket], [], [], 0)[0]:
            self._packet()

    def close(self):
        try:
            self._socket.close()
        except OSError:
            pass
