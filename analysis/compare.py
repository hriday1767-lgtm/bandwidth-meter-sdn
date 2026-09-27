#!/usr/bin/env python3
import argparse
import csv

def load_receiver(path):
    with open(path) as f:
        return {int(row['second']): float(row['throughput_bps']) for row in csv.DictReader(f)}

def load_controller(path):
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append({
                'second': int(float(row['timestamp'])),
                'rate_bps': float(row['rate_bps']),
                'path': row.get('active_path', ''),
                'event': row.get('event', ''),
            })
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--receiver', required=True)
    ap.add_argument('--controller', required=True)
    ap.add_argument('--out', default='results/comparison_report.csv')
    ap.add_argument('--pad-seconds', type=int, default=2)
    args = ap.parse_args()

    app = load_receiver(args.receiver)
    sdn = load_controller(args.controller)
    if not sdn:
        raise SystemExit('No rows in the controller stats CSV')
    if not app:
        raise SystemExit('No rows in the receiver timeseries CSV')

    window_start = min(app) - args.pad_seconds
    window_end = max(app) + args.pad_seconds
    sdn = [row for row in sdn if window_start <= row['second'] <= window_end]
    if not sdn:
        raise SystemExit('No controller rows fall inside this flow\'s time window')

    t0 = window_start

    with open(args.out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t_s', 'app_mbps', 'sdn_mbps', 'diff_pct', 'active_path', 'event'])
        for row in sdn:
            t = row['second'] - t0
            app_bps = app.get(row['second'], 0.0)
            sdn_bps = row['rate_bps']
            diff_pct = ((sdn_bps - app_bps) / app_bps * 100) if app_bps else 0.0
            w.writerow([t, f'{app_bps / 1e6:.3f}', f'{sdn_bps / 1e6:.3f}', f'{diff_pct:.1f}', row['path'], row['event']])

if __name__ == '__main__':
    main()