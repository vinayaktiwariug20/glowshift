"""The package must reproduce the recorded baseline exactly. Needs no legacy/ directory."""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import baseline_builder
import package_adapter
from glowshift.analysis.ambient import analyze_frame
from glowshift.analysis.smoothing import ColorSmoother
from scenes import SCENES, SENSITIVITY_SCENES, scene_hash
from test_baseline import assert_matches


def test_analysis(baseline):
    assert_matches(baseline_builder.analysis(package_adapter.ambient), baseline['analysis'])


def test_sensitivity_scenes(sensitivity_baseline):
    assert {name: scene_hash(scene()) for name, scene in SENSITIVITY_SCENES.items()} \
        == sensitivity_baseline['scene_hashes']
    assert_matches(baseline_builder.sensitivity(package_adapter.ambient), sensitivity_baseline['analysis'])


def test_dimming(baseline):
    assert_matches(baseline_builder.dimming(package_adapter.ambient), baseline['dimming'])


def test_smoothing(baseline):
    assert_matches(package_adapter.smoothing_entries(), baseline['smoothing'])


def test_packets(baseline):
    assert_matches(baseline_builder.packets(package_adapter.ambient, package_adapter.studio),
                   baseline['packets'])


def test_animations(baseline):
    assert_matches(baseline_builder.animations(package_adapter.studio), baseline['animations'])


def test_experimental_adaptive(baseline):
    assert_matches(baseline_builder.adaptive(package_adapter.adaptive), baseline['adaptive'])


@pytest.mark.xfail(strict=True, reason='known defect carried over: empty slices produce NaN colors')
@pytest.mark.filterwarnings('ignore::RuntimeWarning')
def test_adaptive_handles_frames_narrower_than_its_slices():
    from PIL import Image
    image = Image.fromarray(SCENES['small_frame']()[..., [2, 1, 0]])
    palette = package_adapter.adaptive.adaptive_palette(image, 9)
    assert all(0 <= int(channel) <= 255 for color in palette for channel in color)


def test_function_default_mode_and_tuning_are_unchanged():
    frame = SCENES['grass_sky']()
    assert analyze_frame(frame) == analyze_frame(
        frame, True, 1.35, .48, .48, 'Atmospheric', 'Serpentine')


def test_unknown_mode_still_behaves_as_atmospheric():
    frame = SCENES['gradient']()
    assert analyze_frame(frame, color_mode='typo') == analyze_frame(frame, color_mode='Atmospheric')


def test_smoother_starts_at_first_target_and_clamps_elapsed_time():
    target = np.array([[100.0, 50.0, 0.0]])
    smoother = ColorSmoother(1.0)
    assert smoother.update(target, 0.0) == [(100, 50, 0)]
    one_second, ten_seconds = ColorSmoother(1.0), ColorSmoother(1.0)
    for smoother in (one_second, ten_seconds):
        smoother.update(np.zeros((1, 3)), 0.0)
    assert one_second.update(target, 1.0) == ten_seconds.update(target, 10.0)


def test_stable_analysis_does_not_load_the_experimental_algorithm():
    code = ('import sys, glowshift.analysis.ambient;'
            "assert 'glowshift.analysis.experimental.adaptive' not in sys.modules;"
            "assert 'PIL' not in sys.modules")
    run_fresh_interpreter(code)


def test_package_import_needs_no_hardware_libraries():
    code = ('import sys, glowshift.analysis.ambient, glowshift.analysis.smoothing,'
            'glowshift.animations.effects, glowshift.animations.designs,'
            'glowshift.devices.govee.protocol, glowshift.devices.govee.connection,'
            'glowshift.capture.loop, glowshift.engine.session, glowshift.diagnostics;'
            "assert not {'bleak', 'mss', 'tkinter'} & set(sys.modules)")
    run_fresh_interpreter(code)


def run_fresh_interpreter(code):
    source = Path(__file__).resolve().parent.parent / 'src'
    subprocess.run([sys.executable, '-c', code], check=True,
                   env={**os.environ, 'PYTHONPATH': str(source)})
