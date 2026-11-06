# GlowShift

Open-source Windows application for screen-reactive RGB lighting, currently
targeting the Govee H617A Bluetooth LED strip (15 segments).

**Status:** pre-release. `src/glowshift` holds the protocol, color analysis, smoothing,
animations, design files, connection manager, session lifecycle and the GUI. The
connection manager has passed short supervised tests on a physical H617A.
`python tests/demo_simulated_session.py` demonstrates it without hardware.

## Running

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python -m glowshift
```

One window with two tabs:

- **Screen sync** is GlowShift Ambient v3.5.1, with the same controls and defaults,
  except that it starts in Atmospheric mode (tuning unchanged: saturation boost 1.5,
  minimum saturation 0.60, minimum intensity 0.75).
- **Studio** is Govee H617A Studio v5: paint segments, save and load designs, power,
  brightness and animations.

Only one of them uses the strip at a time. Starting screen sync with "Send colors to
physical strip" disconnects Studio first; Studio's strip actions wait while screen
sync is sending. Preview-only screen sync and Studio can run together.

Differences from the old apps: Studio's live painting sends the whole design rather
than one packet per click, and rapid clicks are combined; Studio animations keep
Studio v5's pace (up to about 4 updates a second, at least 0.1 s after each update),
while screen sync keeps its 1.2-second minimum. Brightness set in Studio is the strip's
own setting and stays in effect during screen sync, as it did with the old apps.

## Device address

The strip's Bluetooth address is stored only on your machine, in
`%LOCALAPPDATA%\GlowShift\settings.json`:

```powershell
.venv\Scripts\python -m glowshift.settings --set-address <address>
```

The `GLOWSHIFT_H617A_ADDRESS` environment variable overrides the file. The address is
never logged or printed.

## Screen capture

If the screen cannot be captured, for example on the lock screen, at a UAC prompt or
during a display change, capture retries after 1, 2, 4, 8 and then every 10 seconds
until it works or you stop it. Bluetooth stays connected and the strip keeps its last
colors meanwhile. Other capture errors still stop the run.

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
