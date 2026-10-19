// Colouring the driven line on the map by where time is lost or gained against the reference.
// The comparison trace carries the cumulative time difference (delta_s, this lap − reference) at each
// distance; its slope over a short stretch is the time lost (+) or gained (−) right there, in seconds per
// 100 m. Where either end of that stretch has no measured delta the line keeps its plain colour.
const finite = Number.isFinite;

export const LOSS_WINDOW_M = 25;          // half the stretch the slope is taken over
const NEUTRAL = [229, 231, 235];
const LOST = [239, 68, 68];
const GAINED = [34, 197, 94];
const STEPS = 10;                         // colours per side, so neighbouring segments merge into one line

function deltaAt(rows, pct) {
  let lo = 0, hi = rows.length - 1;
  if (hi < 0 || pct < rows[0].distance_pct || pct > rows[hi].distance_pct) return null;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].distance_pct <= pct) lo = mid; else hi = mid;
  }
  const a = rows[lo], b = rows[hi];
  if (b.distance_pct === a.distance_pct) return a.delta;
  return a.delta + (b.delta - a.delta) * (pct - a.distance_pct) / (b.distance_pct - a.distance_pct);
}

// rows: [{distance_pct, delta}] (prepareComparisonTrace); returns [{distance_pct, rate}] in s per 100 m.
export function lossRates(rows, trackLengthM, halfWindowM = LOSS_WINDOW_M) {
  if (!finite(trackLengthM) || trackLengthM <= 0) return [];
  const measured = (Array.isArray(rows) ? rows : []).filter((row) => finite(row.distance_pct) && finite(row.delta))
    .sort((a, b) => a.distance_pct - b.distance_pct);
  if (measured.length < 3) return [];
  const half = halfWindowM / trackLengthM;
  const maxGap = Math.max(4 * half, 60 / trackLengthM);   // never draw a slope across missing data
  return measured.map((row, index) => {
    const before = measured[index - 1], after = measured[index + 1];
    const gapped = (before && row.distance_pct - before.distance_pct > maxGap) || (after && after.distance_pct - row.distance_pct > maxGap);
    const start = deltaAt(measured, row.distance_pct - half), end = deltaAt(measured, row.distance_pct + half);
    const rate = gapped || start === null || end === null ? null : (end - start) / (2 * halfWindowM) * 100;
    return { distance_pct: row.distance_pct, rate };
  });
}

export function rateAt(rates, pct) {
  if (!rates.length || !finite(pct)) return null;
  let lo = 0, hi = rates.length - 1;
  if (pct < rates[0].distance_pct || pct > rates[hi].distance_pct) return null;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (rates[mid].distance_pct <= pct) lo = mid; else hi = mid;
  }
  const a = rates[lo], b = rates[hi];
  if (!finite(a.rate) || !finite(b.rate)) return null;
  return b.distance_pct === a.distance_pct ? a.rate : a.rate + (b.rate - a.rate) * (pct - a.distance_pct) / (b.distance_pct - a.distance_pct);
}

// The rate at which the colour is full: the lap's 90th percentile, never under 0.02 s per 100 m so a lap
// with almost no difference stays mostly neutral.
export function lossScale(rates) {
  const sizes = rates.map((item) => Math.abs(item.rate)).filter(finite).sort((a, b) => a - b);
  if (!sizes.length) return null;
  return Math.max(.02, sizes[Math.min(sizes.length - 1, Math.floor(sizes.length * .9))]);
}

const mix = (from, to, t) => `rgb(${from.map((value, i) => Math.round(value + (to[i] - value) * t)).join(',')})`;

export function lossColour(rate, scale) {
  if (!finite(rate) || !finite(scale) || scale <= 0) return null;
  const t = Math.round(Math.max(-1, Math.min(1, rate / scale)) * STEPS) / STEPS;
  return t >= 0 ? mix(NEUTRAL, LOST, t) : mix(NEUTRAL, GAINED, -t);
}

// Map points cut into runs of one colour (null = plain), each run sharing its end point with the next.
export function colourRuns(points, colourAt) {
  const runs = [];
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1], b = points[i];
    if (b.breakBefore) continue;
    const colour = colourAt((a.distance_pct + b.distance_pct) / 2);
    const last = runs[runs.length - 1];
    if (last && last.colour === colour && last.points[last.points.length - 1] === a) last.points.push(b);
    else runs.push({ colour, points: [a, b] });
  }
  return runs;
}
