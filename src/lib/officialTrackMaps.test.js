import { findOfficialTrackMap, officialTrackMapCount } from './officialTrackMaps';
import library from '../data/officialTrackMaps.json';

test('all downloaded course diagrams have distinct layout IDs and a complete local layer set', () => {
  expect(officialTrackMapCount).toBe(431);
  expect(new Set(library.maps.map(map => map.trackId)).size).toBe(officialTrackMapCount);
  library.maps.forEach(map => {
    expect(map.gpsRegistered).toBe(false);
    expect(map.viewBox).toHaveLength(4);
    ['background', 'active', 'inactive', 'pitroad', 'start-finish', 'turns'].forEach(layer => {
      expect(map.assets[layer]).toBe(`/track-maps/official/${map.trackId}/${layer}.svg`);
    });
  });
});

test('identifies VLN and every newly discovered layout without substituting other configurations', () => {
  expect(findOfficialTrackMap({ track_platform_id: '262' }).displayName).toContain('VLN');
  expect(findOfficialTrackMap({ track_path: 'nurburgring combinedshortb' }).trackId).toBe(262);
  expect(findOfficialTrackMap({ track_platform_id: '999999', track_path: 'nurburgring combinedshortb' })).toBeNull();
  expect(findOfficialTrackMap(null)).toBeNull();
  library.maps.filter(map => map.discoveredAt).forEach(map => {
    expect(findOfficialTrackMap({ track_platform_id: String(map.trackId) })).toBe(map);
  });
});
