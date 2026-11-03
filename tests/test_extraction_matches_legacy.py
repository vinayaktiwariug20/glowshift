"""Compare the package directly with the local prototypes. Skipped without legacy/."""
import ast
import inspect
from pathlib import Path

import pytest

import legacy_loader
from glowshift.analysis import ambient as package_ambient
from glowshift.analysis.experimental import adaptive as package_adaptive
from glowshift.animations import designs, effects
from glowshift.devices.govee import protocol
from test_designs import COLORS


def function_ast(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return ast.dump(node)
    raise LookupError(name)


def legacy_source(name):
    return (legacy_loader.LEGACY_DIR / f'{name}.py').read_text(encoding='utf-8')


@pytest.mark.parametrize('legacy_name, module, names', [
    (legacy_loader.AMBIENT, package_ambient, ['analyze_frame', 'dim']),
    (legacy_loader.AMBIENT, protocol, ['packet', 'segment_packet', 'grouped_packets']),
    (legacy_loader.STUDIO, effects, ['to_hex', 'rgb', 'blend', 'animation_colors']),
    (legacy_loader.ADAPTIVE, package_adaptive,
     ['ColorCandidate', '_candidates', '_color_distance', 'adaptive_palette', 'save_preview', 'main']),
], ids=['analysis', 'protocol', 'animations', 'adaptive'])
def test_algorithms_are_structurally_identical(legacy_name, module, names):
    if not legacy_loader.available():
        pytest.skip('legacy/ is local-only and not present')
    package_source = Path(inspect.getfile(module)).read_text(encoding='utf-8')
    original = legacy_source(legacy_name)
    for name in names:
        assert function_ast(package_source, name) == function_ast(original, name), name


@pytest.fixture
def studio_window(studio, monkeypatch):
    root = legacy_loader.tk_window()
    window = studio.Studio(root)
    errors = []
    monkeypatch.setattr(studio.messagebox, 'showerror', lambda title, text: errors.append(text))
    window.errors = errors
    yield window
    window.worker.request('quit')
    window.worker.thread.join(timeout=5)
    root.destroy()


def test_prototype_and_package_write_identical_files(studio, studio_window, tmp_path, monkeypatch):
    legacy_path, package_path = tmp_path / 'legacy.json', tmp_path / 'package.json'
    studio_window.colors = list(COLORS)
    studio_window.brightness.set(42)
    monkeypatch.setattr(studio.filedialog, 'asksaveasfilename', lambda **kwargs: str(legacy_path))
    studio_window.save()
    designs.save_design(package_path, designs.Design(list(COLORS), 42))
    assert legacy_path.read_bytes() == package_path.read_bytes()


@pytest.mark.parametrize('text', [
    '{"colors": %s}', '{"colors": %s, "brightness": 500}', '{"colors": %s, "brightness": "70"}',
    '{"colors": ["#123"], "brightness": 5}', '{"brightness": 5}', '{not json',
], ids=['default', 'high', 'text', 'bad-colors', 'no-colors', 'malformed'])
def test_prototype_and_package_load_identically(studio, studio_window, tmp_path, monkeypatch, text):
    import json
    path = tmp_path / 'design.json'
    path.write_text(text % json.dumps(COLORS) if '%s' in text else text, encoding='utf-8')
    monkeypatch.setattr(studio.filedialog, 'askopenfilename', lambda **kwargs: str(path))
    original_colors = list(studio_window.colors)
    studio_window.brightness.set(33)
    studio_window.load()
    try:
        design = designs.load_design(path)
    except Exception as error:
        assert studio_window.errors == [str(error)]
        assert studio_window.colors == original_colors
    else:
        assert studio_window.errors == []
        assert (studio_window.colors, studio_window.brightness.get()) == (design.colors, design.brightness)
