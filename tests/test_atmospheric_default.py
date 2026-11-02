"""Baseline for the planned switch of the GUI default to Atmospheric.

The prototypes stay unchanged, so these tests pin today's behavior. The switch
itself lands with the new GUI, and must only change the selected mode: not the
tuning, and not what Atmospheric produces.
"""
import baseline_builder
from scenes import SCENES


def test_legacy_gui_defaults_are_recorded(ambient, baseline):
    assert baseline_builder.gui_defaults(ambient) == baseline['gui_defaults']


def test_legacy_gui_starts_in_dominant_colors(baseline):
    assert baseline['gui_defaults']['color_mode'] == 'Dominant colors'


def test_gui_tuning_is_the_tuning_to_preserve(baseline):
    defaults = baseline['gui_defaults']
    assert defaults['saturation_boost'] == 1.5
    assert defaults['minimum_saturation'] == 0.60
    assert defaults['minimum_value'] == 0.75


def test_analyze_frame_function_default_mode_is_atmospheric(ambient):
    frame = SCENES['grass_sky']()
    tuning = baseline_builder.GUI_TUNING
    assert ambient.analyze_frame(frame, True, **tuning) == ambient.analyze_frame(
        frame, True, color_mode='Atmospheric', **tuning)


def test_baseline_can_detect_a_wrong_mode(baseline):
    """Guards the baseline: the two modes must differ on scenes where they should."""
    analysis = baseline['analysis']
    key = 'grass_sky|{}|Serpentine|multi=True|gui'
    assert analysis[key.format('Atmospheric')] != analysis[key.format('Dominant colors')]
