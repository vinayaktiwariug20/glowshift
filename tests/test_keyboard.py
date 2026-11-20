"""Keyboard mini screen through OpenRGB: client, grid, key mapping, worker and session.

OpenRGB is a fake server on a loopback port; the screen is synthetic.
"""
import struct
import threading
import time

import numpy as np
import pytest

from fake_openrgb import KEYBOARD, MOUSE, NO_LED, FakeDevice, FakeOpenRGB
from glowshift.analysis.grid import FALLBACK, grid_colors
from glowshift.devices.openrgb import client as client_module
from glowshift.devices.openrgb.client import OpenRGBClient, ProtocolError, led_payload, parse_controller
from glowshift.devices.openrgb.keyboard import NoKeyboard, find_keyboard, led_colors
from glowshift.engine import keyboard as keyboard_module
from glowshift.engine.keyboard import KeyboardWorker
from glowshift.engine.session import Session, StatusModel
from legacy_loader import FakeScreen
from package_adapter import capture_options

BLUE = (0, 0, 191)  # Pure blue at the 0.75 minimum intensity.
GREEN = (0, 191, 0)
TUNING = dict(saturation_boost=1.5, minimum_saturation=.60, minimum_value=.75)


def split_frame(height=200, width=300, top=(255, 0, 0), bottom=(0, 255, 0), boundary=98):
    """BGRA: rows above ``boundary`` one colour, the rest another (blue over green by default).

    For a 200-row frame and a 2-row grid, sampled rows 20-95 form the top cell and 98-173
    the bottom one, so a boundary at row 98 keeps each cell a single colour.
    """
    frame = np.zeros((height, width, 4), np.uint8)
    frame[:boundary, :, :3] = top
    frame[boundary:, :, :3] = bottom
    return frame


@pytest.fixture
def server():
    created = []

    def make(*args, **kwargs):
        created.append(FakeOpenRGB(*args, **kwargs))
        return created[-1]

    yield make
    for item in created:
        item.close()


# The client.

@pytest.mark.parametrize('server_protocol, used', [(5, 4), (4, 4), (3, 3), (1, 1)])
def test_client_uses_the_highest_shared_protocol(server, server_protocol, used):
    fake = server(protocol=server_protocol)
    client = fake.factory()()
    assert client.protocol == used
    [keyboard] = client.controllers()
    assert (keyboard.name, keyboard.modes, keyboard.active_mode_name) == ('Synthetic Keyboard', ('Off', 'Direct'),
                                                                           'Direct')
    assert keyboard.vendor == 'Synthetic'
    client.close()


def test_a_server_that_ignores_the_version_request_is_protocol_0(server):
    fake = server(protocol=None)
    client = fake.factory(timeout=.3)()
    assert client.protocol == 0
    [keyboard] = client.controllers()
    assert keyboard.vendor == ''  # Not sent before protocol 1.
    assert fake.packets(1)[0][1] == b''  # Protocol 0 requests carry no version.
    client.close()


def test_client_names_itself(server):
    fake = server()
    fake.factory()().close()
    assert fake.packets(50) == [(0, b'GlowShift\0')]


def test_controller_data_is_parsed_without_keeping_serial_or_location(server):
    fake = server([FakeDevice(extra_zone_leds=2, led_count=5)])
    [keyboard] = fake.factory()().controllers()
    assert keyboard.led_count == 7
    keys, logo = keyboard.zones
    assert keys.matrix == ((0, 1, 2), (3, NO_LED, 4)) and keys.start == 0
    assert (logo.name, logo.start, logo.led_count, logo.matrix) == ('Logo', 5, 2, None)
    assert keyboard.colors == tuple((index, 2 * index, 3) for index in range(7))
    assert 'SERIAL' not in repr(keyboard) and 'location' not in repr(keyboard)


def test_led_update_bytes():
    # data_size counts itself: 4 + 2 + 2 colours x 4 bytes = 14. Each colour is R, G, B, 0.
    assert led_payload([(1, 2, 3), (255, 0, 16)]) == bytes.fromhex('0e000000' '0200' '01020300' 'ff001000')


def test_update_leds_and_custom_mode_packets(server):
    fake = server()
    client = fake.factory()()
    client.set_custom_mode(0)
    client.update_leds(0, [(9, 8, 7)])
    fake.wait_for(lambda: fake.packets(1050))
    assert fake.packets(1100) == [(0, b'')]
    assert fake.packets(1050) == [(0, led_payload([(9, 8, 7)]))]
    client.close()


