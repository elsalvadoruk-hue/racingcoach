export const PLOT = { left: 48, right: 26, width: 1000, row: 28, top: 18 };
export const raceClock = seconds => {
  if (!Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.floor(seconds));
  return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
    : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
export const positionX = (time, range) => PLOT.left + (time - range[0]) / Math.max(.001, range[1] - range[0]) * (PLOT.width - PLOT.left - PLOT.right);
export const positionY = rank => PLOT.top + (rank - 1) * PLOT.row;
export function pointAt(points, time) {
  if (!points.length || time < points[0][0] || time > points[points.length - 1][0]) return null;
  let low = 0, high = points.length;
  while (low < high) { const mid = (low + high) >>> 1; if (points[mid][0] <= time) low = mid + 1; else high = mid; }
  return points[Math.max(0, low - 1)];
}
export function positionPath(points, metric, range) {
  // Step between observed samples. Nulls break the path; never invent a start/finish.
  let path = '', previous = null;
  for (const point of points) {
    const rank = point[metric];
    if (!Number.isFinite(rank)) { previous = null; continue; }
    const [time] = point;
    if (previous && time >= range[0] && previous[0] <= range[1]) {
      const from = Math.max(range[0], previous[0]), to = Math.min(range[1], time);
      if (to >= from) {
        const x0 = positionX(from, range).toFixed(2), x1 = positionX(to, range).toFixed(2);
        path += `M${x0},${positionY(previous[metric])}H${x1}`;
        if (time <= range[1]) path += `V${positionY(rank)}`;
      }
    }
    previous = point;
  }
  return path;
}
