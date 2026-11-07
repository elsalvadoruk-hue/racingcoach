import { LAST_SESSION_KEY, readDesktopLaps, readDesktopReferenceSource } from './desktopSelection';

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

test('only a deliberate personal source or legacy manual lap suppresses automatic Garage61', () => {
  const stored = { filename: 'A.ibt', reference_mode: 'LOCAL', reference_selection: 'AUTO', external_reference_files: [] };
  const storage = { getItem: () => JSON.stringify(stored) };
  expect(readDesktopReferenceSource('A.ibt', storage)).toBeNull();
  stored.reference_source = 'LOCAL';
  expect(readDesktopReferenceSource('A.ibt', storage)).toBe('LOCAL');
  expect(readDesktopReferenceSource('B.ibt', storage)).toBeNull();
  delete stored.reference_source; stored.reference_selection = 'MANUAL';
  expect(readDesktopReferenceSource('A.ibt', storage)).toBe('LOCAL');
  stored.external_reference_files = ['one.csv'];
  expect(readDesktopReferenceSource('A.ibt', storage)).toBeNull();
  expect(readDesktopReferenceSource('A.ibt', { getItem: () => '{bad' })).toBeNull();
});
