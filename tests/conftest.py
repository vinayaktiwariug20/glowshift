import faulthandler
import json
import os
import sys
import threading
from pathlib import Path

import pytest

import legacy_loader

TEST_TIMEOUT = 30.0  # Wall-clock seconds per test; override with @pytest.mark.timeout(seconds).
TIMEOUT_EXIT_CODE = 3


def pytest_configure(config):
    config.addinivalue_line('markers', 'timeout(seconds): wall-clock limit for one test')


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """A hung test cannot be interrupted from Python, so name it, dump every thread and exit."""
    marker = item.get_closest_marker('timeout')
    limit = marker.args[0] if marker else TEST_TIMEOUT

    def expire():
        capture = item.config.pluginmanager.getplugin('capturemanager')
        if capture is not None:
            capture.suspend_global_capture(in_=True)  # Otherwise the report is lost on exit.
        sys.stderr.write(f'\n\nTIMEOUT: {item.nodeid} still running after {limit:.0f} s. Threads:\n')
        sys.stderr.flush()
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        os._exit(TIMEOUT_EXIT_CODE)

    timer = threading.Timer(limit, expire)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


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
def ble_timeline_untied():
    path = Path(__file__).parent / 'golden' / 'ble_timeline_untied.json'
    return json.loads(path.read_text(encoding='utf-8'))['timelines']


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
