"""Bleak link for the H617A, used by ConnectionManager.

All connection logic lives in connection.py.
"""
import functools
import logging

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakBluetoothNotAvailableError, BleakCharacteristicNotFoundError

from glowshift.devices.govee.connection import (DeviceNotFound, RadioOff, ServiceDiscoveryFailed,
                                                StaleCharacteristic)
from glowshift.devices.govee.protocol import WRITE_UUID

logger = logging.getLogger('glowshift.h617a')

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
            forget_services_changed_handler(self._client)
            await self._client.disconnect()


def forget_services_changed_handler(client):
    """Unregister bleak's services-changed handler before disconnecting.

    bleak 3.0's WinRT ``disconnect()`` closes the device without removing this handler,
    so Windows can still call it afterwards; after the Bluetooth thread's event loop has
    closed, that logs "Event loop is closed" errors. This reaches into bleak internals,
    so it does nothing when they are not as expected.
    """
    backend = getattr(client, '_backend', None)
    requester = getattr(backend, '_requester', None)
    token = getattr(backend, '_services_changed_token', None)
    if requester is None or token is None:
        return
    try:
        requester.remove_gatt_services_changed(token)
    except Exception as error:
        logger.debug('Could not remove the services-changed handler: %r', error)
        return
    backend._services_changed_token = None


def link_factory(address):
    """Return a factory for ConnectionManager. The address comes from local settings."""
    return functools.partial(BleakLink, address)
