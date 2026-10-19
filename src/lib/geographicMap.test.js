import { gpsToMercator, imageryTiles, WORLD_HALF } from './geographicMap';

test('projects equator and known WGS84 coordinates into the standard north-up tile grid', () => {
  const origin = gpsToMercator({ lat: 0, lon: 0 });
  expect(origin.x).toBe(0);
  expect(origin.y).toBeCloseTo(0);
  const east = gpsToMercator({ lat: 0, lon: 180 });
  expect(east.x).toBeCloseTo(WORLD_HALF);
  const north = gpsToMercator({ lat: 45, lon: 90 });
  expect(north.x).toBeCloseTo(10018754.17139462);
  expect(north.y).toBeCloseTo(-5621521.486192066);
});

test('only requests bounded tiles covering the camera and never shifts a racing line to a photo', () => {
  const point = gpsToMercator({ lat: 50.3334443, lon: 6.9392429 });
  const view = { x: point.x - 400, y: point.y - 200, width: 800, height: 400 };
  const tiles = imageryTiles(view, { width: 1200, height: 600 });
  expect(tiles.length).toBeGreaterThan(0);
  expect(tiles.length).toBeLessThanOrEqual(40);
  expect(tiles.some(tile => point.x >= tile.x && point.x <= tile.x + tile.width && point.y >= tile.y && point.y <= tile.y + tile.height)).toBe(true);
  expect(tiles.every(tile => /\/tile\/\d+\/\d+\/\d+$/.test(tile.url))).toBe(true);
  expect(tiles.every(tile => tile.width === tile.height)).toBe(true);
  // Drawn near an origin by the track, the same tiles come back moved by it, so nothing is shifted.
  const origin = { x: Math.round(point.x / 1000) * 1000, y: Math.round(point.y / 1000) * 1000 };
  const local = imageryTiles({ ...view, x: view.x - origin.x, y: view.y - origin.y }, { width: 1200, height: 600 }, origin);
  expect(local.map(tile => tile.key)).toEqual(tiles.map(tile => tile.key));
  local.forEach((tile, index) => { expect(tile.x + origin.x).toBeCloseTo(tiles[index].x); expect(tile.y + origin.y).toBeCloseTo(tiles[index].y); });
});
