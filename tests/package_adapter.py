"""Present the package under the prototype's names so one baseline checks both."""
import types

import numpy as np

import baseline_builder
from glowshift.analysis.ambient import analyze_frame, dim
from glowshift.analysis.experimental.adaptive import adaptive_palette
from glowshift.analysis.smoothing import ColorSmoother
from glowshift.animations.effects import animation_colors
from glowshift.devices.govee.protocol import grouped_packets, packet, segment_packet

ambient = types.SimpleNamespace(analyze_frame=analyze_frame, dim=dim, packet=packet,
                                segment_packet=segment_packet, grouped_packets=grouped_packets)


def _segment_frame(hex_color, mask):
    return segment_packet([int(hex_color[j:j+2], 16) for j in (1, 3, 5)], mask)


studio = types.SimpleNamespace(frame=lambda *data: packet(data), segment_frame=_segment_frame,
                               animation_colors=animation_colors)
adaptive = types.SimpleNamespace(adaptive_palette=adaptive_palette)


def run_pipeline(frames, ticks, **overrides):
    """Drive analysis and smoothing with the prototype's capture-loop timing."""
    options = {'interval': 1.0, 'transition': 3.0, 'brightness': 65, 'multicolor': True,
               'color_mode': 'Atmospheric', 'spatial_mapping': 'Serpentine',
               **baseline_builder.GUI_TUNING, **overrides}
    frames = list(frames)
    smoother = ColorSmoother(options['transition'])
    now = last_tick = 1000.0
    last_sample = -1e9
    target = None
    previews = []
    for _ in range(ticks):
        if now - last_sample >= options['interval']:
            frame = frames.pop(0) if len(frames) > 1 else frames[0]
            target = np.array(analyze_frame(
                frame, options['multicolor'], options['saturation_boost'],
                options['minimum_saturation'], options['minimum_value'],
                options['color_mode'], options['spatial_mapping']), dtype=np.float64)
            last_sample = now
        previews.append(dim(smoother.update(target, now - last_tick), options['brightness']))
        last_tick = now
        now += .15
    return previews


def smoothing_entries():
    return baseline_builder.smoothing_entries(run_pipeline)
