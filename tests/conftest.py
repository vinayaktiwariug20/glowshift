import json
from pathlib import Path

import pytest

import legacy_loader


@pytest.fixture(scope='session', autouse=True)
def no_real_hardware():
    """Fail any test that reaches real Bluetooth or screen capture."""
    def forbidden(*args, **kwargs):
        raise AssertionError('tests must not touch real hardware')

    patch = pytest.MonkeyPatch()
    bleak = pytest.importorskip('bleak')
    mss = pytest.importorskip('mss')
    patch.setattr(bleak.BleakClient, '__init__', forbidden)
    patch.setattr(bleak.BleakScanner, '__init__', forbidden)
    patch.setattr(bleak.BleakScanner, 'find_device_by_address', forbidden)
    patch.setattr(mss, 'mss', forbidden)
    yield
    patch.undo()


@pytest.fixture(scope='session')
def ble_timeline():
    return json.loads((Path(__file__).parent / 'golden' / 'ble_timeline.json').read_text(encoding='utf-8'))['timelines']


@pytest.fixture(scope='session')
def baseline():
    return json.loads((Path(__file__).parent / 'golden' / 'baseline.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='session')
def sensitivity_baseline():
    return json.loads((Path(__file__).parent / 'golden' / 'sensitivity.json').read_text(encoding='utf-8'))


def _load(name):
    if not legacy_loader.available():
        pytest.skip('legacy/ is local-only and not present')
    return legacy_loader.load(name)


@pytest.fixture(scope='session')
def ambient():
    return _load(legacy_loader.AMBIENT)


@pytest.fixture(scope='session')
def studio():
    return _load(legacy_loader.STUDIO)


@pytest.fixture(scope='session')
def adaptive():
    return _load(legacy_loader.ADAPTIVE)
