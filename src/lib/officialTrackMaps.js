import library from '../data/officialTrackMaps.json';
import { findTrackMap } from './trackMapProjection';

export const officialTrackMapCount = library.maps.length;
export function findOfficialTrackMap(identity) {
  return findTrackMap(identity, library.maps);
}
