"""Bleak link for the H617A, used by ConnectionManager.

Not yet run against a physical strip. All connection logic lives in connection.py.
"""
import functools

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakBluetoothNotAvailableError, BleakCharacteristicNotFoundError

from glowshift.devices.govee.connection import (DeviceNotFound, RadioOff, ServiceDiscoveryFailed,
                                                StaleCharacteristic)
from glowshift.devices.govee.protocol import WRITE_UUID

SCAN_TIMEOUT = 12
CLIENT_TIMEOUT = 18


class BleakLink:
    def __init__(self, address, on_disconnect, cached_services=True):
        self._address = address
        self._on_disconnect = on_disconnect
        self._cached_services = cached_services
        self._device = None
        self._client = None
        self._characteristic = None

    async def locate(self):
        try:
            self._device = await BleakScanner.find_device_by_address(self._address, timeout=SCAN_TIMEOUT)
        except BleakBluetoothNotAvailableError as error:
            raise RadioOff(str(error)) from error
        if self._device is None:
            raise DeviceNotFound('H617A not found in scan')

    async def connect(self):
        options = {} if self._cached_services else {'use_cached_services': False}
        self._client = BleakClient(self._device, disconnected_callback=lambda _: self._on_disconnect(),
                                   timeout=CLIENT_TIMEOUT, winrt=options)
        try:
            await self._client.connect()
        except BleakBluetoothNotAvailableError as error:
            raise RadioOff(str(error)) from error
        self._characteristic = self._client.services.get_characteristic(WRITE_UUID)
        if self._characteristic is None:
            raise ServiceDiscoveryFailed('Govee write characteristic missing')

    @property
    def is_connected(self):
        return self._client is not None and self._client.is_connected

    async def write(self, data):
        try:
            await self._client.write_gatt_char(self._characteristic, data, response=False)
        except BleakCharacteristicNotFoundError as error:
            raise StaleCharacteristic(str(error)) from error

    async def disconnect(self):
        if self._client is not None:
            await self._client.disconnect()


def link_factory(address):
    """Return a factory for ConnectionManager. The address comes from local settings."""
    return functools.partial(BleakLink, address)
