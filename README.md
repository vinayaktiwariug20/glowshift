# GlowShift

Open-source Windows application for screen-reactive RGB lighting, currently
targeting the Govee H617A Bluetooth LED strip (15 segments).

**Status:** pre-release. The package has not been extracted yet; this repository
currently holds the behavior baseline it will be built against.

## Development setup

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install --only-binary :all: -r requirements.txt
.venv\Scripts\python -m pytest
```

## Tests

`tests/golden/baseline.json` records the exact output of the original prototypes
on synthetic scenes. The prototypes themselves are kept locally in `legacy/` and
are not published, so tests that execute them are skipped when `legacy/` is absent.

Tests never open a Bluetooth connection or capture the screen.

## Privacy

Never commit device MAC addresses, logs, personal paths, Bluetooth scan output or
real screenshots. Test fixtures are generated synthetically.
`tests/test_privacy.py` checks every tracked file.

## Notes

- [Windows Bluetooth diagnostic notes](docs/windows-bluetooth-notes.md)
