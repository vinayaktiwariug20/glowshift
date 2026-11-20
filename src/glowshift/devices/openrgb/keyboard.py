"""Find a keyboard in OpenRGB's device list and map grid colours to its keys."""
from dataclasses import dataclass

from glowshift.devices.openrgb.client import DEVICE_TYPE_KEYBOARD, NO_LED


class NoKeyboard(Exception):
    """OpenRGB lists no keyboard with a key layout."""


@dataclass(frozen=True)
class Layout:
    index: int  # The controller's index in OpenRGB.
    name: str
    rows: int
    columns: int
    led_count: int
    keys: tuple  # (row, column, LED index within the device)


def find_keyboard(controllers):
    """The first keyboard with a matrix zone, as a ``Layout``."""
    for controller in controllers:
        if controller.type != DEVICE_TYPE_KEYBOARD:
            continue
        for zone in controller.zones:
            if zone.matrix and zone.matrix[0]:
                keys = tuple((row, column, zone.start + value)
                             for row, line in enumerate(zone.matrix)
                             for column, value in enumerate(line)
                             if value != NO_LED and value < zone.led_count)
                if keys:
                    return Layout(controller.index, controller.name, len(zone.matrix), len(zone.matrix[0]),
                                  controller.led_count, keys), controller
    raise NoKeyboard('OpenRGB lists no keyboard with a key layout')


def led_colors(grid, layout):
    """One colour per LED. LEDs outside the key grid take the grid's average colour."""
    cells = [color for row in grid for color in row]
    average = tuple(round(sum(color[channel] for color in cells) / len(cells)) for channel in range(3))
    colors = [average] * layout.led_count
    for row, column, led in layout.keys:
        colors[led] = tuple(grid[row][column])
    return colors
