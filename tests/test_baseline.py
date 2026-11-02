"""The prototypes must reproduce the recorded baseline exactly."""
import pytest

import baseline_builder
from scenes import SCENES, scene_hash


def assert_matches(actual, expected):
    assert actual.keys() == expected.keys()
    different = [key for key in expected if actual[key] != expected[key]]
    detail = '\n'.join(f'{key}\n  expected {expected[key]}\n  actual   {actual[key]}'
                       for key in different[:5])
    assert not different, f'{len(different)} of {len(expected)} differ:\n{detail}'


def test_synthetic_scenes_are_unchanged(baseline):
    assert {name: scene_hash(scene()) for name, scene in SCENES.items()} == baseline['scene_hashes']


def test_analysis_matches_baseline(ambient, baseline):
    assert_matches(baseline_builder.analysis(ambient), baseline['analysis'])


def test_dimming_matches_baseline(ambient, baseline):
    assert_matches(baseline_builder.dimming(ambient), baseline['dimming'])


def test_smoothing_matches_baseline(ambient, baseline):
    assert_matches(baseline_builder.smoothing(ambient), baseline['smoothing'])


def test_packets_match_baseline(ambient, studio, baseline):
    assert_matches(baseline_builder.packets(ambient, studio), baseline['packets'])


def test_animations_match_baseline(studio, baseline):
    assert_matches(baseline_builder.animations(studio), baseline['animations'])


def test_adaptive_prototype_matches_baseline(adaptive, baseline):
    assert_matches(baseline_builder.adaptive(adaptive), baseline['adaptive'])


@pytest.mark.xfail(strict=True, reason='known prototype defect: empty slices produce NaN colors')
@pytest.mark.filterwarnings('ignore::RuntimeWarning')
def test_adaptive_handles_frames_narrower_than_its_slices(adaptive):
    from PIL import Image
    image = Image.fromarray(SCENES['small_frame']()[..., [2, 1, 0]])
    palette = adaptive.adaptive_palette(image, 9)
    assert all(0 <= int(channel) <= 255 for color in palette for channel in color)