def test_truncated_controller_data_is_a_protocol_error():
    data = FakeDevice().encode(4)
    with pytest.raises(ProtocolError):
        parse_controller(0, data[:-10], 4)


def test_a_wrong_magic_is_a_protocol_error(server):
    fake = server()
    client = fake.factory()()
    client._socket.close()

    class Garbage:
        def __init__(self, data):
            self.data = data

        def recv(self, count):
            chunk, self.data = self.data[:count], self.data[count:]
            return chunk

    client._socket = Garbage(struct.pack('<4sIII', b'XXXX', 0, 0, 0))
    with pytest.raises(ProtocolError):
        client._packet()


# The mini screen grid.

def test_grid_follows_the_screen_rows():
    grid = grid_colors(split_frame(), 2, 3, **TUNING)
    assert grid == [[BLUE] * 3, [GREEN] * 3]


def test_grid_follows_the_screen_columns():
    frame = np.zeros((200, 300, 4), np.uint8)
    frame[:, :150, :3] = (0, 0, 255)  # Red on the left (BGRA).
    frame[:, 150:, :3] = (255, 0, 0)  # Blue on the right.
    grid = grid_colors(frame, 1, 2, **TUNING)
    assert grid == [[(191, 0, 0), BLUE]]


def test_grid_band_selects_part_of_the_screen():
    assert grid_colors(split_frame(), 2, 2, **TUNING, top=.5, bottom=1.0) == [[GREEN] * 2] * 2


def test_grid_handles_frames_smaller_than_the_grid():
    grid = grid_colors(np.zeros((4, 4, 4), np.uint8), 6, 19, **TUNING)
    assert len(grid) == 6 and all(len(row) == 19 for row in grid)
    assert {color for row in grid for color in row} == {FALLBACK}


def test_grid_on_a_keyboard_sized_layout():
    grid = grid_colors(split_frame(360, 640, boundary=178), 6, 19, **TUNING)  # Rows 3 and 4 meet at 178.
    assert [row[0] for row in grid] == [BLUE] * 3 + [GREEN] * 3


# Key mapping.

def test_find_keyboard_skips_other_devices(server):
    fake = server([FakeDevice(name='Mouse', type=MOUSE), FakeDevice()])
    layout, controller = find_keyboard(fake.factory()().controllers())
    assert (layout.index, layout.name, layout.rows, layout.columns) == (1, 'Synthetic Keyboard', 2, 3)
    assert layout.keys == ((0, 0, 0), (0, 1, 1), (0, 2, 2), (1, 0, 3), (1, 2, 4))


def test_no_keyboard_with_a_layout(server):
    fake = server([FakeDevice(type=MOUSE), FakeDevice(matrix=None)])
    with pytest.raises(NoKeyboard):
        find_keyboard(fake.factory()().controllers())


def test_led_colors_follow_the_matrix_and_fill_other_leds_with_the_average(server):
    fake = server([FakeDevice(matrix=[[1, 0], [NO_LED, 2]], led_count=3, extra_zone_leds=1)])
    layout, _ = find_keyboard(fake.factory()().controllers())
    grid = [[(10, 0, 0), (20, 0, 0)], [(30, 0, 0), (40, 0, 0)]]
    assert led_colors(grid, layout) == [(20, 0, 0), (10, 0, 0), (40, 0, 0), (25, 0, 0)]


# The worker.

class Recorder:
    def __init__(self):
        self.statuses = []
        self.previews = []
        self.exits = []
        self.condition = threading.Condition()

    def status(self, text):
        with self.condition:
            self.statuses.append(text)
            self.condition.notify_all()

    def preview(self, grid):
        with self.condition:
            self.previews.append(grid)
            self.condition.notify_all()

    def exit(self, crashed):
        with self.condition:
            self.exits.append(crashed)
            self.condition.notify_all()

    def wait_for(self, predicate, timeout=5):
        with self.condition:
            assert self.condition.wait_for(predicate, timeout), (self.statuses, len(self.previews))


