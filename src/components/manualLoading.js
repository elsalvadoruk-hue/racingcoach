const MANUAL_ON_OPEN_KEY = 'coach.manual-on-open.v1';
const loaded = new Map();

export function manualOnOpen() {
  try { return localStorage.getItem(MANUAL_ON_OPEN_KEY) !== 'false'; }
  catch { return true; }
}

export function saveManualOnOpen(enabled) {
  localStorage.setItem(MANUAL_ON_OPEN_KEY, String(enabled));
}

export function loadedManual(game) {
  return loaded.get(game);
}

export async function loadManual(game) {
  if (!loaded.has(game)) {
    const page = game === 'lmu' ? await import('./LmuManual') : await import('./IracingManual');
    loaded.set(game, page.default);
  }
  return loaded.get(game);
}
