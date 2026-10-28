#!/usr/bin/env python3
"""
Multi-client experiment: TWO senders transmit to the receiver AT THE SAME TIME.

Closes the mandatory-requirement item "support multiple communicating
nodes/clients wherever applicable".

Topology: h1 and h3 are both senders (h3 hangs off s1, port 4), h2 is the
single receiver. Each sender offers ~3.3 Mbps -- individually BELOW the
controller's 4 Mbps congestion threshold -- but together they offer ~6.6 Mbps,
more than the 5 Mbit/s primary link. So:
  * the receiver must separate the two flows (flow ids 11 and 12) and report
    loss/throughput per flow;
  * the controller must react to the AGGREGATE load it sees on the shared
    h2-bound flow entry, not to either sender alone.
The run is repeated with rerouting disabled (static baseline) and enabled
(dynamic) so the result doubles as a second baseline comparison.

Run from the project root, controller already running:
    Terminal 1:  python3 run_controller.py controller.sdn_controller
    Terminal 2:  sudo python3 experiments/run_multiclient.py [--repeats N]
    Then:        python3 analysis/baseline_compare.py --kind multiclient
"""
import argparse
import csv
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'topology'))

from topo import create_network                                        # noqa: E402
from run_experiment import RECEIVER_PORT, wait_for_controller_flows    # noqa: E402
from run_baseline import wait_for_file, ALERT_SOCK, SUMMARY_WAIT_S      # noqa: E402
from ctl import set_mode                                               # noqa: E402

from mininet.log import setLogLevel, info                              # noqa: E402

# (host, flow_id, packets/s, payload bytes, duration s)
SENDERS = [('h1', 11, 400, 1024, 25),
           ('h3', 12, 400, 1024, 25)]


def wait_for_host(src, dst, tries=30):
    for i in range(1, tries + 1):
        out = src.cmd(f'ping -c 1 -W 1 {dst.IP()}')
        if re.search(r'\b0% packet loss\b', out):
            info(f'*** {src.name} -> {dst.name} reachable (after {i}s)\n')
            return True
        time.sleep(1)
    return False


def run_once(net, mode, rep, base_dir, index_writer):
    h2 = net.get('h2')
    run_dir = os.path.join(base_dir, f'{mode}__rep{rep}')
    os.makedirs(run_dir, exist_ok=True)
    info(f'\n*** [{mode.upper():7s}] two simultaneous senders, rep={rep} -> {run_dir}\n')

    set_mode(mode)
    time.sleep(2)

    h2.cmd(f'python3 socket_app/receiver.py --port {RECEIVER_PORT} --idle-timeout 4 '
           f'--out-dir {run_dir} --alert-sock {ALERT_SOCK} '
           f'> {run_dir}/receiver.log 2>&1 &')
    time.sleep(1)

    start_ts = time.time()
    hosts = []
    for host, flow_id, rate, size, duration in SENDERS:       # start both, non-blocking
        h = net.get(host)
        h.sendCmd(f'python3 socket_app/sender.py --dst {h2.IP()} --port {RECEIVER_PORT} '
                  f'--rate {rate} --duration {duration} --size {size} --flow-id {flow_id} '
                  f'--out {run_dir}/sender_stats.csv')
        hosts.append(h)
    for h in hosts:                                           # then wait for both
        info(h.waitOutput())

    if not wait_for_file(os.path.join(run_dir, 'receiver_summary.csv'), SUMMARY_WAIT_S):
        info(f'*** WARNING: no receiver summary appeared in {SUMMARY_WAIT_S}s\n')
    time.sleep(1)              # both flows' summary rows are written back to back
    end_ts = time.time()

    index_writer.writerow([os.path.basename(run_dir), mode, 'multiclient', rep,
                           '+'.join(str(s[1]) for s in SENDERS),
                           '+'.join(str(s[2]) for s in SENDERS),
                           SENDERS[0][3], f'{start_ts:.3f}', f'{end_ts:.3f}'])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--repeats', type=int, default=2)
    args = ap.parse_args()

    setLogLevel('info')
    base_dir = os.path.join('results', time.strftime('multiclient_%Y%m%d_%H%M%S'))
    os.makedirs(base_dir, exist_ok=True)

    net = create_network(multi_client=True)
    net.start()
    h1, h2, h3 = net.get('h1'), net.get('h2'), net.get('h3')
    try:
        if not wait_for_controller_flows(h1, h2) or not wait_for_host(h3, h2):
            raise SystemExit(1)

        with open(os.path.join(base_dir, 'run_index.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['run_dir', 'mode', 'load', 'rep', 'flow_id', 'rate_pps',
                        'size_bytes', 'start_ts', 'end_ts'])
            for rep in range(1, args.repeats + 1):
                for mode in ('static', 'dynamic'):
                    run_once(net, mode, rep, base_dir, w)
                    f.flush()
    finally:
        try:
            set_mode('dynamic', confirm=False)
        except SystemExit:
            pass
        net.stop()

    info(f'\n*** Done. Results in {base_dir}\n'
         f'*** Next:  python3 analysis/baseline_compare.py --kind multiclient --dir {base_dir}\n')


if __name__ == '__main__':
    main()
