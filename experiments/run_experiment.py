#!/usr/bin/env python3
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'topology'))
from topo import create_network

from mininet.log import setLogLevel, info

LOAD_LEVELS = [
    (1, 200, 20, 512, 'light'),
    (2, 800, 25, 1024, 'moderate'),
    (3, 1500, 25, 1024, 'heavy'),
]

RECEIVER_PORT = 5005
RESULTS_DIR = 'results'
CONTROLLER_WAIT_MAX_TRIES = 30
CONTROLLER_WAIT_INTERVAL = 1.0

def wait_for_controller_flows(h1, h2):
    for attempt in range(1, CONTROLLER_WAIT_MAX_TRIES + 1):
        out = h1.cmd(f'ping -c 1 -W 1 {h2.IP()}')
        if re.search(r'\b0% packet loss\b', out) or ' 1 received' in out:
            return True
        time.sleep(CONTROLLER_WAIT_INTERVAL)
    return False

def main():
    setLogLevel('info')
    os.makedirs(RESULTS_DIR, exist_ok=True)

    net = create_network()
    net.start()
    h1, h2 = net.get('h1'), net.get('h2')

    if not wait_for_controller_flows(h1, h2):
        net.stop()
        raise SystemExit(1)

    for flow_id, rate, duration, size, label in LOAD_LEVELS:
        recv_cmd = (f'python3 socket_app/receiver.py --port {RECEIVER_PORT} '
                    f'--idle-timeout 4 --out-dir {RESULTS_DIR} '
                    f'--alert-sock /tmp/bwm_controller_alert.sock '
                    f'> {RESULTS_DIR}/receiver_flow{flow_id}.log 2>&1 &')
        h2.cmd(recv_cmd)
        time.sleep(1)

        send_cmd = (f'python3 socket_app/sender.py --dst {h2.IP()} --port {RECEIVER_PORT} '
                    f'--rate {rate} --duration {duration} --size {size} --flow-id {flow_id} '
                    f'--out {RESULTS_DIR}/sender_stats.csv')
        h1.cmd(send_cmd)
        time.sleep(6)

    from mininet.cli import CLI
    CLI(net)
    net.stop()

if __name__ == '__main__':
    main()