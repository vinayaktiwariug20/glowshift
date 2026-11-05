"""Local connection diagnostics. Files stay on this machine, under the user's AppData."""
import enum
import json
import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = 'glowshift.diagnostics'
_MAC_ADDRESS = re.compile(r'\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b')
_USER_PATH = re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+[^\\/"\s]+', re.IGNORECASE)
_NOT_RECORDED = {'batch_accepted_by_windows', 'refresh_accepted_by_windows'}  # Summarized at close.


def redact(text):
    return _USER_PATH.sub('<home>', _MAC_ADDRESS.sub('<device>', text))


class RedactingFormatter(logging.Formatter):
    """Redacts the whole formatted record, including tracebacks and library messages."""

    def format(self, record):
        return redact(super().format(record))


def default_directory():
    base = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(base) / 'GlowShift' / 'logs'


def configure(directory=None, level=logging.INFO):
    """Log to a rotating local file, covering GlowShift and bleak. Returns the handler."""
    directory = Path(directory) if directory else default_directory()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / 'glowshift.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    handler.setFormatter(RedactingFormatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    return handler


def _plain(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, float):
        return round(value, 3)
    return value


def record_connection_event(event):
    """Write one line per connection event. Pass as ``on_connection_event``."""
    if event.kind in _NOT_RECORDED:
        return
    fields = {key: _plain(value) for key, value in event.fields.items()}
    logging.getLogger(LOGGER_NAME).info(json.dumps({'event': event.kind, 'at': time.time(), **fields}))
