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

test('the geographic frame is drawn near the track, so the line keeps centimetres in 32-bit graphics', () => {
  // Long Beach: world Mercator x is about -13.16 million metres, which a 32-bit float holds only to 1 m.
  const path = [0, 1, 2].map(step => ({ lat: 33.7643, lon: -118.1843 + step * 1e-6, distance_pct: step / 2 }));
  const result = projectComparisonPaths(path);
  expect(Math.abs(result.target[0].x)).toBeLessThan(1000);
  expect(Math.abs(result.target[0].y)).toBeLessThan(1000);
  const step = result.target[1].x - result.target[0].x;
  expect(Math.fround(result.target[1].x) - Math.fround(result.target[0].x)).toBeCloseTo(step, 2);   // 9 cm survives
  expect(result.target[0].x + result.origin.x).toBeCloseTo(-118.1843 * Math.PI / 180 * 6378137, 3);
});

test('all imported maps have a usable registration and matching layout asset paths', () => {
  const dzMaps = library.maps.filter(m => m.assets.active.includes('/dz-replay/'));
  expect(dzMaps).toHaveLength(17);
  // Maps traced from aerial photos are not used in the app (user decision 2026-10-17); only DZ replay's.
  expect(library.maps.filter(m => m.provenance.geometry === 'traced')).toHaveLength(0);
  expect(library.maps).toHaveLength(17);
  expect(new Set(library.maps.map(m => m.trackId)).size).toBe(library.maps.length);
  library.maps.forEach(m => {
    expect(gpsToRegisteredMap(m.origin, m).x).toEqual(expect.any(Number));
    expect(m.transform.a ** 2 + m.transform.b ** 2).toBeGreaterThan(0);
    // DZ replay's maps, or an official drawing fitted to the driver's lap (scripts/register_track_map.py).
    // Only road edges traced from real imagery (DZ replay); estimated roads are not shown (user decision 2026-10-15).
    expect(m.assets.active).toBe(`/track-maps/${m.provenance.geometry === 'traced' ? 'traced' : 'dz-replay'}/${m.trackId}/active.svg`);
    if (m.provenance.geometry === 'traced') {
      expect(m.provenance.guideLapCount).toBeGreaterThanOrEqual(3);
      expect(m.provenance.manualRatio).toBeGreaterThanOrEqual(0);
      expect(m.provenance.manualRatio).toBeLessThanOrEqual(1);
      expect(m.provenance.imagery.length).toBeGreaterThan(0);
      m.provenance.imagery.forEach(source => {
        expect(source.redistribution).toBe(true);
        expect(source.derivatives).toBe(true);
        expect(source.documentationUrl).toMatch(/^https:\/\//);
      });
    }
    if (m.fit.p95Meters !== null) expect(m.fit.p95Meters).toBeGreaterThan(0);
  });
});
