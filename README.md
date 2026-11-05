# GlowShift

Open-source Windows application for screen-reactive RGB lighting, currently
targeting the Govee H617A Bluetooth LED strip (15 segments).

**Status:** pre-release. `src/glowshift` holds the protocol, color analysis, smoothing,
animations, design files, connection manager and session lifecycle. The connection
manager has only run against a simulated device so far, and the GUI is not in the
package yet. `python tests/demo_simulated_session.py` demonstrates it without hardware.

## Development setup

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install --only-binary :all: -r requirements.txt
.venv\Scripts\python -m pytest
```

## Tests

`tests/golden/baseline.json` records the exact output of the original prototypes
on synthetic scenes, and the package tests check against it without needing the
prototypes. The prototypes are kept locally in `legacy/` and are not published, so
tests that execute them directly are skipped when `legacy/` is absent. See
[tests/golden/README.md](tests/golden/README.md) for how the baseline is maintained.

Tests never open a Bluetooth connection or capture the screen.

Each test has a 30-second wall-clock limit. A test that hangs is named, every thread's
stack is printed and the run exits with code 3. Simulated Bluetooth scenarios also fail
if they pass 3600 virtual seconds.

## Privacy

Never commit device MAC addresses, logs, personal paths, Bluetooth scan output or
real screenshots. Test fixtures are generated synthetically.
`tests/test_privacy.py` checks every tracked file.

## Notes

- [Windows Bluetooth diagnostic notes](docs/windows-bluetooth-notes.md)
