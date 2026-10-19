"""Read a race timing log (data/timing-logs/*.jsonl.gz) and see how CarIdxF2Time behaves.

    python scripts/read_timing_log.py                 # newest file
    python scripts/read_timing_log.py <file> [--csv out.csv]

What it prints:
  1. how often F2Time changes per car and where on the lap (continuous or at timing lines),
  2. its values in the pits, on the first lap and for cars a lap or more apart, and which car reads 0
     (overall leader or each class leader),
  3. the gap to the class car ahead/behind three ways, against a measured on-track gap:
       measured = how many seconds ago the car ahead was where the player is now (1Hz positions);
       f2       = F2Time difference; app = the app's current estimate (spotter_context.estimated_gap_s).
Only seconds of green racing (SessionState 4) count.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))

from spotter_context import estimated_gap_s  # noqa: E402
import timing_log  # noqa: E402

HISTORY_S = 400


def finite(value):
    return isinstance(value, (int, float)) and value == value


def progress(car):
    lap, pct = car.get('lap_completed'), car.get('distance_pct')
    return lap + pct if finite(lap) and finite(pct) and lap >= 0 and 0 <= pct <= 1 else None


def time_at(points, value):
    for (t0, p0), (t1, p1) in zip(points, list(points)[1:]):
        if p0 <= value <= p1 and p1 > p0:
            return t0 + (t1 - t0) * (value - p0) / (p1 - p0)
    return None


def quant(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def summary(values):
    if not values:
        return 'n=0'
    return f'n={len(values)}  중앙 {statistics.median(values):.3f}  90% {quant(values, .9):.3f}  최대 {max(values):.3f}'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('file', nargs='?')
    parser.add_argument('--csv', help='write every compared second to this CSV')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    path = Path(args.file) if args.file else None
    if path is None:
        files = sorted((ROOT / 'data' / 'timing-logs').glob('timing-*.jsonl.gz'), key=lambda p: p.stat().st_mtime)
        if not files:
            print('기록 파일이 없습니다 (data/timing-logs).')
            return 1
        path = files[-1]
    header, rows = timing_log.read(path)
    print(f'파일: {path.name}  트랙: {header.get("track")}  행: {len(rows)}')
    green = [row for row in rows if row.get('state') == 4]
    print(f'그린 상태(4) 초: {len(green)}')
    if not green:
        return 0

    # 1. update pattern
    last, changes, change_pct, intervals, last_change = {}, defaultdict(int), [], [], {}
    seen = defaultdict(int)
    for row in green:
        for car in row['c']:
            idx, f2 = car['idx'], car.get('f2_time_s')
            if not finite(f2) or car.get('on_pit_road'):
                continue
            seen[idx] += 1
            if idx in last and f2 != last[idx]:
                changes[idx] += 1
                if finite(car.get('distance_pct')):
                    change_pct.append(car['distance_pct'])
                if idx in last_change:
                    intervals.append(row['t'] - last_change[idx])
                last_change[idx] = row['t']
            last[idx] = f2
    share = [changes[i] / seen[i] for i in seen if seen[i] > 30]
    print('\n[1] F2Time 갱신')
    print(f'  1초 표본 중 값이 바뀐 비율(차별): {summary(share)}')
    print(f'  바뀐 간격(초): {summary(intervals)}')
    if change_pct:
        bins = [0] * 20
        for pct in change_pct:
            bins[min(19, int(pct * 20))] += 1
        print('  바뀐 위치(랩 5% 칸별 횟수): ' + ' '.join(str(b) for b in bins))

    # 2. special values
    pit, first, zero_classes, overall_zero = [], [], defaultdict(int), 0
    for row in green:
        zero = [c for c in row['c'] if finite(c.get('f2_time_s')) and abs(c['f2_time_s']) < 1e-6]
        for car in zero:
            if car.get('position') == 1:
                overall_zero += 1
            elif car.get('class_position') == 1:
                zero_classes[car.get('class_id')] += 1
        for car in row['c']:
            if car.get('on_pit_road') and finite(car.get('f2_time_s')):
                pit.append(car['f2_time_s'])
            if car.get('lap_completed') == 0 and finite(car.get('f2_time_s')):
                first.append(car['f2_time_s'])
    print('\n[2] 특별한 값')
    print(f'  피트로드 위 F2: {summary(pit)}  음수 {sum(v < 0 for v in pit)}개')
    print(f'  첫 랩 F2: {summary(first)}  음수 {sum(v < 0 for v in first)}개')
    print(f'  0을 가진 차: 전체 1위 {overall_zero}초, 클래스 1위(전체 1위 아님) {sum(zero_classes.values())}초 {dict(zero_classes)}')

    # 3. gaps against the measured on-track gap
    tracks = defaultdict(lambda: deque())
    paces, out = defaultdict(list), []
    errors = {'f2': defaultdict(list), 'app': defaultdict(list)}
    for row in rows:
        t = row['t']
        cars = {c['idx']: c for c in row['c']}
        for car in row['c']:
            p = progress(car)
            if p is None or car.get('on_pit_road'):
                continue
            track = tracks[car['idx']]
            if track and p < track[-1][1]:
                track.clear()                                 # reset / tow: positions are not continuous
            track.append((t, p))
            while track and t - track[0][0] > HISTORY_S:
                track.popleft()
            lap = car.get('last_lap_s')
            if finite(lap) and lap > 0 and (not paces[car['idx']] or paces[car['idx']][-1] != lap):
                paces[car['idx']].append(lap)
        own = cars.get(row.get('own'))
        if row.get('state') != 4 or not own or own.get('on_pit_road') or progress(own) is None:
            continue
        pos = own.get('class_position')
        if not finite(pos) or pos <= 0:
            continue
        pace = statistics.median(paces[own['idx']][-5:]) if paces[own['idx']] else None
        for side, wanted in (('ahead', pos - 1), ('behind', pos + 1)):
            other = next((c for c in row['c'] if c.get('class_id') == own.get('class_id') and c.get('class_position') == wanted), None)
            if not other or other.get('on_pit_road') or progress(other) is None:
                continue
            front, back = (other, own) if side == 'ahead' else (own, other)
            seen_at = time_at(tracks[front['idx']], progress(back))
            measured = t - seen_at if seen_at is not None else None
            f2 = (back['f2_time_s'] - front['f2_time_s']) if finite(back.get('f2_time_s')) and finite(front.get('f2_time_s')) else None
            delta = progress(other) - progress(own)
            est_lap = (header.get('classes') or {}).get(str(own.get('class_id')), {}).get('est_lap_s')
            app = estimated_gap_s({**own, 'class_est_lap_s': est_lap}, other, delta, pace) if abs(delta) < 1 else None
            out.append({'t': t, 'side': side, 'car': other['idx'], 'laps_apart': round(delta, 3), 'measured': measured, 'f2': f2, 'app': app})
            if measured is not None and measured < 60:
                band = 'under_3s' if measured < 3 else '3_to_10s' if measured < 10 else 'over_10s'
                for name, value in (('f2', f2), ('app', app)):
                    if value is not None:
                        errors[name][band].append(abs(value - measured))
    print('\n[3] 같은 클래스 앞·뒤 차 간격, 실측과의 차이(초)')
    for band in ('under_3s', '3_to_10s', 'over_10s'):
        print(f'  실측 {band}:')
        print(f'    F2Time 차이   {summary(errors["f2"][band])}')
        print(f'    앱 지금 추정  {summary(errors["app"][band])}')
    if args.csv:
        with open(args.csv, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['t', 'side', 'car', 'laps_apart', 'measured', 'f2', 'app'])
            writer.writeheader()
            writer.writerows(out)
        print(f'\nCSV: {args.csv} ({len(out)}행)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
