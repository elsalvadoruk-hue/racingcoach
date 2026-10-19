import { LAST_SESSION_KEY, readDesktopLaps } from './desktopSelection';

test('saved lap pairs restore only for the same file and references', () => {
  const storage = { getItem: jest.fn(() => JSON.stringify({ filename: 'A.ibt', target_lap: 4, reference_lap: 2,
    external_reference_files: ['a.csv'] })) };
  expect(readDesktopLaps('A.ibt', ['a.csv'], storage)).toEqual({ targetLapOverride: 4, referenceLapOverride: 2 });
  expect(storage.getItem).toHaveBeenCalledWith(LAST_SESSION_KEY);
  expect(readDesktopLaps('B.ibt', ['a.csv'], storage)).toBeNull();
  expect(readDesktopLaps('A.ibt', ['b.csv'], storage)).toBeNull();
});

test('damaged or blocked browser storage preserves automatic lap selection', () => {
  expect(readDesktopLaps('A.ibt', [], { getItem: () => '{bad' })).toBeNull();
  expect(readDesktopLaps('A.ibt', [], { getItem: () => { throw new Error('blocked'); } })).toBeNull();
  expect(readDesktopLaps('A.ibt', [], { getItem: () => JSON.stringify({ filename: 'A.ibt', target_lap: null }) })).toBeNull();
});
