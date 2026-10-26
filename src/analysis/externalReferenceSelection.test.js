import { retainPrimary, selectPrimary } from './externalReferenceSelection';
import { analysisContextKey } from './analysisShared';

test('adding supporting laps preserves the primary regardless of display order', () => {
  expect(retainPrimary(['b', 'a'], ['a', 'b', 'c'])).toEqual(['b', 'a', 'c']);
  expect(selectPrimary(['b', 'a', 'c'], 'c')).toEqual(['c', 'b', 'a']);
});

test('changing the primary invalidates analysis while reordering support does not', () => {
  const key = references => analysisContextKey({ filename: 'lap.ibt', references });
  expect(key(['b', 'a', 'c'])).toBe(key(['b', 'c', 'a']));
  expect(key(['b', 'a', 'c'])).not.toBe(key(['a', 'b', 'c']));
});
