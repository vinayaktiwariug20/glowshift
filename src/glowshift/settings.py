"""Local settings, kept on this machine and outside the repository.

The strip's Bluetooth address lives in ``%LOCALAPPDATA%\\GlowShift\\settings.json``.
The ``GLOWSHIFT_H617A_ADDRESS`` environment variable overrides it. The address is
never logged.
"""
import json
import os
import re
from pathlib import Path

ADDRESS_VARIABLE = 'GLOWSHIFT_H617A_ADDRESS'
_ADDRESS = re.compile(r'^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$')


class InvalidAddress(ValueError):
    """Not a Bluetooth address of the form AA:BB:CC:DD:EE:FF. The value is not repeated."""


def default_path():
    base = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(base) / 'GlowShift' / 'settings.json'


def normalize_address(address):
    value = address.strip().upper().replace('-', ':')
    if not _ADDRESS.match(value):
        raise InvalidAddress('Expected a Bluetooth address: six pairs of hex digits separated by colons')
    return value


def load(path=None):
    path = Path(path or default_path())
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def device_address(path=None, environ=None):
    """The configured address, or None. The environment variable wins over the file."""
    environ = os.environ if environ is None else environ
    value = environ.get(ADDRESS_VARIABLE) or load(path).get('device_address')
    return normalize_address(value) if value else None


def save_device_address(address, path=None):
    path = Path(path or default_path())
    data = load(path)
    data['device_address'] = normalize_address(address)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def main(arguments=None):
    import argparse
    parser = argparse.ArgumentParser(prog='python -m glowshift.settings',
                                     description='Save the H617A Bluetooth address to local settings.')
    parser.add_argument('--set-address', required=True, metavar='ADDRESS')
    options = parser.parse_args(arguments)
    try:
        save_device_address(options.set_address)
    except InvalidAddress as error:
        parser.exit(2, f'{error}\n')
    print(f'Saved to {default_path()}')


if __name__ == '__main__':
    main()
