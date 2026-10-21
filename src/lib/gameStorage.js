// Legacy keys remain available as the initial value for each game.
export const GAMES = ['iracing', 'lmu'];
export const gameKey = (base, game) => `${base}.${game === 'lmu' ? 'lmu' : 'iracing'}`;
export function readGameItem(base, game) {
  try { return localStorage.getItem(gameKey(base, game)) ?? localStorage.getItem(base); }
  catch { return null; }
}
export function writeGameItem(base, game, value) {
  try { localStorage.setItem(gameKey(base, game), value); } catch { /* Preferences remain usable in memory. */ }
}
export function removeGameItem(base, game) {
  try { localStorage.removeItem(gameKey(base, game)); } catch { /* Storage may be unavailable. */ }
}
