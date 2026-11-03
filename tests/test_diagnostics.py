"""Connection records stay local and redacted."""
import json
import logging

import pytest

from glowshift import diagnostics
from glowshift.devices.govee.connection import ConnectionEvent, Failure

ADDRESS = 'AA:BB:CC:DD:EE:FF'
USER_PATH = 'C:' + '\\Users\\someone\\AppData\\file.py'


@pytest.fixture
def log_file(tmp_path):
    handler = diagnostics.configure(tmp_path)
    yield tmp_path / 'glowshift.log'
    logging.getLogger().removeHandler(handler)
    handler.close()


def text(path):
    logging.getLogger().handlers[-1].flush()
    return path.read_text(encoding='utf-8')


def test_redact():
    assert diagnostics.redact(f'{ADDRESS}: at "{USER_PATH}"') == '<device>: at "<home>\\AppData\\file.py"'


def test_library_messages_are_redacted(log_file):
    logging.getLogger('bleak.backends.winrt.client').warning('%s: unhandled services changed event', ADDRESS)
    assert '<device>: unhandled' in text(log_file)
    assert ADDRESS not in text(log_file)


def test_tracebacks_are_redacted(log_file):
    try:
        raise RuntimeError(f'failed at {USER_PATH} for {ADDRESS}')
    except RuntimeError:
        logging.getLogger('glowshift.test').exception('boom')
    assert 'someone' not in text(log_file) and ADDRESS not in text(log_file)


def test_connection_event_is_one_json_record(log_file):
    event = ConnectionEvent('connection_closed', 5.0, {'lifetime': 12.34567, 'cause': Failure.DISCONNECTED,
                                                       'connection': 3, 'detail': f'at {ADDRESS}'})
    diagnostics.record_connection_event(event)
    record = json.loads(text(log_file).strip().split(' ', 4)[4])
    assert record['event'] == 'connection_closed'
    assert record['lifetime'] == 12.346
    assert record['cause'] == 'disconnected'
    assert record['detail'] == 'at <device>'


def test_per_batch_events_are_not_written(log_file):
    diagnostics.record_connection_event(ConnectionEvent('batch_accepted_by_windows', 1.0, {}))
    assert text(log_file) == ''


def test_default_directory_is_outside_the_repository(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert diagnostics.default_directory() == tmp_path / 'GlowShift' / 'logs'


def test_log_file_name_is_ignored_by_git():
    from pathlib import Path
    assert '*.log\n' in (Path(__file__).parent.parent / '.gitignore').read_text(encoding='utf-8')
