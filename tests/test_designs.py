"""Studio design file format. Needs no legacy/ directory."""
import json
import os

import pytest

from glowshift.animations.designs import Design, load_design, save_design

COLORS = [f'#{i:02x}80{255 - i:02x}' for i in range(0, 150, 10)]
EXPECTED_TEXT = ('{\n  "model": "H617A",\n  "colors": [\n'
                 + ',\n'.join(f'    "{color}"' for color in COLORS)
                 + '\n  ],\n  "brightness": 42\n}')


def write(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding='utf-8')
    return path


def test_saved_file_format_is_exact(tmp_path):
    path = tmp_path / 'design.json'
    save_design(path, Design(list(COLORS), 42))
    assert path.read_bytes() == EXPECTED_TEXT.replace('\n', os.linesep).encode('utf-8')


def test_round_trip(tmp_path):
    path = tmp_path / 'design.json'
    save_design(path, Design(list(COLORS), 42))
    assert load_design(path) == Design(COLORS, 42)


def test_uppercase_colors_load_with_their_case_preserved(tmp_path):
    colors = ['#ABCDEF'] * 15
    assert load_design(write(tmp_path / 'd.json', {'colors': colors})).colors == colors


def test_missing_brightness_defaults_to_50(tmp_path):
    assert load_design(write(tmp_path / 'd.json', {'colors': COLORS})).brightness == 50


@pytest.mark.parametrize('stored, loaded', [(0, 1), (-9, 1), (1, 1), (100, 100), (500, 100), ('70', 70), (70.9, 70)])
def test_brightness_is_clamped_and_truncated(tmp_path, stored, loaded):
    design = load_design(write(tmp_path / 'd.json', {'colors': COLORS, 'brightness': stored}))
    assert design.brightness == loaded


def test_model_field_is_not_validated(tmp_path):
    assert load_design(write(tmp_path / 'd.json', {'model': 'other', 'colors': COLORS})).colors == COLORS


@pytest.mark.parametrize('colors', [
    COLORS[:14], COLORS + ['#000000'], ['#12345'] * 15, ['#1234567'] * 15, ['1234567'] * 15,
    ['#12345g'] * 15, [123456] * 15, 'x' * 15,
], ids=['too-few', 'too-many', 'short', 'long', 'no-hash', 'not-hex', 'not-string', 'plain-string'])
def test_invalid_colors_are_rejected(tmp_path, colors):
    with pytest.raises(ValueError, match='Invalid segment colors'):
        load_design(write(tmp_path / 'd.json', {'colors': colors}))


def test_missing_colors_raises(tmp_path):
    with pytest.raises(KeyError):
        load_design(write(tmp_path / 'd.json', {'brightness': 10}))


def test_malformed_json_raises(tmp_path):
    with pytest.raises(json.JSONDecodeError):
        load_design(write(tmp_path / 'd.json', '{not json'))


@pytest.mark.parametrize('brightness, error', [('bright', ValueError), (None, TypeError)])
def test_non_numeric_brightness_raises(tmp_path, brightness, error):
    with pytest.raises(error):
        load_design(write(tmp_path / 'd.json', {'colors': COLORS, 'brightness': brightness}))
