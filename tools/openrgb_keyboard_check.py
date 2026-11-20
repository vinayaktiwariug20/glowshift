"""Supervised check of GlowShift's keyboard output against a real keyboard in OpenRGB.

Each run does one stage, so a person can watch the keyboard between stages:

    info         list what OpenRGB reports for the keyboard; sends nothing to it
    solid        red, then green, then blue, for --seconds each
    orientation  columns go red to blue left to right; the top row is brightest
    grid         a synthetic blue-over-green screen through the mini-screen pipeline

Every stage except ``info`` puts back the colours the keyboard had before it started.
It talks only to the local OpenRGB SDK server, and uses only GlowShift's client: set
client name, read devices, set custom mode (only if the keyboard is not already in
Direct mode) and update LEDs. No serial number or device location is printed.
"""
import argparse
import colorsys
import sys
import time

import numpy as np

from glowshift.analysis.grid import grid_colors
from glowshift.devices.openrgb.client import OpenRGBClient
from glowshift.devices.openrgb.keyboard import find_keyboard, led_colors

MAX_SECONDS = 10.0


def orientation_grid(rows, columns):
    grid = []
    for row in range(rows):
        value = 1.0 - .8 * row / max(1, rows - 1)
        grid.append([tuple(round(255 * c) for c in colorsys.hsv_to_rgb(.66 * column / max(1, columns - 1), 1, value))
                     for column in range(columns)])
    return grid


def synthetic_screen_grid(rows, columns):
    frame = np.zeros((1080, 1920, 4), np.uint8)
    frame[:540, :, :3] = (255, 0, 0)  # BGRA: blue sky
    frame[540:, :, :3] = (0, 255, 0)  # green ground
    return grid_colors(frame, rows, columns, 1.5, .60, .75)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=['info', 'solid', 'orientation', 'grid'])
    parser.add_argument('--seconds', type=float, default=4.0)
    args = parser.parse_args(argv)
    seconds = max(0.5, min(MAX_SECONDS, args.seconds))

    client = OpenRGBClient(name='GlowShift check')
    try:
        print(f'OpenRGB protocol {client.protocol}')
        layout, controller = find_keyboard(client.controllers())
        print(f'Keyboard: {layout.name} | {layout.rows} x {layout.columns} grid, {len(layout.keys)} keys, '
              f'{layout.led_count} LEDs | modes {", ".join(controller.modes)} | active {controller.active_mode_name}')
        if args.stage == 'info':
            return 0
        original = list(controller.colors) or [(0, 0, 0)] * layout.led_count
        if controller.active_mode_name.lower() != 'direct':
            print('Switching to direct control (OpenRGB set custom mode)')
            client.set_custom_mode(layout.index)
        try:
            if args.stage == 'solid':
                for name, color in [('red', (255, 0, 0)), ('green', (0, 255, 0)), ('blue', (0, 0, 255))]:
                    print(f'{time.strftime("%H:%M:%S")} {name}', flush=True)
                    client.update_leds(layout.index, [color] * layout.led_count)
                    time.sleep(seconds)
            else:
                grid = (orientation_grid if args.stage == 'orientation' else synthetic_screen_grid)(
                    layout.rows, layout.columns)
                print(f'{time.strftime("%H:%M:%S")} {args.stage}', flush=True)
                client.update_leds(layout.index, led_colors(grid, layout))
                time.sleep(seconds)
        finally:
            client.update_leds(layout.index, original)
            print(f'{time.strftime("%H:%M:%S")} restored the previous colours', flush=True)
        return 0
    finally:
        client.close()


if __name__ == '__main__':
    sys.exit(main())