@pytest.fixture
def worker():
    created = []

    def make(factory, frame=None, **overrides):
        recorder = Recorder()
        options = capture_options(**{'transition': 0, 'brightness': 100, **overrides})
        item = KeyboardWorker(options, recorder.status, recorder.preview, factory, on_exit=recorder.exit)
        if frame is not None:
            item.offer_frame(frame)
        item.start()
        created.append(item)
        return item, recorder

    yield make
    for item in created:
        item.request_stop()
        item.thread.join(5)
        assert not item.thread.is_alive()


@pytest.fixture
def quick_retry(monkeypatch):
    monkeypatch.setattr(keyboard_module, 'RETRY_SECONDS', .05)


def last_leds(fake):
    return fake.packets(1050)[-1][1] if fake.packets(1050) else None


def test_worker_lights_keys_from_the_screen(server, worker):
    fake = server()
    item, recorder = worker(fake.factory(), split_frame())
    fake.wait_for(lambda: fake.packets(1050))
    assert last_leds(fake) == led_payload([BLUE, BLUE, BLUE, GREEN, GREEN])
    recorder.wait_for(lambda: 'Lighting Synthetic Keyboard' in recorder.statuses)
    assert recorder.previews[-1] == [[BLUE] * 3, [GREEN] * 3]


def test_worker_does_not_resend_unchanged_colors(server, worker):
    fake = server()
    worker(fake.factory(), split_frame())
    fake.wait_for(lambda: fake.packets(1050))
    time.sleep(.5)
    assert len(fake.packets(1050)) == 1


def test_worker_applies_brightness(server, worker):
    fake = server()
    worker(fake.factory(), split_frame(), brightness=50)
    fake.wait_for(lambda: fake.packets(1050))
    half_blue, half_green = (0, 0, 96), (0, 96, 0)
    assert last_leds(fake) == led_payload([half_blue] * 3 + [half_green] * 2)


def test_worker_follows_new_frames_smoothly(server, worker):
    fake = server()
    item, recorder = worker(fake.factory(), split_frame(), transition=.5)
    fake.wait_for(lambda: fake.packets(1050))
    item.offer_frame(split_frame(top=(0, 255, 0), bottom=(255, 0, 0)))  # Swap: green over blue.
    fake.wait_for(lambda: last_leds(fake) == led_payload([GREEN] * 3 + [BLUE] * 2), timeout=8)
    assert len(fake.packets(1050)) > 3  # Several in-between steps, not a jump.


def test_worker_leaves_direct_mode_alone(server, worker):
    fake = server()
    worker(fake.factory(), split_frame())
    fake.wait_for(lambda: fake.packets(1050))
    assert fake.packets(1100) == []


def test_worker_asks_for_direct_control_when_needed(server, worker):
    fake = server([FakeDevice(active_mode=0)])  # 'Off'
    worker(fake.factory(), split_frame())
    fake.wait_for(lambda: fake.packets(1050))
    assert fake.packets(1100) == [(0, b'')]


def test_worker_retries_while_openrgb_is_not_running(worker, quick_retry):
    attempts = []

    def refused():
        attempts.append(1)
        raise ConnectionRefusedError

    item, recorder = worker(refused, split_frame())
    recorder.wait_for(lambda: len(attempts) >= 3)
    assert recorder.statuses[0] == 'OpenRGB is not running or its SDK server is off; retrying in 0.05 s'
    assert recorder.previews  # The preview carries on without OpenRGB.


def test_worker_reports_a_missing_keyboard_and_retries(server, worker, quick_retry):
    fake = server([FakeDevice(type=MOUSE)])
    item, recorder = worker(fake.factory(), split_frame())
    fake.wait_for(lambda: fake.connections >= 2)
    assert recorder.statuses[0].startswith('OpenRGB lists no keyboard with a key layout')
    assert fake.packets(1050) == [] and fake.packets(1100) == []


def test_worker_rereads_devices_when_the_list_changes(server, worker):
    fake = server()
    item, recorder = worker(fake.factory(), split_frame())
    fake.wait_for(lambda: fake.packets(1050))
    fake.announce_device_list_change()
    fake.wait_for(lambda: fake.connections == 2 and len(fake.packets(1050)) == 2)
    assert 'OpenRGB device list changed; reconnecting' in recorder.statuses


def test_worker_reconnects_after_openrgb_drops_it(server, worker, quick_retry):
    fake = server()
    item, recorder = worker(fake.factory(), split_frame(), transition=.5)
    fake.wait_for(lambda: fake.packets(1050))
    fake.drop_clients()
    item.offer_frame(split_frame(top=(0, 255, 0)))  # Something new to send.
    fake.wait_for(lambda: fake.connections >= 2 and len(fake.packets(1050)) >= 2, timeout=8)
    assert any(text.startswith('Lost contact with OpenRGB') for text in recorder.statuses)


