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
| Idle connection ends after about 7 s | First supervised test (Nov 2026): 361 of 361 connections with nothing written dropped after 1.7–17.7 s, median 7.0 s. |
| Connection ends about 10 s after the last write | Second supervised test: three connections dropped 10.1, 10.15 and 10.13 s after their last accepted write. One stayed up for 20 s while writes arrived every 2 s. Cause unknown; resending avoided it in one test. See "Resending unchanged colors". |
| `unhandled services changed event` (bleak warning) | Logged on most connections in the same test. Meaning unknown. |
| `[WinError -2147024809] The parameter is incorrect.` | Once, during connect. |
| Connect call ignores cancellation | Once, a connect hung for about 37 h despite a 24-second deadline, because `asyncio.wait_for` waits for the cancelled call. The manager now abandons such calls; see below. |

## Calls that ignore cancellation

Every link call (scan, connect, write, disconnect) has a hard deadline. A call that
misses it is cancelled and abandoned rather than awaited, logged as `call_abandoned`,
and logged again as `abandoned_call_finished` if it ever ends. A connect that
succeeds after being abandoned is disconnected at once, so it cannot hold the strip.
On Stop, the Bluetooth thread waits at most one second for such calls before
closing its event loop.

## Resending unchanged colors

Because the link dropped about 10 s after the last write, the connection manager
resends the current segment colors when nothing has been written for 5 seconds
(`Timing.refresh_after_idle`). A resend uses the same segment packets and 65 ms
gaps as any batch; power, brightness and every other command are never resent this
way. Resends are counted separately (`refreshes` on each closed connection) and are
not logged one by one. Nothing is resent before the first colors are known.

In one supervised test (one solid color, so one packet per resend), the link stayed up
for 31 s with six resends about 5.1 s apart, and the color stayed steady. That is a
single run; multi-packet patterns were not held.

## "Event loop is closed" after shutdown

In one test bleak logged many "Event loop is closed" errors from its services-changed
handler after the Bluetooth thread had shut down. In bleak 3.0, the WinRT backend's
`disconnect()` closes the device without unregistering that handler (only a disconnect
reported by Windows removes it), so Windows can still call it later. GlowShift now
removes the handler itself just before disconnecting. This reaches into bleak
internals: it does nothing if they differ, and a test fails if an installed bleak no
longer matches. In two supervised runs with this change (three connections, one held
for 145 s), neither error nor the `unhandled services changed event` warning appeared.
The day before, without it, the warning appeared once in six connections and the
loop-closed errors not at all, so this is too little evidence to call it fixed.

## Stop during a disconnect

If Stop arrives while the manager is already disconnecting a dropped link, the
disconnect is allowed to finish (it is bounded at 5 seconds) instead of being cancelled
and logged as `disconnect_failed`.

## Disconnect callbacks

On hardware the callback often arrives after polling has already seen the link drop,
and sometimes twice. A callback is marked `expected` only if GlowShift disconnected a
link it still saw as connected; repeats are marked `duplicate`.

## Connection records

The package's connection manager records connection lifetimes, disconnect callbacks,
failed writes, service-discovery failures and reconnection attempts as JSON lines in
`%LOCALAPPDATA%\GlowShift\logs\glowshift.log`. Device addresses and home paths are
redacted, and the file stays on this machine.

Connection uptime and accepted writes are recorded separately: each closed connection
reports its lifetime, the packets Windows accepted, when the first write was accepted
and how long before closing the last one was. A connected strip is not necessarily
receiving updates.

The retry delay resets only after a connection has lasted 30 seconds. This rule is
provisional and counts uptime alone, which `reset_by_uptime` marks in the log. Radio-off
events have their own state and fixed 60-second retry, and leave the retry delay
untouched.

A batch that stops partway, through a failed write, a timeout or Stop, is logged as
`batch_incomplete` with how many of its packets Windows accepted. It is not resent.
Callers set a desired power, brightness and color state, and each new connection
sends the whole state as it is then, so a reconnect never replays superseded commands.

`tools/h617a_hardware_check.py` runs a supervised, stage-by-stage check against a real
strip. It reads the address from local settings or `GLOWSHIFT_H617A_ADDRESS` and never
prints it.
Bluetooth is active only during a stage: a connection lost between stages stops the
manager instead of leaving it retrying. The whole run ends after `--max-seconds`
(default 300), and the process always exits.

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
