#!/usr/bin/env python3
"""List the controller's MODE / REROUTE / STATIC events in readable form.
Usage: python3 analysis/show_events.py [results/controller_stats.csv]"""
import csv
import sys

path = sys.argv[1] if len(sys.argv) > 1 else 'results/controller_stats.csv'
print(f"{'time(s)':>10}  {'path':<8} {'rate':>11}  event")
for r in csv.DictReader(open(path, newline='')):
    e = r['event']
    if e.startswith(('MODE', 'REROUTE', 'STATIC')):
        print(f"{float(r['timestamp']) % 100000:10.1f}  {r['active_path']:<8} "
              f"{float(r['rate_bps']) / 1e6:6.2f} Mbps  {e}")
