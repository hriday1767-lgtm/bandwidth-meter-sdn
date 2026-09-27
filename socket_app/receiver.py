#!/usr/bin/env python3
import argparse
import collections
import csv
import os
import socket
import struct
import time

HEADER = struct.Struct('!IdI')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=5005)
    ap.add_argument('--idle-timeout', type=float, default=5.0)
    ap.add_argument('--loss-alert-pct', type=float, default=5.0)
    ap.add_argument('--alert-sock', default='/tmp/bwm_controller_alert.sock')
    ap.add_argument('--out-dir', default='results')
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', args.port))
    sock.settimeout(1.0)

    alert_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) if args.alert_sock else None
    os.makedirs(args.out_dir, exist_ok=True)

    per_flow = collections.defaultdict(lambda: {
        'min_seq': None, 'max_seq': None, 'received': 0, 'bytes': 0,
        'first_ts': None, 'last_ts': None, 'timeseries': [],
    })
    second_buckets = collections.defaultdict(lambda: {'bytes': 0, 'packets': 0})
    already_alerted = set()
    last_rx_time = time.time()
    current_second = int(time.time())

    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            got_any = any(f['received'] for f in per_flow.values())
            if got_any and (time.time() - last_rx_time > args.idle_timeout):
                break
            continue

        recv_ts = time.time()
        last_rx_time = recv_ts
        if len(data) < HEADER.size:
            continue
        seq, _send_ts, flow_id = HEADER.unpack_from(data)

        f = per_flow[flow_id]
        f['received'] += 1
        f['bytes'] += len(data)
        f['min_seq'] = seq if f['min_seq'] is None else min(f['min_seq'], seq)
        f['max_seq'] = seq if f['max_seq'] is None else max(f['max_seq'], seq)
        f['first_ts'] = f['first_ts'] or recv_ts
        f['last_ts'] = recv_ts

        sec = int(recv_ts)
        bucket = second_buckets[(flow_id, sec)]
        bucket['bytes'] += len(data)
        bucket['packets'] += 1

        if sec > current_second:
            _flush(per_flow, second_buckets, sec, args, alert_sock, already_alerted)
            current_second = sec

    _flush(per_flow, second_buckets, current_second + 2, args, alert_sock, already_alerted)
    _write_summaries(per_flow, args.out_dir)
    if alert_sock is not None:
        alert_sock.close()

def _flush(per_flow, second_buckets, up_to_second, args, alert_sock, already_alerted):
    for key in [k for k in second_buckets if k[1] < up_to_second]:
        flow_id, sec = key
        bucket = second_buckets.pop(key)
        per_flow[flow_id]['timeseries'].append(
            [sec, bucket['packets'], bucket['bytes'], bucket['bytes'] * 8])

    for flow_id, f in per_flow.items():
        expected = (f['max_seq'] - f['min_seq'] + 1) if f['max_seq'] is not None else 0
        if expected < 20:
            continue
        loss_pct = max(expected - f['received'], 0) / expected * 100
        if (alert_sock is not None and loss_pct > args.loss_alert_pct
                and flow_id not in already_alerted):
            msg = f'LOSS:{loss_pct:.2f}:{flow_id}'.encode()
            try:
                alert_sock.sendto(msg, args.alert_sock)
            except OSError as e:
                pass
            already_alerted.add(flow_id)

def _write_summaries(per_flow, out_dir):
    for flow_id, f in per_flow.items():
        expected = (f['max_seq'] - f['min_seq'] + 1) if f['max_seq'] is not None else 0
        lost = max(expected - f['received'], 0)
        loss_pct = (lost / expected * 100) if expected else 0.0
        duration = (f['last_ts'] - f['first_ts']) if f['last_ts'] and f['first_ts'] else 0
        throughput_bps = (f['bytes'] * 8 / duration) if duration > 0 else 0

        summary_path = os.path.join(out_dir, 'receiver_summary.csv')
        write_header = not os.path.exists(summary_path)
        with open(summary_path, 'a', newline='') as fp:
            w = csv.writer(fp)
            if write_header:
                w.writerow(['flow_id', 'expected', 'received', 'lost', 'loss_pct',
                            'duration_s', 'throughput_bps'])
            w.writerow([flow_id, expected, f['received'], lost, f'{loss_pct:.2f}',
                        f'{duration:.4f}', f'{throughput_bps:.2f}'])

        ts_path = os.path.join(out_dir, f'receiver_timeseries_flow{flow_id}.csv')
        with open(ts_path, 'w', newline='') as fp:
            w = csv.writer(fp)
            w.writerow(['second', 'packets', 'bytes', 'throughput_bps'])
            for row in sorted(f['timeseries']):
                w.writerow(row)

if __name__ == '__main__':
    main()