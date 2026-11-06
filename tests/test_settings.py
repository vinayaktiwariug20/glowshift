"""The device address is read from local settings, never from the repository."""
import json

import pytest

from glowshift import settings

ADDRESS = 'AA:BB:CC:DD:EE:FF'  # Fictional. Allowed by the privacy test.


def test_no_address_configured(tmp_path):
    assert settings.device_address(tmp_path / 'settings.json', environ={}) is None


def test_saved_address_is_read_back_normalized(tmp_path):
    path = tmp_path / 'GlowShift' / 'settings.json'
    settings.save_device_address(f" {ADDRESS.lower().replace(':', '-')} ", path)
    assert json.loads(path.read_text(encoding='utf-8')) == {'device_address': ADDRESS}
    assert settings.device_address(path, environ={}) == ADDRESS


def test_saving_keeps_other_settings(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{"other": 1}', encoding='utf-8')
    settings.save_device_address(ADDRESS, path)
    assert json.loads(path.read_text(encoding='utf-8')) == {'other': 1, 'device_address': ADDRESS}
    assert not path.with_suffix('.tmp').exists()


def test_environment_variable_overrides_the_file(tmp_path):
    path = tmp_path / 'settings.json'
    settings.save_device_address(ADDRESS, path)
    other = ADDRESS.replace('FF', '00')  # Built here: the privacy test allows one literal address.
    assert settings.device_address(path, environ={settings.ADDRESS_VARIABLE: other}) == other


@pytest.mark.parametrize('value', ['', 'AA:BB:CC:DD:EE', 'AA:BB:CC:DD:EE:GG', 'AABBCCDDEEFF', 'AA:BB:CC:DD:EE:FF:00'])
def test_invalid_addresses_are_rejected_without_repeating_them(tmp_path, value):
    with pytest.raises(settings.InvalidAddress) as caught:
        settings.save_device_address(value or ' ', tmp_path / 'settings.json')
    assert value not in str(caught.value) or value == ''


def test_invalid_address_in_the_file_is_rejected(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{"device_address": "nonsense"}', encoding='utf-8')
    with pytest.raises(settings.InvalidAddress):
        settings.device_address(path, environ={})


def test_command_line_saves_without_printing_the_address(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    settings.main(['--set-address', ADDRESS])
    assert ADDRESS not in capsys.readouterr().out
    assert settings.device_address(environ={}) == ADDRESS


def test_default_path_is_outside_the_repository(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert settings.default_path() == tmp_path / 'GlowShift' / 'settings.json'
