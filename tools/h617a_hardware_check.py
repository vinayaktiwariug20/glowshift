"""Supervised, stage-by-stage check of the connection manager against a real H617A.

Runs nothing on its own. Each stage starts only when its name is written to
``<control>/next``, so a person can watch the strip between stages:

    connect     discover and connect; nothing is written
    solid       power on, then one solid color
    pattern     two-color segment pattern
    sequence    six solid colors, one every two seconds
    hold        keep the colors unchanged for 30 s and count the idle resends
    disconnect  stop the manager and disconnect cleanly
    reconnect   change the desired color while disconnected, then reconnect once
    quit        disconnect if connected and exit

Bluetooth is only active during a stage. If the connection is lost while waiting
for the next stage, the manager is stopped ("parked") rather than left retrying,
and the next stage reconnects. The whole run ends after --max-seconds (default 300)
whatever happens, and the process always exits.

The device address comes from the GLOWSHIFT_H617A_ADDRESS environment variable and
is never printed. Events are redacted and logged to the local diagnostics folder.
Uses only the package's packets and timing; no other commands are sent.
"""
import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from glowshift import diagnostics
from glowshift.devices.govee.connection import BluetoothWorker, ConnectionManager, ConnectionState
from glowshift.devices.govee.h617a import link_factory

