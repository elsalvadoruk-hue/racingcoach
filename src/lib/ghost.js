// Ghost replay on the map: where each lap's car was at the same time since its lap start.
// The map points carry time_s (seconds since the start line, from the analysis grid). A lap's timeline is
// its points in order of time, kept only where time keeps rising; positions between points are
// interpolated, and nothing is drawn outside what was observed.
const finite = Number.isFinite;

export function timeline(points) {
  const line = [];
  for (const point of Array.isArray(points) ? points : []) {
    if (!finite(point.time_s) || !finite(point.x) || !finite(point.y) || !finite(point.distance_pct)) continue;
    if (line.length && point.time_s <= line[line.length - 1].t) continue;
    line.push({ t: point.time_s, x: point.x, y: point.y, pct: point.distance_pct, gapBefore: Boolean(point.breakBefore) });
  }
  return line;
}

function search(line, key, value) {
  let lo = 0, hi = line.length - 1;
  if (hi < 1 || value < line[0][key] || value > line[hi][key]) return null;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (line[mid][key] <= value) lo = mid; else hi = mid;
  }
  const a = line[lo], b = line[hi];
  const f = b[key] === a[key] ? 0 : (value - a[key]) / (b[key] - a[key]);
  return { a, b, f };
}

// Position at a time since the lap start (null before the first or after the last observed point).
export function positionAt(line, t) {
  const found = search(line, 't', t);
  if (!found) return null;
  const { a, b, f } = found;
  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, pct: a.pct + (b.pct - a.pct) * f };
}

// Time since the lap start when this lap passed a lap position (distance falls as time rises nowhere here).
export function timeAtPct(line, pct) {
  const found = search(line, 'pct', pct);
  if (!found) return null;
  const { a, b, f } = found;
  return a.t + (b.t - a.t) * f;
}

// The time span to play: the whole lap, or from the first of the two cars entering the picked range to
// the last one leaving it.
export function replaySpan(target, reference, range) {
  if (target.length < 2 || reference.length < 2) return null;
  if (!range) return [Math.min(target[0].t, reference[0].t), Math.max(target[target.length - 1].t, reference[reference.length - 1].t)];
  const starts = [timeAtPct(target, range[0]), timeAtPct(reference, range[0])].filter(finite);
  const ends = [timeAtPct(target, range[1]), timeAtPct(reference, range[1])].filter(finite);
  if (!starts.length || !ends.length) return null;
  const span = [Math.min(...starts), Math.max(...ends)];
  return span[1] > span[0] ? span : null;
}

// Seconds the player is behind (+) or ahead (−) of the reference at the player's current lap position.
export function gapAt(target, reference, t) {
  const here = positionAt(target, t);
  if (!here) return null;
  const theirs = timeAtPct(reference, here.pct);
  return finite(theirs) ? t - theirs : null;
}
