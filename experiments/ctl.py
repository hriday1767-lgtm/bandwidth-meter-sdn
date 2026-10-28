#!/usr/bin/env python3
"""
Tiny control client for the SDN controller's AF_UNIX channel.

Switches the controller between the proposed dynamic behaviour and the
static (no-reroute) baseline WITHOUT restarting it. Each switch also forces
the h1/h3 -> h2 flow back onto the primary path, so consecutive experiment
runs start from an identical state.

Usage (from the project root, while the controller is running):
    python3 experiments/ctl.py static
    python3 experiments/ctl.py dynamic

Importable:
    from ctl import set_mode
    set_mode('static')          # returns True if the controller logged the switch
"""
import csv
import os
import socket
import sys
import time

SOCK_PATH = '/tmp/bwm_controller_alert.sock'
STATS_CSV = os.path.join('results', 'controller_stats.csv')
MODES = ('dynamic', 'static')


def _confirmed(mode, since, timeout=4.0):
    """Look for the controller's own 'MODE->mode' row written after `since`.
    Best effort: returns False if the CSV is missing (controller started in a
    different directory) or the controller is an older version."""
    deadline = time.time() + timeout
    needle = f'MODE->{mode}'
    while time.time() < deadline:
        try:
            with open(STATS_CSV, newline='') as f:
                for row in csv.DictReader(f):
                    if row.get('event') == needle and float(row['timestamp']) >= since - 0.5:
                        return True
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.25)
    return False


def set_mode(mode, sock_path=SOCK_PATH, confirm=True):
    mode = mode.strip().lower()
    if mode not in MODES:
        raise ValueError(f'mode must be one of {MODES}, got {mode!r}')
    since = time.time()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.sendto(f'MODE:{mode}'.encode(), sock_path)
    except OSError as e:
        raise SystemExit(f'Could not reach the controller at {sock_path}: {e}\n'
                         'Is `python3 run_controller.py controller.sdn_controller` running?')
    finally:
        s.close()
    if not confirm:
        return True
    ok = _confirmed(mode, since)
    if not ok:
        print(f'[ctl] WARNING: no "MODE->{mode}" row seen in {STATS_CSV}. The message was '
              'sent, but check the controller terminal for "MODE -> ..." and that it is '
              'the updated sdn_controller.py.', file=sys.stderr)
    return ok


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1].lower() not in MODES:
        raise SystemExit('Usage: python3 experiments/ctl.py static|dynamic')
    set_mode(sys.argv[1])
    print(f'[ctl] controller mode requested: {sys.argv[1].lower()}')
