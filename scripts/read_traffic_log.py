"""Summarize local race traffic logs, optionally flattening observations to CSV."""
import argparse
import csv
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
from traffic_log import FIELDS, OWN_FIELDS, read


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?', type=Path)
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--csv', type=Path)
    args = parser.parse_args(argv)
    path = args.file
    if path is None:
        import os
        from app_settings import data_dir
        folder = Path(os.getenv('IRACING_TRAFFIC_LOG_DIR') or data_dir() / 'traffic-logs')
        files = sorted(folder.glob('traffic-*.jsonl.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            parser.error('No traffic log found')
        path = files[0]
    header, rows = read(path)
    counts = sorted(len(row['c']) for row in rows)
    p95 = counts[min(len(counts) - 1, int((len(counts) - 1) * .95))] if counts else None
    duration = rows[-1]['t'] - rows[0]['t'] if rows else 0.
    print(f"{header.get('game')} · {header.get('track')}")
    print(f"rows={len(rows)} duration_s={duration:.3f} bytes={path.stat().st_size}")
    print(f"nearby median={statistics.median(counts) if counts else None} p95={p95} max={max(counts) if counts else None}")
    print(f"pit_rows={sum(bool(row['own'].get('on_pit_road')) for row in rows)}")
    if args.csv:
        names = ['t'] + ['own_' + k for k in OWN_FIELDS] + FIELDS
        with args.csv.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=names)
            writer.writeheader()
            for row in rows:
                base = {'t': row['t'], **{'own_' + k: row['own'].get(k) for k in OWN_FIELDS}}
                for car in row['c'] or [{}]:
                    writer.writerow({**base, **car})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
