// Unknown metadata stays selectable until the recording has been inspected.
// LMU measurement eligibility is separate from the game's official validity.
export function isSelectableRecording(file) {
  if (!file) return true;
  if (file.analyzable === false || file.selectable === false) return false;
  if ([file.detected_laps, file.lap_count, file.complete_laps].some(value => Number.isFinite(value) && value <= 0)) return false;
  const usableLaps = Number.isFinite(file.measurement_laps) ? file.measurement_laps : file.valid_laps;
  return !Number.isFinite(usableLaps) || usableLaps > 0;
}
