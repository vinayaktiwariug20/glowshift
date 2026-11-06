"""Studio controller on a simulated strip, with real threads."""
import threading
import time

import pytest

from glowshift.animations.effects import animation_colors
from glowshift.devices.govee.protocol import brightness_packet, power_packet
from glowshift.engine.studio import Studio
from package_adapter import studio as studio_packets
from simulated_device import FakeRadio, Step

DESIGN = ['#ff6030'] * 5 + ['#00FF00'] * 5 + ['#0000ff'] * 5
CONFIG = {'kind': 'Breathing', 'a': '#ff0000', 'b': '#000000', 'speed': 1.0, 'reverse': False, 'fps': 4.0}


def legacy_design_packets(colors):
    """What Studio v5's Apply design sent: one packet per color, case-insensitive."""
    masks = {}
    for index, color in enumerate(colors):
        masks[color.lower()] = masks.get(color.lower(), 0) | (1 << index)
    return [studio_packets.segment_frame(color, mask).hex() for color, mask in masks.items()]


class Harness:
    def __init__(self, steps=(Step(),)):
        self.radio = FakeRadio(list(steps))
        self.events = []
        self.condition = threading.Condition()
        self.studio = Studio(self.radio.link_factory, self.sink)

    def sink(self, event):
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def written(self):
        return [entry[2] for entry in self.radio.operations('write')]

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, self.events
            time.sleep(.01)

    def texts(self, kind):
        return [event.data['text'] for event in self.events if event.kind == kind]


@pytest.fixture
def harness():
    created = []

    def make(*args):
        created.append(Harness(*args))
        return created[-1]

    yield make
    for item in created:
        item.radio.released.set()
        assert item.studio.close(5)
    assert [t for t in threading.enumerate() if t.name.startswith('glowshift-')] == []


def test_apply_design_sends_studio_v5_packets(harness):
    item = harness()
    item.studio.send_colors(DESIGN)
    expected = legacy_design_packets(DESIGN)
    item.wait_for(lambda: len(item.written()) >= len(expected))
    assert item.written() == expected


def test_power_and_brightness_are_studio_v5_packets_sent_before_colors(harness):
    item = harness()
    item.studio.set_power(True)
    item.studio.set_brightness(140)  # Clamped, as Studio v5 did.
    item.studio.send_colors(DESIGN)
    expected = [power_packet(True).hex(), brightness_packet(100).hex()] + legacy_design_packets(DESIGN)
    item.wait_for(lambda: len(item.written()) >= len(expected))
    assert item.written() == expected
    assert brightness_packet(100) == studio_packets.frame(0x33, 0x04, 100)
    assert power_packet(True) == studio_packets.frame(0x33, 0x01, 1)


def test_state_chosen_while_disconnected_is_sent_on_connect(harness):
    item = harness()
    item.studio._power = False
    item.studio._publish([(1, 2, 3)] * 15, animated=False)
    assert not item.studio.active
    item.studio.connect()
    item.wait_for(lambda: len(item.written()) >= 2)
    assert item.written()[0] == power_packet(False).hex()


def test_connect_twice_uses_one_connection(harness):
    item = harness()
    item.studio.connect()
    item.studio.connect()
    item.wait_for(lambda: item.radio.operations('connect'))
    time.sleep(.2)
    assert len(item.radio.links) == 1


def test_animation_frames_follow_the_studio_effect(harness):
    item = harness()
    item.studio.start_animation(CONFIG)
    item.wait_for(lambda: len(item.written()) >= 6, timeout=8)
    for packet in item.written():
        # Breathing: every segment the same red at 10-100 % intensity, as Studio v5 drew it.
        red = int(packet[8:10], 16)
        assert packet == studio_packets.segment_frame('#%02x0000' % red, 0x7fff).hex()
        assert round(255 * .10) <= red <= 255
    assert animation_colors(CONFIG, 0)[0] == '#%02x0000' % round(255 * .55)
    assert len(set(item.written())) == len(item.written())  # Unchanged frames are not resent.
    assert 'Animation running' in item.texts('animation')


def test_animation_runs_at_about_four_frames_per_second(harness):
    item = harness()
    item.studio.start_animation(CONFIG)
    item.wait_for(lambda: item.radio.operations('write'))
    first = len(item.written())
    time.sleep(2)
    assert 5 <= len(item.written()) - first <= 10


def test_stopping_an_animation_leaves_its_last_frame(harness):
    item = harness()
    item.studio.start_animation(CONFIG)
    item.wait_for(lambda: len(item.written()) >= 3, timeout=8)
    item.studio.stop_animation()
    time.sleep(.5)
    count = len(item.written())
    time.sleep(1)
    assert len(item.written()) == count  # Nothing new until the 5 s idle resend.
    assert not item.studio.animating
    assert item.texts('animation')[-1] == 'Animation stopped'


def test_sending_a_design_ends_the_animation(harness):
    item = harness()
    item.studio.start_animation(CONFIG)
    item.studio.send_colors(DESIGN)
    assert not item.studio.animating
    expected = legacy_design_packets(DESIGN)
    item.wait_for(lambda: item.written()[-len(expected):] == expected, timeout=8)


def test_disconnect_releases_the_strip(harness):
    item = harness()
    item.studio.send_colors(DESIGN)
    item.wait_for(lambda: item.written())
    item.studio.disconnect()
    item.wait_for(lambda: not item.studio.active)
    assert len(item.radio.operations('disconnect')) == 1
    assert item.texts('bluetooth')[-1] == 'Disconnected'


def test_status_never_claims_delivery(harness):
    item = harness()
    item.studio.send_colors(DESIGN)
    item.wait_for(lambda: any('accepted by Windows' in text for text in item.texts('bluetooth')))
    assert all('not confirmed' in text for text in item.texts('bluetooth') if 'accepted by Windows' in text)


def test_default_frame_rate_is_studio_v5s():
    from glowshift.engine import studio
    assert studio.FRAME_RATE == 4.0


def test_a_stopped_animation_returns_to_normal_pacing(harness):
    item = harness()
    item.studio.start_animation(CONFIG)
    item.wait_for(lambda: item.written(), timeout=8)
    assert item.studio._worker.manager._pacing() == (.10, .02)
    item.studio.stop_animation()
    assert item.studio._worker.manager._pacing() == (1.2, .15)


def test_close_without_connecting_is_harmless():
    assert Studio(None, lambda event: None).close() is True
