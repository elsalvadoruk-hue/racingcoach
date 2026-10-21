"""Replay an LMU diagnostic journal through the iRacing live coach.

python scripts/replay_lmu_live.py logs/lmu-live/<id>.jsonl [--limit N] [--quiet]

Feeds each recorded LMU frame to ``LmuIrsdk`` and the shared ``LiveTelemetry``
tick on the recorded clock, and prints every spotter call and radio call the
driver would have heard (each radio call is treated as heard once shown).
Uses a temporary data folder; no audio, network or personal data is touched.
"""
import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))


class JournalSource:
    def __init__(self, path, limit=None):
        self.rows = []
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                row = json.loads(line)
                if row.get('type') == 'SNAPSHOT':
                    self.rows.append(row)
                    if limit and len(self.rows) >= limit:
                        break
        self.index = -1

    def open(self):
        return {}

    def read(self, timeout_ms=0):
        self.index += 1
        return self.rows[self.index]['snapshot'] if self.index < len(self.rows) else None

    def close(self):
        pass


def replay(path, limit=None, echo=print, traffic_dir=None):
    from lmu_irsdk import LmuIrsdk
    from live_telemetry import LiveTelemetry

    source = JournalSource(path, limit)
    now = [0.]
    clock = lambda: now[0]
    sdk = LmuIrsdk(source_factory=lambda: source, clock=clock, normalised=True)
    live = LiveTelemetry(clock=clock)
    if traffic_dir is not None:
        from traffic_log import TrafficLog
        live.traffic_log = TrafficLog(traffic_dir)
    calls, heard, spot = [], set(), 0
    for row in source.rows:
        now[0] = 1_000_000 + row['observed_elapsed_s']
        live._tick(sdk)
        status = live.spotter_status(after=spot)
        for event in status['events']:
            spot = max(spot, event['serial'])
            calls.append(('spotter', event['key'], event['text'], row['observed_elapsed_s']))
        for event in status['radio'].get('events', []):
            if event['id'] in heard:
                continue
            heard.add(event['id'])
            calls.append(('radio', event.get('key'), event.get('text'), row['observed_elapsed_s']))
            live.radio.ack(event['id'], now[0])
    live.traffic_log.close()
    for kind, key, text, at in calls:
        echo(f'{at:8.1f}s  {kind:<7} {key or "":<16} {text}')
    return live, calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('journal')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--traffic-dir', type=Path)
    args = parser.parse_args()
    os.environ.setdefault('IRACING_TUNER_DB_PATH', os.path.join(tempfile.mkdtemp(prefix='lmu-replay-'), 'db.sqlite3'))
    live, calls = replay(args.journal, args.limit, echo=(lambda *_: None) if args.quiet else print,
                         traffic_dir=args.traffic_dir)
    print('traffic_dropped_rows', live.traffic_log.dropped_rows)
    print('state', live.state, '·', live.message)
    print('calls', dict(Counter((kind, key) for kind, key, _, _ in calls)))


if __name__ == '__main__':
    main()
