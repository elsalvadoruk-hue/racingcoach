import { isSelectableRecording } from './recordingFiles';

test.each([
  [{ detected_laps: 0, valid_laps: 0 }, false],
  [{ lap_count: 0 }, false],
  [{ detected_laps: 4, valid_laps: 0, analyzable: true, status: 'COACHING_ONLY' }, false],
  [{ complete_laps: 0 }, false],
  [{ detected_laps: 4, valid_laps: 1 }, true],
  [{ detected_laps: null, valid_laps: null, metadata_pending: true }, true],
  [{}, true],
  [{ measurement_laps: 2, valid_laps: null }, true],
  [{ measurement_laps: 0, valid_laps: null }, false],
  [{ measurement_laps: 2, valid_laps: 0 }, true],
  [{ valid_laps: 2, analyzable: false }, false],
  [undefined, true],
])('selectable recording %j is %s', (file, expected) => {
  expect(isSelectableRecording(file)).toBe(expected);
});