def test_worker_stops_promptly_and_reports_its_exit(server, worker):
    fake = server()
    item, recorder = worker(fake.factory(), split_frame())
    fake.wait_for(lambda: fake.packets(1050))
    started = time.monotonic()
    item.request_stop()
    item.thread.join(2)
    assert time.monotonic() - started < 1
    assert recorder.statuses[-1] == 'Stopped' and recorder.exits == [False]


def test_worker_crash_is_reported(worker):
    def broken():
        raise ZeroDivisionError

    item, recorder = worker(broken, split_frame())
    recorder.wait_for(lambda: recorder.exits)
    assert recorder.exits == [True]


# The session.

class SessionHarness:
    def __init__(self, client_factory):
        self.model = StatusModel()
        self.events = []
        self.condition = threading.Condition()
        self.session = Session(self.sink, None, FakeScreen([split_frame(360, 640, boundary=180)]),
                               keyboard_client_factory=client_factory)

    def sink(self, event):
        with self.condition:
            self.events.append(event)
            self.model.apply(event)
            self.condition.notify_all()

    def wait_for(self, predicate, timeout=5):
        with self.condition:
            assert self.condition.wait_for(predicate, timeout), self.model.__dict__


def test_session_lights_the_keyboard_beside_the_preview(server):
    fake = server([FakeDevice(matrix=[[0], [1]], led_count=2)])
    harness = SessionHarness(fake.factory())
    harness.session.start(capture_options(interval=.01), connect=False, keyboard=True)
    harness.wait_for(lambda: harness.model.keyboard == 'Lighting Synthetic Keyboard')
    fake.wait_for(lambda: fake.packets(1050))
    harness.wait_for(lambda: harness.model.keyboard_grid is not None)
    assert harness.session.close(5)
    harness.wait_for(lambda: harness.model.message == 'Stopped. You can start again.')
    assert harness.model.keyboard == 'Stopped'
    assert not [t for t in threading.enumerate() if t.name == 'glowshift-keyboard']


def test_session_leaves_openrgb_alone_unless_asked(server):
    fake = server()
    harness = SessionHarness(fake.factory())
    harness.session.start(capture_options(interval=.01), connect=False)
    harness.wait_for(lambda: harness.model.colors is not None)
    assert harness.session.close(5)
    assert fake.connections == 0 and harness.model.keyboard == 'Off'


def test_keyboard_worker_does_not_hold_up_bluetooth_shutdown(worker):
    """A keyboard stuck connecting still stops within the session's shutdown limit."""
    release = threading.Event()

    def slow():
        release.wait(1.5)  # Like a 1.5 s socket timeout.
        raise ConnectionRefusedError

    item, recorder = worker(slow)
    time.sleep(.1)
    started = time.monotonic()
    item.request_stop()
    item.thread.join(5)
    assert not item.thread.is_alive() and time.monotonic() - started < 2.5


# The supervised hardware check tool, against the fake server.

