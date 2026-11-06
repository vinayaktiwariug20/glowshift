# GlowShift

Screen-reactive RGB lighting for Windows. GlowShift reads the colours on your screen
and paints them, segment by segment, onto a **Govee H617A** Bluetooth LED strip
(15 segments). It also has a small studio for painting the strip by hand and running
animations.

**Status:** alpha (0.1.0). Tested on Windows 10 with Python 3.14 and one H617A.
Other Govee models are not supported.

> GlowShift is an independent project. It is not affiliated with, endorsed by or
> supported by Govee. "Govee" is a trademark of its owner and is used here only to
> name the hardware this works with.

## Requirements

- Windows 10 or 11 with Bluetooth Low Energy
- Python 3.14
- A Govee H617A strip, switched on and **not** connected to the Govee Home app at the
  same time (the strip accepts one Bluetooth connection)

## Install and run

```powershell
git clone <repository-url> glowshift
cd glowshift
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install --only-binary :all: -e .
.venv\Scripts\glowshift
```

`.venv\Scripts\python -m glowshift` works too.

### Strip address

GlowShift connects to your strip by its Bluetooth address, which looks like
`AA:BB:CC:DD:EE:FF`. The Govee Home app may show it among the device's details, and any
Bluetooth LE scanner app lists it next to the strip's name. Enter it in the **Strip
address** field at the top of the window and press **Save address**, or run:

```powershell
.venv\Scripts\python -m glowshift.settings --set-address <address>
```

The address is stored only on your machine, in
`%LOCALAPPDATA%\GlowShift\settings.json`. The `GLOWSHIFT_H617A_ADDRESS` environment
variable overrides the file. The app never shows, prints or logs the address.

## Using it

One window with two tabs. Only one of them uses the strip at a time.

**Screen sync** captures a monitor and sends its colours to the strip. It starts in
**Atmospheric** mode, which favours the overall mood of the scene. The other modes are
**Dominant colors** and **Spatial (5x3)**, which maps screen regions to segments. Leave "Send colors to physical strip" off
to preview without Bluetooth.

**Studio** lets you paint each of the 15 segments, save and load designs, set power and
brightness, and run animations (about 4 updates a second).

Starting screen sync with the strip enabled disconnects Studio first. Studio's strip
controls are refused while screen sync is sending. Preview-only screen sync and Studio
can run together. Brightness set in Studio is the strip's own setting and stays in
effect during screen sync.

### What to expect from Bluetooth

- Screen sync updates the strip at most every 1.2 seconds. That limit keeps the H617A
  responsive over Windows Bluetooth; it is not meant for fast flicker effects.
- If the strip goes out of range or is switched off, GlowShift reconnects on its own.
  Unchanged colours are resent after 5 seconds without updates.
- The status line says when Windows **accepted** a write. The H617A does not confirm
  delivery, so that is not proof the strip changed colour.
- If the screen cannot be captured (lock screen, UAC prompt, display change), capture
  retries after 1, 2, 4, 8 and then every 10 seconds. The strip keeps its last colours
  meanwhile.

See [Windows Bluetooth notes](docs/windows-bluetooth-notes.md) for known Windows
Bluetooth behaviour and troubleshooting.

## Diagnostics

GlowShift writes a diagnostic log to `%LOCALAPPDATA%\GlowShift\logs\glowshift.log`
(rotated, at most about 12 MB). Device addresses and your user folder are redacted
before anything is written. Logs never leave your machine. Please read a log
before attaching it to an issue.

## Experimental: adaptive palette

`glowshift.analysis.experimental.adaptive` is an offline prototype that turns a single
image into 15 ordered LED colours. The app does not use it. It needs Pillow
(`pip install -e .[experimental]`):

```powershell
.venv\Scripts\python -m glowshift.analysis.experimental.adaptive screenshot.png --output palette.png
```

Known defect: images narrower than the number of slices produce invalid (NaN) colours.

## Development

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install --only-binary :all: -r requirements.txt
.venv\Scripts\python -m pytest
```

`requirements.txt` pins the exact versions the project is tested with; `pyproject.toml`
holds the supported ranges.

The tests never open a Bluetooth connection or capture the screen. Bluetooth is
simulated (`tests/simulated_device.py`), and the screen is replaced with synthetic
scenes. Each test has a 30-second wall-clock limit; a test that hangs is named, every
thread's stack is printed and the run exits with code 3.

`tests/golden/` records the exact output of the original prototypes on synthetic scenes,
so behaviour changes are caught without the prototypes. Tests that run the prototypes
directly are skipped when they are absent. See
[tests/golden/README.md](tests/golden/README.md).

`tools/h617a_hardware_check.py` is a supervised, stage-by-stage check against a real
strip. It sends nothing until you start each stage.

### Privacy

Never commit device addresses, logs, personal paths, Bluetooth scan output or real
screenshots. Test fixtures are synthetic. `tests/test_privacy.py` checks every tracked
file; the only address allowed is the placeholder `AA:BB:CC:DD:EE:FF`.

## License

[MIT](LICENSE)
