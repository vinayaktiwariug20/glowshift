"""Nothing personal may enter Git. Checks everything tracked or staged."""
import getpass
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FICTIONAL_ADDRESS = 'AA:BB:CC:DD:EE:FF'  # The only address tests may use.
MAC_ADDRESS = re.compile(r'\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b')
USER_PATH = re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+', re.IGNORECASE)
FORBIDDEN_NAME = re.compile(r'(^|/)legacy/|\.log(\.\d+)?$|\.(png|jpe?g|bmp)$', re.IGNORECASE)


def tracked_files():
    try:
        output = subprocess.run(['git', 'ls-files', '--cached'], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip('not a Git checkout')
    return output.split('\n')[:-1]


def test_repository_has_tracked_files():
    assert tracked_files()


def test_no_forbidden_files_are_tracked():
    assert [name for name in tracked_files() if FORBIDDEN_NAME.search(name)] == []


@pytest.mark.parametrize('pattern', [MAC_ADDRESS, USER_PATH], ids=['mac-address', 'user-path'])
def test_no_identifiers_in_tracked_files(pattern):
    hits = [name for name in tracked_files()
            if pattern.search((ROOT / name).read_text(encoding='utf-8').replace(FICTIONAL_ADDRESS, ''))]
    assert hits == []


def test_no_local_username_in_tracked_files():
    username = re.compile(rf'\b{re.escape(getpass.getuser())}\b', re.IGNORECASE)
    hits = [name for name in tracked_files()
            if username.search((ROOT / name).read_text(encoding='utf-8'))]
    assert hits == []
