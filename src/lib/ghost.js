// Ghost replay uses measured passage times. Selected segments have independent entry clocks.
// The map points carry time_s (seconds since the start line, from the analysis grid). A lap's timeline is
// its points in order of time, kept only where time keeps rising; positions between points are
// interpolated, and nothing is drawn outside what was observed.
const finite = Number.isFinite;

export function timeline(points) {
  const line = [];
  let missing = false;
  for (const point of Array.isArray(points) ? points : []) {
    if (!finite(point.time_s) || !finite(point.x) || !finite(point.y) || !finite(point.distance_pct)
      || (line.length && (point.time_s <= line[line.length - 1].t || point.distance_pct < line[line.length - 1].pct))) { missing = true; continue; }
    line.push({ t: point.time_s, x: point.x, y: point.y, pct: point.distance_pct, gapBefore: Boolean(point.breakBefore || missing) });
    missing = false;
  }
  return line;
}

function search(line, key, value) {
  let lo = 0, hi = line.length - 1;
  if (hi < 1 || !finite(value) || value < line[0][key] || value > line[hi][key]) return null;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (line[mid][key] <= value) lo = mid; else hi = mid;
  }
  const a = line[lo], b = line[hi];
  const f = b[key] === a[key] ? 0 : (value - a[key]) / (b[key] - a[key]);
  if (b.gapBefore && f > 0 && f < 1) return null;
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

// Both cars enter the same physical segment at elapsed zero. Keep their actual durations;
// the faster car waits at the end. Whole-lap playback retains the lap's observed clock.
export function replayPair(target, reference, range) {
  if (target.length < 2 || reference.length < 2) return null;
  if (!range) return { target, reference, span: replaySpan(target, reference, null) };
  if (!range.every(finite) || range[1] <= range[0]) return null;
  const start = Math.max(range[0], target[0].pct, reference[0].pct);
  const end = Math.min(range[1], target[target.length - 1].pct, reference[reference.length - 1].pct);
  if (end <= start) return null;
  const clip = line => {
    const entry = timeAtPct(line, start), exit = timeAtPct(line, end);
    if (!finite(entry) || !finite(exit) || exit <= entry) return null;
    const between = line.filter(p => p.pct > start && p.pct < end);
    if (between.some(p => p.gapBefore) || line.some(p => p.gapBefore && p.pct > start && p.pct <= end)) return null;
    return [{ ...positionAt(line, entry), t: 0 },
      ...between.map(p => ({ ...p, t: p.t - entry })),
      { ...positionAt(line, exit), t: exit - entry }];
  };
  const mine = clip(target), theirs = clip(reference);
  return mine && theirs ? { target: mine, reference: theirs,
    span: [0, Math.max(mine[mine.length - 1].t, theirs[theirs.length - 1].t)] } : null;
}

export function heldPositionAt(line, t) {
  return line.length < 2 || !finite(t) ? null
    : positionAt(line, Math.max(line[0].t, Math.min(line[line.length - 1].t, t)));
}

// Seconds the player is behind (+) or ahead (−) of the reference at the player's current lap position.
export function gapAt(target, reference, t) {
  const elapsed = target.length ? Math.min(t, target[target.length - 1].t) : t;
  const here = positionAt(target, elapsed);
  if (!here) return null;
  const theirs = timeAtPct(reference, here.pct);
  return finite(theirs) ? elapsed - theirs : null;
}
