export const LAST_SESSION_KEY = 'iracing.desktop.last-session.v1';

export function readDesktopLaps(filename, references, storage = localStorage) {
  try {
    const saved = JSON.parse(storage.getItem(LAST_SESSION_KEY) || 'null');
    if (saved?.filename !== filename || !Number.isInteger(saved.target_lap) || saved.target_lap < 0) return null;
    if (saved.reference_lap != null && (!Number.isInteger(saved.reference_lap) || saved.reference_lap < 0)) return null;
    if (JSON.stringify(saved.external_reference_files || []) !== JSON.stringify(references || [])) return null;
    return { targetLapOverride: saved.target_lap, referenceLapOverride: saved.reference_selection === 'AUTO' ? null : saved.reference_lap ?? null,
      ...(saved.reference_filename ? { referenceFilenameOverride: saved.reference_filename } : {}),
      ...(saved.reference_selection ? { referenceSelectionOverride: saved.reference_selection } : {}) };
  } catch { return null; }
}
