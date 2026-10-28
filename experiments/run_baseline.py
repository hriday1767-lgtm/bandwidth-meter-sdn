#!/usr/bin/env python3
"""
Static-baseline vs dynamic-rerouting experiment.

Runs the SAME traffic loads twice -- once with the controller's rerouting
disabled (static baseline) and once with it enabled (proposed approach) --
so the benefit of the dynamic SDN behaviour can be shown side by side.
This closes the rubric item "proposed approach is compared with a suitable
baseline/static approach".

Run from the project root, with the controller already running:
    Terminal 1:  python3 run_controller.py controller.sdn_controller
    Terminal 2:  sudo python3 experiments/run_baseline.py
    Then:        python3 analysis/baseline_compare.py

Options:
    --repeats N       repeat every (load, mode) pair N times (default 2)
    --loads a,b,c     subset of: light,moderate,heavy (default: all three)
"""
import argparse
import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'topology'))

from topo import create_network                                   # noqa: E402
from run_experiment import (LOAD_LEVELS, RECEIVER_PORT,           # noqa: E402
                            wait_for_controller_flows)
from ctl import set_mode                                          # noqa: E402

from mininet.log import setLogLevel, info                         # noqa: E402

LOAD_NAMES = {1: 'light', 2: 'moderate', 3: 'heavy'}
ALERT_SOCK = '/tmp/bwm_controller_alert.sock'
SUMMARY_WAIT_S = 25          # receiver idle-timeout is 4 s; allow generous slack


def wait_for_file(path, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        time.sleep(0.5)
    return False


def run_once(h1, h2, mode, load, rep, base_dir, index_writer):
    flow_id, rate, duration, size, _label = load
    name = LOAD_NAMES[flow_id]
    run_dir = os.path.join(base_dir, f'{mode}__{name}__rep{rep}')
    os.makedirs(run_dir, exist_ok=True)
    info(f'\n*** [{mode.upper():7s}] load={name} rep={rep}  -> {run_dir}\n')

    set_mode(mode)            # also resets the flow to the primary path
    time.sleep(2)             # let one poll cycle pass in the new mode

    h2.cmd(f'python3 socket_app/receiver.py --port {RECEIVER_PORT} --idle-timeout 4 '
           f'--out-dir {run_dir} --alert-sock {ALERT_SOCK} '
           f'> {run_dir}/receiver.log 2>&1 &')
    time.sleep(1)

    start_ts = time.time()
    info(h1.cmd(f'python3 socket_app/sender.py --dst {h2.IP()} --port {RECEIVER_PORT} '
                f'--rate {rate} --duration {duration} --size {size} --flow-id {flow_id} '
                f'--out {run_dir}/sender_stats.csv'))

    summary = os.path.join(run_dir, 'receiver_summary.csv')
    if not wait_for_file(summary, SUMMARY_WAIT_S):
        info(f'*** WARNING: no receiver summary appeared in {SUMMARY_WAIT_S}s for this run\n')
    end_ts = time.time()

    index_writer.writerow([os.path.basename(run_dir), mode, name, rep, flow_id, rate, size,
                           f'{start_ts:.3f}', f'{end_ts:.3f}'])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--repeats', type=int, default=2)
    ap.add_argument('--loads', default='light,moderate,heavy')
    args = ap.parse_args()

    wanted = [n.strip().lower() for n in args.loads.split(',') if n.strip()]
    bad = [n for n in wanted if n not in LOAD_NAMES.values()]
    if bad:
        raise SystemExit(f'Unknown load name(s) {bad}; choose from {list(LOAD_NAMES.values())}')
    loads = [l for l in LOAD_LEVELS if LOAD_NAMES[l[0]] in wanted]

    setLogLevel('info')
    base_dir = os.path.join('results', time.strftime('baseline_%Y%m%d_%H%M%S'))
    os.makedirs(base_dir, exist_ok=True)

    net = create_network()
    net.start()
    h1, h2 = net.get('h1'), net.get('h2')
    try:
        if not wait_for_controller_flows(h1, h2):
            raise SystemExit(1)

        with open(os.path.join(base_dir, 'run_index.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['run_dir', 'mode', 'load', 'rep', 'flow_id', 'rate_pps',
                        'size_bytes', 'start_ts', 'end_ts'])
            for rep in range(1, args.repeats + 1):
                for load in loads:
                    for mode in ('static', 'dynamic'):
                        run_once(h1, h2, mode, load, rep, base_dir, w)
                        f.flush()
    finally:
        try:
            set_mode('dynamic', confirm=False)      # leave the controller in normal mode
        except SystemExit:
            pass
        net.stop()

    info(f'\n*** Done. Results in {base_dir}\n'
         f'*** Next:  python3 analysis/baseline_compare.py --dir {base_dir}\n')


if __name__ == '__main__':
    main()