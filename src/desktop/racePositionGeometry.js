export const PLOT = { left: 48, right: 26, width: 1000, row: 28, top: 18 };
export const raceClock = seconds => {
  if (!Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.floor(seconds));
  return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
    : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
export const positionX = (time, range) => PLOT.left + (time - range[0]) / Math.max(.001, range[1] - range[0]) * (PLOT.width - PLOT.left - PLOT.right);
export const positionY = rank => PLOT.top + (rank - 1) * PLOT.row;
export function positionMarkers(points, metric, range) {
  // Round caps on zero-length subpaths preserve every dot in one SVG element.
  return points.filter(p => Number.isFinite(p[metric]) && p[0] >= range[0] && p[0] <= range[1])
    .map(p => `M${positionX(p[0], range).toFixed(2)},${positionY(p[metric]).toFixed(2)}h0`).join('');
}
export function pointAt(points, time) {
  if (!points.length || time < points[0][0] || time > points[points.length - 1][0]) return null;
  let low = 0, high = points.length;
  while (low < high) { const mid = (low + high) >>> 1; if (points[mid][0] <= time) low = mid + 1; else high = mid; }
  const before = points[Math.max(0, low - 1)], after = points[low];
  if (before[0] === time) return before;
  if (!after || before[2] == null || after[2] == null) return null;
  return time - before[0] <= after[0] - time ? before : after;
}
export function rankAt(points, x, metric = 2) {
  if (!points.length || x < points[0][0] || x > points[points.length - 1][0]) return null;
  let low = 0, high = points.length;
  while (low < high) { const mid = (low + high) >>> 1; if (points[mid][0] <= x) low = mid + 1; else high = mid; }
  const before = points[Math.max(0, low - 1)], after = points[low];
  if (!Number.isFinite(before[metric])) return null;
  if (before[0] === x) return before[metric];
  if (!after || !Number.isFinite(after[metric])) return null;
  return before[metric] + (after[metric] - before[metric]) * (x - before[0]) / (after[0] - before[0]);
}
export function lapTicks(range) {
  const step = Math.max(1, Math.ceil((range[1] - range[0]) / 6)), ticks = [];
  for (let lap = Math.ceil(range[0] / step) * step; lap <= range[1]; lap += step) ticks.push(lap);
  return ticks;
}
export function positionPath(points, metric, range) {
  // Straight segments between actual checkpoints; clip geometry, never extend it.
  let path = '', previous = null, connected = false;
  for (const point of points) {
    const rank = point[metric];
    if (!Number.isFinite(rank)) { previous = null; connected = false; continue; }
    const [time] = point;
    if (previous && time > previous[0] && time >= range[0] && previous[0] <= range[1]) {
      const from = Math.max(range[0], previous[0]), to = Math.min(range[1], time);
      if (to >= from) {
        const slope = (rank - previous[metric]) / (time - previous[0]);
        const y0 = positionY(previous[metric] + slope * (from - previous[0]));
        const y1 = positionY(previous[metric] + slope * (to - previous[0]));
        if (!connected) path += `M${positionX(from, range).toFixed(2)},${y0.toFixed(2)}`;
        path += `L${positionX(to, range).toFixed(2)},${y1.toFixed(2)}`;
        connected = true;
      }
    }
    previous = point;
  }
  return path;
}
