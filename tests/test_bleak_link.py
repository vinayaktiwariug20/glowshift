"""BleakLink against fake bleak classes. Never touches a real adapter."""
import asyncio
import types

import pytest
from bleak.exc import (BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason,
                       BleakCharacteristicNotFoundError)

from glowshift.devices.govee import h617a
from glowshift.devices.govee.connection import (ConnectionManager, DeviceNotFound, RadioOff,
                                                ServiceDiscoveryFailed, StaleCharacteristic)
from glowshift.devices.govee.protocol import WRITE_UUID
from simulated_device import run_virtual

ADDRESS = 'AA:BB:CC:DD:EE:FF'  # Fictional. Allowed by the privacy test.
RADIO_ERROR = BleakBluetoothNotAvailableError('off', BleakBluetoothNotAvailableReason.POWERED_OFF)


class FakeClient:
    instances = []
    characteristic = 'characteristic'
    connect_error = None
    write_error = None

    def __init__(self, device, disconnected_callback=None, timeout=None, winrt=None):
        self.device, self.callback, self.timeout, self.winrt = device, disconnected_callback, timeout, winrt
        self.is_connected = False
        self.writes = []
        self.disconnects = 0
        self.services = types.SimpleNamespace(get_characteristic=self._characteristic)
        FakeClient.instances.append(self)

    def _characteristic(self, uuid):
        assert uuid == WRITE_UUID
        return self.characteristic

    async def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.is_connected = True

    async def write_gatt_char(self, characteristic, data, response):
        if self.write_error:
            raise self.write_error
        self.writes.append((characteristic, data, response))

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False


@pytest.fixture
def client(monkeypatch):
    class Client(FakeClient):
        instances = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            Client.instances.append(self)

    async def find(address, timeout):
        assert (address, timeout) == (ADDRESS, 12)
        return Client.device

    Client.device = 'device'
    monkeypatch.setattr(h617a, 'BleakClient', Client)
    monkeypatch.setattr(h617a, 'BleakScanner', types.SimpleNamespace(find_device_by_address=find))
    return Client


def open_link(cached_services=True, on_disconnect=lambda: None):
    link = h617a.BleakLink(ADDRESS, on_disconnect, cached_services)

    async def main():
        await link.locate()
        await link.connect()

    asyncio.run(main())
    return link


def test_connects_to_the_scanned_device_with_prototype_timeout(client):
    open_link()
    assert client.instances[0].device == 'device'
    assert client.instances[0].timeout == 18
    assert client.instances[0].winrt == {}


def test_uncached_services_are_requested(client):
    open_link(cached_services=False)
    assert client.instances[0].winrt == {'use_cached_services': False}


def test_writes_without_response_to_the_characteristic(client):
    link = open_link()
    asyncio.run(link.write(b'data'))
    assert client.instances[0].writes == [('characteristic', b'data', False)]


def test_callback_is_forwarded(client):
    calls = []
    open_link(on_disconnect=lambda: calls.append(1))
    client.instances[0].callback(client.instances[0])
    assert calls == [1]


def test_device_not_found(client):
    client.device = None
    with pytest.raises(DeviceNotFound):
        open_link()


def test_radio_off_while_scanning(client, monkeypatch):
    async def find(address, timeout):
        raise RADIO_ERROR

    monkeypatch.setattr(h617a, 'BleakScanner', types.SimpleNamespace(find_device_by_address=find))
    with pytest.raises(RadioOff):
        open_link()


def test_radio_off_while_connecting(client):
    client.connect_error = RADIO_ERROR
    with pytest.raises(RadioOff):
        open_link()


def test_missing_characteristic(client):
    client.characteristic = None
    with pytest.raises(ServiceDiscoveryFailed):
        open_link()


def test_stale_characteristic_while_writing(client):
    link = open_link()
    client.write_error = BleakCharacteristicNotFoundError(WRITE_UUID)
    with pytest.raises(StaleCharacteristic):
        asyncio.run(link.write(b'data'))


def test_disconnect_before_connecting_is_harmless(client):
    asyncio.run(h617a.BleakLink(ADDRESS, lambda: None).disconnect())


def test_manager_drives_the_real_link_class(client):
    client.device = 'device'

    async def main():
        manager = ConnectionManager(h617a.link_factory(ADDRESS))
        manager.publish([(1, 2, 3)] * 15)
        task = asyncio.create_task(manager.run())
        await asyncio.sleep(1)
        task.cancel()
        await task

    run_virtual(main)
    assert len(client.instances[0].writes) == 1
    assert client.instances[0].disconnects == 1
