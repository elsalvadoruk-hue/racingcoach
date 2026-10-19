import { findRegisteredTrackMap, gpsToRegisteredMap, projectComparisonPaths } from './trackMapProjection';
import library from '../data/registeredTrackMaps.json';

const map = {
  trackId: 127, name: 'Road Atlanta', layout: 'Full Course', sourceUrl: 'https://example.test/127-roadatlanta-full',
  origin: { lat: 0, lon: 0 }, transform: { a: .6, b: .8, tx: 10, ty: 20 }, viewBox: [0, 0, 200, 100],
};
// A 100,50 point maps to 110 m east, 70 m north under this registration.
const point = { lat: 70 / 110540, lon: 110 / 111320, distance_pct: .5 };
const path = [point, { ...point, distance_pct: .6 }];

test('uses exact layout identity and refuses a same-venue layout without a registered map', () => {
  expect(findRegisteredTrackMap({ track_platform_id: '523' }).trackId).toBe(523);
  expect(findRegisteredTrackMap({ track_platform_id: '163', track_path: 'spa 2024 up' })).toBeNull();
  expect(findRegisteredTrackMap({ track_platform_id: '262' })).toBeNull();
  expect(findRegisteredTrackMap({ track_path: 'roadatlanta full' }).trackId).toBe(127);
  expect(findRegisteredTrackMap({ track_name: 'Road Atlanta', track_config: 'Full Course' }).trackId).toBe(127);
  expect(findRegisteredTrackMap({ track_name: 'Road Atlanta', track_config: 'Club' })).toBeNull();
  expect(findRegisteredTrackMap({ track_name: 'Road Atlanta' })).toBeNull();
});

test('inverts the geographic registration with y-down and preserves real inter-driver separation', () => {
  expect(gpsToRegisteredMap(point, map).x).toBeCloseTo(100);
  expect(gpsToRegisteredMap(point, map).y).toBeCloseTo(50);
  const reference = path.map(p => ({ ...p, lon: p.lon + 10 / 111320 }));
  const result = projectComparisonPaths(path, reference, path, map);
  expect(result.map).toBe(map);
  expect(result.mapStatus).toBe('registered');
  expect(result.reference[0].x - result.target[0].x).toBeCloseTo(6);
  expect(result.reference[0].y - result.target[0].y).toBeCloseTo(8);
});

test('keeps missing GPS gaps and never fits distant GPS to a map', () => {
  const withHole = [point, { lat: null, lon: null, distance_pct: .55 }, { ...point, distance_pct: .6 }];
  const result = projectComparisonPaths(withHole, withHole, withHole, map);
  expect(result.target[1].breakBefore).toBe(true);
  expect(result.reference[1].breakBefore).toBe(true);
  const distant = path.map(p => ({ ...p, lat: p.lat + 30 }));
  const fallback = projectComparisonPaths(distant, distant, distant, map);
  expect(fallback.map).toBeNull();
  expect(fallback.mapStatus).toBe('gps-mismatch');
  expect(fallback.target).toEqual(fallback.reference);
  expect(fallback.geographic).toBe(true);
});

test('unsupported layouts retain a shared geographic frame for global imagery and real lap offsets', () => {
  const target = [{ lat: 0, lon: 0, distance_pct: 0 }, { lat: 0, lon: .001, distance_pct: 1 }];
  const reference = target.map(p => ({ ...p, lon: p.lon + 10 / 111319.49079327358 }));
  const result = projectComparisonPaths(target, reference);
  expect(result.geographic).toBe(true);
  expect(result.metersPerUnit).toBeCloseTo(1);
  expect(result.reference[0].x - result.target[0].x).toBeCloseTo(10);
  expect(result.reference[0].y).toBeCloseTo(result.target[0].y);
});

test('all imported maps have a usable registration and matching layout asset paths', () => {
  expect(library.maps).toHaveLength(17);
  expect(new Set(library.maps.map(m => m.trackId)).size).toBe(17);
  library.maps.forEach(m => {
    expect(gpsToRegisteredMap(m.origin, m).x).toEqual(expect.any(Number));
    expect(m.transform.a ** 2 + m.transform.b ** 2).toBeGreaterThan(0);
    expect(m.assets.active).toBe(`/track-maps/dz-replay/${m.trackId}/active.svg`);
    if (m.fit.p95Meters !== null) expect(m.fit.p95Meters).toBeGreaterThan(0);
  });
});
