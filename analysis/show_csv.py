#!/usr/bin/env python3
"""Pretty-print a results CSV as an aligned table (quoted commas are handled,
unlike `column -s, -t`).  Usage: python3 analysis/show_csv.py results/comparison_flow3.csv"""
import csv
import sys

if len(sys.argv) != 2:
    raise SystemExit(__doc__)
rows = list(csv.reader(open(sys.argv[1], newline='')))
if not rows:
    raise SystemExit('empty file')
ncol = len(rows[0])
widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(ncol - 1)]
for r in rows:
    r = r + [''] * (ncol - len(r))
    head = '  '.join(r[i].rjust(widths[i]) for i in range(ncol - 1))
    print(f'{head}  {r[-1]}')