SEGMENTS = 15
RED, GREEN, BLUE, WHITE = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)
SEQUENCE = [(255, 0, 0), (255, 120, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (160, 0, 255)]
SEQUENCE_NAMES = ['red', 'orange', 'yellow', 'green', 'cyan', 'purple']
CONNECT_WAIT = 45.0
BATCH_WAIT = 10.0
STOP_WAIT = 8.0
HOLD_SECONDS = 30.0
STAGES = ('connect', 'solid', 'pattern', 'sequence', 'hold', 'disconnect', 'reconnect', 'quit')


def say(text):
    print(diagnostics.redact(text), flush=True)


def leave(code):
    """Exit even if a stuck Bluetooth call holds a thread open."""
    logging.shutdown()
    sys.stdout.flush()
    os._exit(code)


class Check:
    def __init__(self, link_factory_for_address):
        self._link_factory = link_factory_for_address
        self.events = []
        self.condition = threading.Condition()
        self.worker = None
        self.desired_power = None
        self.desired_colors = None
        self.started = time.monotonic()
        self.idle_from = 0  # Events after this index arrived while waiting between stages.

    # Events arrive on the Bluetooth thread.
    def on_event(self, event):
        diagnostics.record_connection_event(event)
        fields = {key: diagnostics._plain(value) for key, value in event.fields.items()}
        say(f'  [{time.monotonic() - self.started:7.2f}s] {event.kind} {json.dumps(fields)}')
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def mark(self):
        with self.condition:
            return len(self.events)

    def since(self, index, kind):
        with self.condition:
            return [event for event in self.events[index:] if event.kind == kind]

    def wait_for_kind(self, index, kinds, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                found = next((event for event in self.events[index:] if event.kind in kinds), None)
                remaining = deadline - time.monotonic()
                if found is not None or remaining <= 0:
                    return found
                self.condition.wait(remaining)

    @property
    def running(self):
        return self.worker is not None and self.worker.thread.is_alive()

    @property
    def connected(self):
        return self.running and self.worker.manager.state is ConnectionState.CONNECTED

    def park(self, reason):
        """Stop the manager so nothing retries unattended."""
        if not self.running:
            return True
        index = self.mark()
        began = time.monotonic()
        clean = self.worker.stop(STOP_WAIT)
        closed = self.since(index, 'connection_closed')
        lifetime = f'{closed[0].fields["lifetime"]:.2f}s' if closed else 'no open connection'
        say(f'PARKED ({reason}): stopped cleanly={clean} in {time.monotonic() - began:.2f}s; '
            f'closed connection lifetime: {lifetime}')
        if not clean:
            say('FATAL: Bluetooth worker did not stop; exiting the process.')
            leave(2)
        return clean

    def ensure_connected(self):
        if self.connected:
            return True
        index = self.mark()
        if not self.running:
            manager = ConnectionManager(self._link_factory, self.on_event)
            if self.desired_power is not None:
                manager.set_power(self.desired_power)
            if self.desired_colors is not None:
                manager.publish(self.desired_colors)
            self.worker = BluetoothWorker(manager, lambda error: say(f'  worker exited, crashed={error is not None}'))
            self.worker.start()
        opened = self.wait_for_kind(index, {'connection_opened'}, CONNECT_WAIT)
        if opened is None:
            say(f'RESULT: not connected within {CONNECT_WAIT:.0f}s')
            self.park('no connection')
            return False
        return True

    def set(self, power=None, colors=None):
        if power is not None:
            self.desired_power = power
        if colors is not None:
            self.desired_colors = colors
        if self.running:
            if power is not None:
                self.worker.manager.set_power(power)
            if colors is not None:
                self.worker.manager.publish(colors)

    def accepted_after(self, index):
        """The batch result, ignoring connect failures the manager already retried past."""
        deadline = time.monotonic() + BATCH_WAIT
        with self.condition:
            while True:
                for event in self.events[index:]:
                    if event.kind == 'batch_accepted_by_windows' or (
                            event.kind == 'failure' and event.fields['phase'] == 'write'):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)

    def report_batch(self, event, expected):
        if event is None:
            say(f'RESULT: expected {expected}; nothing accepted within {BATCH_WAIT:.0f}s')
        elif event.kind == 'failure':
            say(f'RESULT: expected {expected}; failed: {event.fields["failure"].value}')
        else:
            say(f'RESULT: expected {expected}; accepted by Windows ({event.fields["packets"]} packets), '
                f'device delivery not confirmed')

    # Stages
    def connect(self):
        if self.ensure_connected():
            say(f'RESULT: connected; state={self.worker.manager.state.value}; nothing written')

    def solid(self):
        index = self.mark()
        self.set(power=True, colors=[BLUE] * SEGMENTS)
        if self.ensure_connected():
            self.report_batch(self.accepted_after(index), 'power on, all 15 segments blue')

    def pattern(self):
        index = self.mark()
        self.set(colors=[RED] * 8 + [GREEN] * 7)
        if self.ensure_connected():
            self.report_batch(self.accepted_after(index), 'segments 1-8 red, 9-15 green')

    def sequence(self):
        if not self.ensure_connected():
            return
        index = self.mark()
        for color, name in zip(SEQUENCE, SEQUENCE_NAMES):
            say(f'  publishing {name}')
            self.set(colors=[color] * SEGMENTS)
            time.sleep(2.0)
        time.sleep(.5)
        accepted = len(self.since(index, 'batch_accepted_by_windows'))
        lost = len(self.since(index, 'failure'))
        say(f'RESULT: expected {" -> ".join(SEQUENCE_NAMES)}; batches accepted by Windows={accepted} of 6; '
            f'connection failures during the stage={lost}')

    def hold(self):
        """Leave the colors unchanged for HOLD_SECONDS; the manager resends them when idle."""
        if not self.ensure_connected():
            return
        index = self.mark()
        began = time.monotonic()
        while time.monotonic() - began < HOLD_SECONDS and self.connected and not self.since(index, 'failure'):
            time.sleep(.2)
        held = time.monotonic() - began
        resends = len(self.since(index, 'refresh_accepted_by_windows'))
        failures = [event.fields['failure'].value for event in self.since(index, 'failure')]
        say(f'RESULT: colors unchanged for {held:.1f}s of {HOLD_SECONDS:.0f}s; resends accepted by Windows={resends}; '
            f'connection failures={failures or "none"}; still connected={self.connected}')

    def disconnect(self):
        if not self.running:
            say('RESULT: already disconnected')
            return
        self.park('disconnect stage')

    def reconnect(self):
        if self.running:
            self.park('before reconnect')
        index = self.mark()
        self.set(colors=[WHITE] * SEGMENTS)
        say('  desired state changed while disconnected: power on, all white')
        if self.ensure_connected():
            self.report_batch(self.accepted_after(index), 'the strip to turn white from the resent state')

    def quit(self):
        self.park('quit')

    def watch_idle(self):
        """Between stages: if the connection fails, stop instead of retrying."""
        if self.running and self.since(self.idle_from, 'failure'):
            self.park('connection lost while waiting for the next stage')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--control', required=True, type=Path, help='folder watched for a file named "next"')
    parser.add_argument('--max-seconds', type=float, default=300.0, help='hard limit for the whole run')
    args = parser.parse_args()
    address = os.environ.get('GLOWSHIFT_H617A_ADDRESS')
    if not address:
        sys.exit('Set GLOWSHIFT_H617A_ADDRESS first.')
    handler = diagnostics.configure()
    say(f'Diagnostics log: {handler.baseFilename}')
    check = Check(link_factory(address))
    deadline = time.monotonic() + args.max_seconds
    command = args.control / 'next'
    say(f'READY: waiting for a stage name; hard limit {args.max_seconds:.0f}s')
    while True:
        if time.monotonic() > deadline:
            say(f'TIME LIMIT: {args.max_seconds:.0f}s reached')
            check.park('time limit')
            leave(0)
        check.watch_idle()
        if not command.exists():
            time.sleep(.2)
            continue
        name = command.read_text(encoding='utf-8').strip()
        command.unlink()
        if name not in STAGES:
            say(f'Unknown stage: {name}')
            continue
        say(f'STAGE {name}')
        try:
            getattr(check, name)()
        except Exception as error:
            say(f'RESULT: stage raised {type(error).__name__}: {error}')
            check.park('stage error')
        check.idle_from = check.mark()
        say(f'DONE {name}')
        if name == 'quit':
            leave(0)


if __name__ == '__main__':
    main()
