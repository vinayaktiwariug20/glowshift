"""The unified window, on a hidden Tk window with a fake screen and simulated strip."""
import gc
import threading
import time

import pytest

from glowshift import settings
from glowshift.gui import app as gui
from legacy_loader import FakeScreen, tk_window
from scenes import dark_blue_cave
from simulated_device import FakeRadio, Step

ADDRESS = 'AA:BB:CC:DD:EE:FF'  # Fictional. Allowed by the privacy test.


class Harness:
    def __init__(self, tmp_path, address=ADDRESS):
        self.radio = FakeRadio([Step()])
        self.settings_path = tmp_path / 'settings.json'
        if address:
            settings.save_device_address(address, self.settings_path)
        self.addresses = []
        self.root = tk_window()
        self.app = gui.App(self.root, screen_factory=FakeScreen([dark_blue_cave()]),
                           link_factory_for=self.factory_for, settings_path=self.settings_path, environ={})

    def factory_for(self, address):
        self.addresses.append(address)
        return self.radio.link_factory

    def pump(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            self.root.update()
            if predicate():
                return
            assert time.monotonic() < deadline, (self.app.status.get(), self.app.screen_sync.status.get(),
                                                 self.app.studio_tab.status.get())
            time.sleep(.01)


@pytest.fixture
def harness(tmp_path):
    created = []

    def make(**kwargs):
        created.append(Harness(tmp_path, **kwargs))
        return created[-1]

    yield make
    for item in created:
        item.radio.released.set()
        try:
            item.app.close()
        except Exception:
            pass  # Already closed by the test.
        assert item.app.shutdown(5)
    assert [t for t in threading.enumerate() if t.name.startswith('glowshift-')] == []
    # A closed window's Tk variables sit in reference cycles. Collect them here, on the
    # main thread: if a later test's Bluetooth thread collected them, each Tk call
    # would block that thread for about a second ("main thread is not in main loop").
    created.clear()
    item = None
    gc.collect()


def test_screen_sync_defaults_are_ambient_v3_5_1_except_atmospheric(harness, baseline):
    tab = harness().app.screen_sync
    fields = baseline['gui_defaults'].keys()
    shown = {field: getattr(tab, field).get() for field in fields}
    assert shown == {**baseline['gui_defaults'], 'color_mode': 'Atmospheric'}


def test_capture_options_come_from_the_controls(harness):
    tab = harness().app.screen_sync
    tab.color_mode.set('Spatial (5x3)')
    tab.brightness.set(40)
    options = tab.options()
    assert (options.monitor, options.color_mode, options.brightness) == (0, 'Spatial (5x3)', 40)
    assert (options.saturation_boost, options.minimum_saturation, options.minimum_value) == (1.5, .60, .75)


def test_preview_only_shows_colors_without_bluetooth(harness):
    item = harness()
    item.app.screen_sync.interval.set(1)
    item.app.screen_sync.start()
    item.pump(lambda: item.app.screen_sync._shown_colors is not None)
    assert item.app.screen_sync.status.get() == 'Screen sync running.'
    assert item.radio.links == []


def test_screen_sync_sends_to_the_strip(harness):
    item = harness()
    item.app.screen_sync.connect.set(True)
    item.app.screen_sync.start()
    item.pump(lambda: 'accepted by Windows' in item.app.screen_sync.ble_status.get())
    assert item.addresses == [ADDRESS]


def test_bluetooth_needs_an_address(harness):
    item = harness(address=None)
    assert item.app.address_text.get() == 'Strip address: not set'
    item.app.screen_sync.connect.set(True)
    item.app.screen_sync.start()
    assert 'Set the strip address first' in item.app.status.get()
    item.app.studio_tab.apply_design()
    assert item.radio.links == [] and not item.app.studio.active


def test_saving_an_address_never_shows_it(harness):
    item = harness(address=None)
    item.app.address_entry.insert(0, ADDRESS.lower())
    item.app.save_address()
    assert item.app.address == ADDRESS
    assert item.app.address_text.get() == 'Strip address: saved'
    assert item.app.address_entry.get() == ''
    assert ADDRESS not in item.app.status.get()
    assert settings.device_address(item.settings_path, environ={}) == ADDRESS


def test_invalid_address_is_rejected(harness):
    item = harness(address=None)
    item.app.address_entry.insert(0, 'not an address')
    item.app.save_address()
    assert item.app.address is None
    assert 'Expected a Bluetooth address' in item.app.status.get()


def test_studio_waits_while_screen_sync_uses_the_strip(harness):
    item = harness()
    item.app.screen_sync.connect.set(True)
    item.app.screen_sync.start()
    item.pump(lambda: item.app.session.using_bluetooth)
    item.app.studio_tab.apply_design()
    item.app.studio_tab.start_animation()
    assert 'Stop it before using Studio' in item.app.status.get()
    assert not item.app.studio.active and not item.app.studio.animating
    assert len(item.radio.links) == 1


def test_studio_may_use_the_strip_beside_preview_only(harness):
    item = harness()
    item.app.screen_sync.start()  # Preview only.
    item.app.studio_tab.apply_design()
    item.pump(lambda: 'accepted by Windows' in item.app.studio_tab.status.get())


def test_screen_sync_takes_the_strip_over_from_studio(harness):
    item = harness()
    item.app.studio_tab.start_animation()
    item.pump(lambda: item.radio.operations('write'))
    item.app.screen_sync.connect.set(True)
    item.app.screen_sync.start()
    assert 'Disconnecting Studio' in item.app.screen_sync.status.get()
    item.pump(lambda: len(item.radio.operations('locate')) == 2)
    assert item.app.session.using_bluetooth
    assert not item.app.studio.active and not item.app.studio.animating
    disconnect = item.radio.operations('disconnect')[0][0]
    second_link = [entry[0] for entry in item.radio.operations('locate')][1]
    assert second_link > disconnect  # Never two connections at once.


def test_studio_painting_and_live_mode(harness):
    item = harness()
    tab = item.app.studio_tab
    tab.selected = '#112233'
    tab.paint(0)
    assert tab.colors[0] == '#112233' and not item.app.studio.active  # Not live: nothing sent.
    tab.live.set(True)
    tab.paint(1)
    item.pump(lambda: 'accepted by Windows' in tab.status.get())


def test_loaded_design_is_shown(harness):
    from glowshift.animations.designs import Design
    tab = harness().app.studio_tab
    tab.show_design(Design(['#abcdef'] * 15, 30))
    assert tab.colors == ['#abcdef'] * 15 and tab.brightness.get() == 30
    assert tab.buttons[14].cget('bg') == '#abcdef'


def test_closing_releases_the_strip(harness):
    item = harness()
    item.app.studio_tab.apply_design()
    item.pump(lambda: item.radio.operations('write'))
    item.app.close()
    assert item.app.shutdown(5)
    assert len(item.radio.operations('disconnect')) == 1


def test_shutdown_alone_releases_studio(harness):
    """__main__ calls shutdown even when the window did not close normally."""
    item = harness()
    item.app.studio_tab.apply_design()
    item.pump(lambda: item.radio.operations('write'))
    assert item.app.shutdown(5)
    assert not item.app.studio.active
    assert len(item.radio.operations('disconnect')) == 1