def load_check_tool():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / 'tools' / 'openrgb_keyboard_check.py'
    spec = importlib.util.spec_from_file_location('openrgb_keyboard_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('stage', ['solid', 'orientation', 'grid'])
def test_check_tool_restores_the_previous_colors(server, monkeypatch, capsys, stage):
    fake = server()
    tool = load_check_tool()
    monkeypatch.setattr(tool, 'OpenRGBClient', lambda name: OpenRGBClient(port=fake.port, name=name))
    monkeypatch.setattr(tool.time, 'sleep', lambda seconds: None)
    assert tool.main([stage]) == 0
    expected = 4 if stage == 'solid' else 2  # Each pattern, then the restore.
    fake.wait_for(lambda: len(fake.packets(1050)) == expected)
    sent = fake.packets(1050)
    assert sent[-1][1] == led_payload([(index, 2 * index, 3) for index in range(5)])  # Put back.
    assert fake.packets(1100) == []  # Already in Direct mode.
    assert 'restored the previous colours' in capsys.readouterr().out


def test_check_tool_info_sends_nothing(server, monkeypatch, capsys):
    fake = server()
    tool = load_check_tool()
    monkeypatch.setattr(tool, 'OpenRGBClient', lambda name: OpenRGBClient(port=fake.port, name=name))
    assert tool.main(['info']) == 0
    assert 'Synthetic Keyboard | 2 x 3 grid, 5 keys, 5 LEDs' in capsys.readouterr().out
    assert fake.packets(1050) == [] and fake.packets(1100) == []


# Sky and ground: the strip's band, and what the keyboard shows.

def first_strip_colors(options, frame):
    from glowshift.capture.loop import capture_loop
    stop = threading.Event()
    seen = []

    def on_colors(colors):
        seen.append(colors)
        stop.set()

    capture_loop(options, stop, on_colors, screen_factory=FakeScreen([frame]))
    return seen[0]


def expected_strip(frame, options):
    from glowshift.analysis.ambient import analyze_frame, dim
    return dim(analyze_frame(frame, options.multicolor, options.saturation_boost, options.minimum_saturation,
                             options.minimum_value, options.color_mode, options.spatial_mapping), options.brightness)


def test_strip_shows_the_whole_screen_by_default():
    frame = split_frame(360, 640, boundary=180)
    options = capture_options()
    assert options.strip_band == (0.0, 1.0)
    assert first_strip_colors(options, frame) == expected_strip(frame, options)


def test_strip_can_show_only_the_upper_half():
    from glowshift.capture.loop import UPPER_HALF
    frame = split_frame(360, 640, boundary=180)
    options = capture_options(strip_band=UPPER_HALF, color_mode='Spatial (5x3)')
    colors = first_strip_colors(options, frame)
    assert colors == expected_strip(frame[:180], options)
    assert all(blue > 0 and green == 0 for _red, green, blue in colors)  # Sky only.
    assert colors != expected_strip(frame, options)


def test_strip_grid_spreads_the_strip_left_to_right():
    from glowshift.engine.keyboard import strip_grid
    colors = [(index, 0, 0) for index in range(15)]
    grid = strip_grid(colors, 6, 19)
    assert len(grid) == 6 and all(row == grid[0] for row in grid)
    assert [color[0] for color in grid[0]] == [column * 15 // 19 for column in range(19)]
    assert grid[0][0] == (0, 0, 0) and grid[0][-1] == (14, 0, 0)


def test_keyboard_can_show_the_lower_half(server, worker):
    from glowshift.engine.keyboard import LOWER_HALF
    fake = server()
    recorder = Recorder()
    item = KeyboardWorker(capture_options(transition=0, brightness=100), recorder.status, recorder.preview,
                          fake.factory(), source=LOWER_HALF)
    item.offer_frame(split_frame())
    item.start()
    try:
        fake.wait_for(lambda: fake.packets(1050))
        assert last_leds(fake) == led_payload([GREEN] * 5)  # Ground only.
    finally:
        item.request_stop()
        item.thread.join(5)


def test_keyboard_can_match_the_strip(server):
    from glowshift.engine.keyboard import MATCH_STRIP
    fake = server([FakeDevice(matrix=[[0, 1, 2], [3, 4, 5]], led_count=6)])
    recorder = Recorder()
    item = KeyboardWorker(capture_options(brightness=50), recorder.status, recorder.preview, fake.factory(),
                          source=MATCH_STRIP)
    strip = [(10 * index, 0, 0) for index in range(15)]
    item.offer_strip(strip)
    item.offer_frame(split_frame())  # Ignored: the keyboard copies the strip.
    item.start()
    try:
        fake.wait_for(lambda: fake.packets(1050))
        line = [strip[0], strip[5], strip[10]]  # 15 colours over 3 columns, as already dimmed.
        assert last_leds(fake) == led_payload(line + line)
        assert recorder.previews[-1] == [line, line]
    finally:
        item.request_stop()
        item.thread.join(5)


def test_session_hands_the_strip_colors_to_the_keyboard(server):
    from glowshift.engine.keyboard import MATCH_STRIP
    fake = server([FakeDevice(matrix=[list(range(15))], led_count=15)])
    harness = SessionHarness(fake.factory())
    harness.session.start(capture_options(interval=.01, transition=0), connect=False, keyboard=MATCH_STRIP)
    harness.wait_for(lambda: harness.model.colors is not None and harness.model.keyboard_grid is not None)
    harness.wait_for(lambda: harness.model.keyboard_grid == [list(map(tuple, harness.model.colors))])
    assert harness.session.close(5)
