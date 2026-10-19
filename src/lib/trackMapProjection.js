import library from '../data/registeredTrackMaps.json';
import { gpsToMercator } from './geographicMap';

const finite = Number.isFinite;
const token = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
const validPoints = path => (Array.isArray(path) ? path : []).map((point, sourceIndex) => ({ ...point, sourceIndex }))
  .filter(point => finite(point.lat) && finite(point.lon) && Math.abs(point.lat) <= 90 && Math.abs(point.lon) <= 180 && finite(point.distance_pct));

export function findTrackMap(identity, maps) {
  const track = identity || {};
  const id = String(track.track_platform_id || '').trim();
  // A known layout ID always wins, including when that layout is unsupported.
  if (id) return maps.find(map => String(map.trackId) === id) || null;
  const path = token(track.track_path);
  if (path) {
    const found = maps.find(map => token(map.sourceUrl.split('/').pop().replace(/^\d+-/, '')) === path);
    if (found) return found;
  }
  const name = token(track.track_name);
  const config = token(track.track_config);
  return maps.find(map => name && name === token(map.name) && config === token(map.layout)) || null;
}

export function findRegisteredTrackMap(identity, maps = library.maps) {
  return findTrackMap(identity, maps);
}

export function gpsToRegisteredMap(point, map) {
  const { origin, transform: { a, b, tx, ty } } = map;
  const east = (point.lon - origin.lon) * 111320 * Math.cos(origin.lat * Math.PI / 180) - tx;
  const north = (point.lat - origin.lat) * 110540 - ty;
  const denominator = a * a + b * b;
  // The registration transforms SVG (y down) to geographic metres (north up).
  return { x: (a * east + b * north) / denominator, y: (b * east - a * north) / denominator };
}

const projectPath = (path, project) => {
  const valid = validPoints(path);
  return valid.map((point, index) => ({ ...point, ...project(point),
    breakBefore: index > 0 && point.sourceIndex > valid[index - 1].sourceIndex + 1,
  }));
};

function registrationUsable(map, frame) {
  const values = [map?.origin?.lat, map?.origin?.lon, ...['a', 'b', 'tx', 'ty'].map(key => map?.transform?.[key]), ...(map?.viewBox || [])];
  if (values.length !== 10 || !values.every(finite) || map.viewBox[2] <= 0 || map.viewBox[3] <= 0
      || map.transform.a ** 2 + map.transform.b ** 2 < 1e-12) return false;
  const [x, y, width, height] = map.viewBox;
  const within = frame.filter(point => {
    const p = gpsToRegisteredMap(point, map);
    return p.x >= x - width * .05 && p.x <= x + width * 1.05 && p.y >= y - height * .05 && p.y <= y + height * 1.05;
  });
  // Reject clearly wrong coordinate frames. This is a location check, not a
  // measurement of road-boundary accuracy or an independent fit of either lap.
  return within.length / frame.length >= .9;
}

export function projectComparisonPaths(pathData, referencePath = [], framePath = pathData, registeredMap = null) {
  const frame = validPoints(framePath);
  if (frame.length < 2) return { target: [], reference: [], map: null, mapStatus: 'no-gps' };
  if (registeredMap && registrationUsable(registeredMap, frame)) {
    const project = point => gpsToRegisteredMap(point, registeredMap);
    return { target: projectPath(pathData, project), reference: projectPath(referencePath, project),
      map: registeredMap, mapStatus: 'registered' };
  }
  const metersPerUnit = Math.cos(frame.reduce((sum, point) => sum + point.lat, 0) / frame.length * Math.PI / 180);
  return { target: projectPath(pathData, gpsToMercator), reference: projectPath(referencePath, gpsToMercator), map: null,
    geographic: true, metersPerUnit, mapStatus: registeredMap ? 'gps-mismatch' : 'unavailable' };
}
