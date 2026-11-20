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
    def __init__(self, tmp_path, address=ADDRESS, keyboard_client_factory=None):
        self.radio = FakeRadio([Step()])
        self.openrgb_calls = 0
        self.keyboard_client_factory = keyboard_client_factory
        self.settings_path = tmp_path / 'settings.json'
        if address:
            settings.save_device_address(address, self.settings_path)
        self.addresses = []
        self.root = tk_window()
        self.app = gui.App(self.root, screen_factory=FakeScreen([dark_blue_cave()]),
                           link_factory_for=self.factory_for, settings_path=self.settings_path, environ={},
                           keyboard_client_factory=self.openrgb)

    def openrgb(self):
        self.openrgb_calls += 1
        if self.keyboard_client_factory is None:
            raise AssertionError('this test must not contact OpenRGB')
        return self.keyboard_client_factory()

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


def test_keyboard_is_off_by_default(harness):
    item = harness()
    assert item.app.screen_sync.keyboard.get() is False
    item.app.screen_sync.interval.set(1)
    item.app.screen_sync.start()
    item.pump(lambda: item.app.screen_sync._shown_colors is not None)
    assert item.openrgb_calls == 0
    assert item.app.screen_sync.keyboard_status.get() == 'Keyboard: Off'


def test_keyboard_mini_screen_is_lit_and_previewed(harness):
    from fake_openrgb import FakeOpenRGB
    fake = FakeOpenRGB()
    try:
        item = harness(keyboard_client_factory=fake.factory())
        tab = item.app.screen_sync
        tab.keyboard.set(True)
        tab.interval.set(1)
        tab.start()
        item.pump(lambda: tab.keyboard_status.get() == 'Keyboard: Lighting Synthetic Keyboard')
        item.pump(lambda: tab._shown_grid is not None)
        assert len(tab._keyboard_cells) == 2 * 3  # The synthetic keyboard's 2 x 3 layout.
        fills = {tab.keyboard_preview.itemcget(cell, 'fill') for cell in tab._keyboard_cells.values()}
        assert fills and '' not in fills
        assert fake.packets(1050)
        assert item.radio.links == []  # The strip stays untouched in preview only.
        tab.stop()
        item.pump(lambda: tab.keyboard_status.get() == 'Keyboard: Stopped')
    finally:
        fake.close()


def test_sky_and_ground_choices(harness):
    from glowshift.capture.loop import UPPER_HALF
    from glowshift.engine.keyboard import LOWER_HALF, MATCH_STRIP, WHOLE_SCREEN
    tab = harness().app.screen_sync
    assert (tab.strip_source.get(), tab.keyboard_source.get()) == ('Whole screen', 'Mini screen: whole screen')
    assert tab.options().strip_band == (0.0, 1.0) and tab.keyboard_choice() is None
    tab.keyboard.set(True)
    assert tab.keyboard_choice() == WHOLE_SCREEN
    tab.strip_source.set('Upper half (sky)')
    tab.keyboard_source.set('Mini screen: lower half (ground)')
    assert tab.options().strip_band == UPPER_HALF and tab.keyboard_choice() == LOWER_HALF
    tab.keyboard_source.set('Match the strip')
    assert tab.keyboard_choice() == MATCH_STRIP
