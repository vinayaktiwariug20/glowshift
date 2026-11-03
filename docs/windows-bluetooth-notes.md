# Windows Bluetooth diagnostic notes

These are observations from testing against a physical H617A. None of them is a
confirmed root cause. Keep logs and device identifiers local; do not paste them
into public issues.

## Observed failure types

Treat these as separate categories when reading logs.

| Symptom | Notes |
|---|---|
| `Device disconnected` | The strip connects, Windows accepts writes, then the link drops. Cause unknown. |
| `OSError: [WinError -2147467260] Operation aborted` | Raised from `write_gatt_char`, so the link failed during a write. |
| `BleakError: Could not write value ... to characteristic 0013: Unreachable` | Seen in Studio and Ambient during physical-device testing. Absent from the retained logs. |
| `BleakBluetoothNotAvailableError` (radio not powered on) | Keep separate from disconnections. It is not known whether the radio was intentionally off during long logged runs of this error. |
| Write characteristic missing | Possibly a stale Windows service cache. Unconfirmed. |

## Connection records

The package's connection manager records connection lifetimes, disconnect callbacks,
failed writes, service-discovery failures and reconnection attempts as JSON lines in
`%LOCALAPPDATA%\GlowShift\logs\glowshift.log`. Device addresses and home paths are
redacted, and the file stays on this machine.

The retry delay resets only after a connection has lasted 30 seconds, because an
accepted write says nothing about whether the link is healthy. Radio-off events have
their own state and fixed 60-second retry, and leave the retry delay untouched.

## Reading write results

The strip is written with write-without-response. A successful write call means
Windows accepted the data. It does not show that the strip received or applied it.

## Observation: BluetoothUserService stuck in STOP_PENDING

While Windows BLE scanning and pairing were hanging, the per-user service
`BluetoothUserService_<suffix>` was found stuck in `STOP_PENDING`. Terminating its
isolated service-host process restored Bluetooth functionality without a reboot.

This is a single diagnostic observation, not a confirmed root cause, and the
connection between it and the failures above is unknown.

GlowShift must not automate process termination.
