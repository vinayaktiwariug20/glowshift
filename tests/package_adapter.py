"""Present the package under the prototype's names so one baseline checks both."""
import types

import baseline_builder
import legacy_loader
from glowshift.analysis.ambient import analyze_frame, dim
from glowshift.analysis.experimental.adaptive import adaptive_palette
from glowshift.animations.effects import animation_colors
from glowshift.capture.loop import CaptureOptions, capture_loop
from glowshift.devices.govee.protocol import grouped_packets, packet, segment_packet

ambient = types.SimpleNamespace(analyze_frame=analyze_frame, dim=dim, packet=packet,
                                segment_packet=segment_packet, grouped_packets=grouped_packets)


def _segment_frame(hex_color, mask):
    return segment_packet([int(hex_color[j:j+2], 16) for j in (1, 3, 5)], mask)


studio = types.SimpleNamespace(frame=lambda *data: packet(data), segment_frame=_segment_frame,
                               animation_colors=animation_colors)
adaptive = types.SimpleNamespace(adaptive_palette=adaptive_palette)


def capture_options(**overrides):
    return CaptureOptions(**{'monitor': 0, 'interval': 1.0, 'transition': 3.0, 'brightness': 65,
                             'multicolor': True, 'color_mode': 'Atmospheric',
                             'spatial_mapping': 'Serpentine', **baseline_builder.GUI_TUNING, **overrides})


def run_pipeline(frames, ticks, **overrides):
    """Run the package's real capture loop on a fake clock."""
    clock = baseline_builder._FakeClock()
    previews = []
    capture_loop(capture_options(**overrides), baseline_builder._FakeStop(clock, ticks), previews.append,
                 screen_factory=legacy_loader.FakeScreen(frames), clock=clock.monotonic)
    return previews


def smoothing_entries():
    return baseline_builder.smoothing_entries(run_pipeline)
