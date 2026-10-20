import { activateSession, normalizeComparison, selectGame } from './sessionData';
import { timeline, replayPair } from '../lib/ghost';

const sample = (index, extra = {}) => ({ lat: 45.62 + index * .0001, lon: 9.28 + index * .0001,
  x_m: index * 10, y_m: index * 5, distance_pct: index / 4, elapsed_s: index, segment: 0, ...extra });

beforeEach(() => {
  selectGame('lmu');
  activateSession({ filename: 'lmu-test', provider: 'LMU', session: { track_length_m: 5000 }, laps: [] });
});
afterEach(() => selectGame('iracing'));

test('LMU ignores old geographic alignment responses and keeps schematic replay clocks', () => {
  const target = [sample(0, { elapsed_s: 2.7 }), sample(1, { elapsed_s: 5 }), sample(2, { elapsed_s: 9 })];
  const reference = [sample(0), sample(1), sample(2)];
  const result = normalizeComparison({ provider: 'LMU', geographic_alignment: { status: 'AVAILABLE' }, track_path: target, reference_track_path: reference });
  expect(result.paths.geographic).toBe(false);
  expect(result.paths.mapStatus).toBe('schematic');
  expect(result.paths.alignment).toBeUndefined();
  expect(result.paths.target.map(point => [point.x, point.y])).toEqual([[0, -0], [10, -5], [20, -10]]);
  const replay = replayPair(timeline(result.paths.target), timeline(result.paths.reference));
  expect(replay.span).toEqual([0, 9]);
  expect(replay.target[0].t).toBe(2.7);
});

test.each([undefined, { status: 'UNAVAILABLE', reason: '기준 지도와 일치하지 않습니다.' }])('unverified registration %p keeps raw schematic coordinates even when GPS-looking values exist', alignment => {
  const path = [sample(0), sample(1), sample(2, { segment: 1 })];
  const result = normalizeComparison({ provider: 'LMU', geographic_alignment: alignment, track_path: path, reference_track_path: path });
  expect(result.paths.geographic).toBe(false);
  expect(result.paths.mapStatus).toBe('schematic');
  expect(result.paths.target.map(point => [point.x, point.y])).toEqual([[0, -0], [10, -5], [20, -10]]);
  expect(result.paths.target[2].breakBefore).toBe(true);
  expect(result.paths.alignment).toBeUndefined();
  expect(result.paths.origin).toBeUndefined();
});

test('LMU steering is signed percent and missing inputs remain missing while neutral and reverse are preserved', () => {
  const trace = [
    { distance_pct: 0, target_steering_ratio: -.35, reference_steering_ratio: .1, target_steering_deg: 999, target_gear: 0, reference_gear: -1 },
    { distance_pct: .5, target_steering_ratio: 0, reference_steering_ratio: null, target_gear: 2, reference_gear: null },
    { distance_pct: 1, target_steering_ratio: NaN, reference_steering_ratio: undefined, target_gear: null },
  ];
  const rows = normalizeComparison({ provider: 'LMU', trace }).rows;
  expect(rows[0]).toMatchObject({ distance: 0, steering: -35, referenceSteering: 10, gear: 0, referenceGear: -1 });
  expect(rows[1]).toMatchObject({ distance: 2500, steering: 0, referenceSteering: null, gear: 2, referenceGear: null });
  expect(rows[2].steering).toBeNull();
  expect(rows[2].referenceSteering).toBeNull();
  expect(rows[2].gear).toBeNull();
});

test('iRacing steering remains degrees even if a ratio is present', () => {
  const result = normalizeComparison({ provider: 'IRACING', trace: [{ distance_pct: .2, target_steering_deg: 42, reference_steering_deg: -18, target_steering_ratio: .9 }] });
  expect(result.rows[0]).toMatchObject({ steering: 42, referenceSteering: -18 });
});

test('schematic fallback never joins a line across an invalid coordinate sample', () => {
  const path = [sample(0), sample(1, { x_m: NaN }), sample(2), sample(3)];
  const result = normalizeComparison({ provider: 'LMU', track_path: path, reference_track_path: path });
  expect(result.paths.target).toHaveLength(3);
  expect(result.paths.target[1].breakBefore).toBe(true);
  expect(result.paths.reference[1].breakBefore).toBe(true);
});
