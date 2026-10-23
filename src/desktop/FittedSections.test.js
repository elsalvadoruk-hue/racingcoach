import { fitSections } from './FittedSections';
import { measuredSections } from './SummaryAnalysis';

test('packs whole sections by priority, skips oversized sections and fills the remaining space', () => {
  const rows = [150, 600, 80, 70, 40, 30].map((height, key) => ({ key, height }));
  const lanes = fitSections(rows, 200, 2);
  expect(lanes).toEqual([[0, 4], [2, 3, 5]]);
  for (const lane of lanes) expect(lane.reduce((sum, key) => sum + rows[key].height, Math.max(0, lane.length - 1) * 10)).toBeLessThanOrEqual(200);
});

test('exact boundaries fit and a narrower one-column layout drops lower priorities without clipping', () => {
  expect(fitSections([{ key: 'a', height: 100 }, { key: 'b', height: 90 }], 200, 1)).toEqual([['a', 'b']]);
  expect(fitSections([{ key: 'a', height: 100 }, { key: 'b', height: 91 }], 200, 1)).toEqual([['a']]);
  expect(fitSections([{ key: 'a', height: NaN }], 0, 1)).toEqual([[]]);
});

test('extra measurements retain usable zero values and omit unknown or unusable channels', () => {
  expect(measuredSections({ target: {}, reference: {} })).toEqual([]);
  expect(measuredSections({ target: { measurement_usable: false, min_speed_kph: 80, inputs_usable: false, peak_brake: .9 }, reference: { min_speed_kph: 90 } })).toEqual([]);
  expect(measuredSections({ target: { peak_brake: 0 }, reference: {} })).toHaveLength(1);
});
