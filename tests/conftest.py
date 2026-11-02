import json
from pathlib import Path

import pytest

import legacy_loader


@pytest.fixture(scope='session')
def baseline():
    return json.loads((Path(__file__).parent / 'golden' / 'baseline.json').read_text(encoding='utf-8'))


def _load(name):
    if not legacy_loader.available():
        pytest.skip('legacy/ is local-only and not present')
    return legacy_loader.load(name)


@pytest.fixture(scope='session')
def ambient():
    return _load(legacy_loader.AMBIENT)


@pytest.fixture(scope='session')
def studio():
    return _load(legacy_loader.STUDIO)


@pytest.fixture(scope='session')
def adaptive():
    return _load(legacy_loader.ADAPTIVE)
