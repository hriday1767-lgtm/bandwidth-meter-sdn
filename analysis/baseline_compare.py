#!/usr/bin/env python3
"""
Static-baseline vs dynamic-rerouting comparison.

Reads the run directories written by experiments/run_baseline.py (or
experiments/run_multiclient.py with --kind multiclient) and prints / saves a
side-by-side table:  loss %, delivered throughput, delivery ratio and the
number of controller REROUTE events inside each run's time window.

Usage:
    python3 analysis/baseline_compare.py                       # newest results/baseline_*
    python3 analysis/baseline_compare.py --dir results/baseline_20261023_101500
    python3 analysis/baseline_compare.py --kind multiclient    # newest results/multiclient_*
Options:
    --controller PATH   controller_stats.csv (default results/controller_stats.csv)
    --out PATH          output CSV (default <dir>/baseline_comparison.csv)
    --no-plot           skip the PNG bar chart (also skipped if matplotlib is missing)
"""
import argparse
import csv
import glob
import os
from collections import defaultdict
from statistics import mean

LOAD_ORDER = ['light', 'moderate', 'heavy', 'multiclient']


def newest(kind):
    dirs = sorted(glob.glob(os.path.join('results', f'{kind}_*')))
    dirs = [d for d in dirs if os.path.isfile(os.path.join(d, 'run_index.csv'))]
    if not dirs:
        raise SystemExit(f'No results/{kind}_*/run_index.csv found -- run the experiment first.')
    return dirs[-1]


def read_csv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def controller_events(path):
    try:
        rows = read_csv(path)
    except OSError:
        print(f'[warn] cannot read {path}; reroute counts will be 0')
        return []
    out = []
    for r in rows:
        try:
            out.append((float(r['timestamp']), r.get('event', '') or ''))
        except (ValueError, KeyError):
            pass
    return out


def analyse_run(run, base, events):
    rows = read_csv(os.path.join(base, run['run_dir'], 'receiver_summary.csv')) \
        if os.path.isfile(os.path.join(base, run['run_dir'], 'receiver_summary.csv')) else []
    t0, t1 = float(run['start_ts']), float(run['end_ts'])
    win = [e for ts, e in events if t0 <= ts <= t1]
    expected = sum(int(r['expected']) for r in rows)
    received = sum(int(r['received']) for r in rows)
    tput = sum(float(r['throughput_bps']) for r in rows)
    return {
        'expected': expected, 'received': received,
        'loss_pct': (expected - received) / expected * 100 if expected else float('nan'),
        'delivery_pct': received / expected * 100 if expected else float('nan'),
        'throughput_mbps': tput / 1e6,
        'reroutes': sum(1 for e in win if e.startswith('REROUTE->backup')),
        'static_markers': sum(1 for e in win if e.startswith('STATIC:congestion-no-action')),
        'flows': len(rows),
        'per_flow_loss': {r['flow_id']: float(r['loss_pct']) for r in rows},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--dir')
    ap.add_argument('--kind', choices=['baseline', 'multiclient'], default='baseline')
    ap.add_argument('--controller', default=os.path.join('results', 'controller_stats.csv'))
    ap.add_argument('--out')
    ap.add_argument('--no-plot', action='store_true')
    args = ap.parse_args()

    base = args.dir or newest(args.kind)
    runs = read_csv(os.path.join(base, 'run_index.csv'))
    events = controller_events(args.controller)

    groups = defaultdict(list)             # (load, mode) -> [metrics]
    for run in runs:
        groups[(run['load'], run['mode'])].append(analyse_run(run, base, events))

    loads = sorted({k[0] for k in groups}, key=lambda l: LOAD_ORDER.index(l) if l in LOAD_ORDER else 99)
    hdr = f'{"load":<12}{"mode":<9}{"runs":>4}{"loss %":>9}{"deliv %":>9}{"tput Mbps":>11}{"reroutes":>10}{"static-flags":>14}'
    print(f'\nSource: {base}\n')
    print(hdr)
    print('-' * len(hdr))

    table = []
    for load in loads:
        for mode in ('static', 'dynamic'):
            ms = groups.get((load, mode))
            if not ms:
                continue
            row = {
                'load': load, 'mode': mode, 'runs': len(ms),
                'loss_pct': mean(m['loss_pct'] for m in ms),
                'delivery_pct': mean(m['delivery_pct'] for m in ms),
                'throughput_mbps': mean(m['throughput_mbps'] for m in ms),
                'reroutes': mean(m['reroutes'] for m in ms),
                'static_markers': mean(m['static_markers'] for m in ms),
            }
            table.append(row)
            print(f'{load:<12}{mode:<9}{row["runs"]:>4}{row["loss_pct"]:>9.2f}{row["delivery_pct"]:>9.2f}'
                  f'{row["throughput_mbps"]:>11.2f}{row["reroutes"]:>10.1f}{row["static_markers"]:>14.1f}')
        s = next((r for r in table if r['load'] == load and r['mode'] == 'static'), None)
        d = next((r for r in table if r['load'] == load and r['mode'] == 'dynamic'), None)
        if s and d:
            print(f'{"":<12}-> dynamic changes loss by {d["loss_pct"] - s["loss_pct"]:+.2f} pp, '
                  f'throughput by {d["throughput_mbps"] - s["throughput_mbps"]:+.2f} Mbps')

    if args.kind == 'multiclient':
        print('\nPer-flow loss % (mean over runs):')
        for mode in ('static', 'dynamic'):
            acc = defaultdict(list)
            for m in groups.get(('multiclient', mode), []):
                for fid, v in m['per_flow_loss'].items():
                    acc[fid].append(v)
            print(f'  {mode:<8}' + '  '.join(f'flow {fid}: {mean(v):.2f}%' for fid, v in sorted(acc.items())))

    out = args.out or os.path.join(base, 'baseline_comparison.csv')
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()) if table else ['load'])
        w.writeheader()
        w.writerows(table)
    print(f'\nWrote {out}')

    if not args.no_plot and table:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError:
            print('[info] matplotlib not installed; skipping chart')
            return
        import numpy as np
        x = np.arange(len(loads))
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        for ax, key, title in ((axes[0], 'loss_pct', 'Packet loss (%)'),
                               (axes[1], 'throughput_mbps', 'Delivered throughput (Mbps)')):
            for i, (mode, color) in enumerate((('static', '#c0504d'), ('dynamic', '#2e75b6'))):
                vals = [next((r[key] for r in table if r['load'] == l and r['mode'] == mode), 0) for l in loads]
                ax.bar(x + (i - 0.5) * 0.38, vals, 0.38, label=mode, color=color)
            ax.set_xticks(x)
            ax.set_xticklabels(loads)
            ax.set_title(title)
            ax.legend()
        fig.suptitle('Static baseline vs dynamic SDN rerouting')
        fig.tight_layout()
        png = os.path.splitext(out)[0] + '.png'
        fig.savefig(png, dpi=150)
        print(f'Wrote {png}')


if __name__ == '__main__':
    main()
