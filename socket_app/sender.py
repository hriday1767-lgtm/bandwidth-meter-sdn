#!/usr/bin/env python3
import argparse
import csv
import os
import socket
import struct
import time

HEADER = struct.Struct('!IdI')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dst', required=True)
    ap.add_argument('--port', type=int, default=5005)
    ap.add_argument('--rate', type=float, default=500)
    ap.add_argument('--duration', type=float, default=30)
    ap.add_argument('--size', type=int, default=1024)
    ap.add_argument('--flow-id', type=int, default=1)
    ap.add_argument('--out', default='results/sender_stats.csv')
    args = ap.parse_args()

    if args.size < HEADER.size:
        raise SystemExit(f'--size must be >= {HEADER.size} bytes')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.rate
    payload_filler = b'x' * (args.size - HEADER.size)

    seq = 0
    bytes_sent = 0
    start = time.time()
    next_send = start
    end_time = start + args.duration

    while time.time() < end_time:
        ts = time.time()
        packet = HEADER.pack(seq, ts, args.flow_id) + payload_filler
        sock.sendto(packet, (args.dst, args.port))
        bytes_sent += len(packet)
        seq += 1
        next_send += interval
        sleep_for = next_send - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)

    elapsed = time.time() - start
    throughput_bps = (bytes_sent * 8) / elapsed if elapsed > 0 else 0

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    write_header = not os.path.exists(args.out)
    with open(args.out, 'a', newline='') as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(['flow_id', 'dst', 'rate_target_pps', 'size_bytes',
                        'packets_sent', 'bytes_sent', 'elapsed_s', 'throughput_bps'])
        w.writerow([args.flow_id, args.dst, args.rate, args.size,
                    seq, bytes_sent, f'{elapsed:.4f}', f'{throughput_bps:.2f}'])

if __name__ == '__main__':
    main()
