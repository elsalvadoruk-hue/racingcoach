const finite = Number.isFinite;

export function controlWindow(signal, start = 0, end = 1) {
  const observed = (signal?.coverage || []).map(([a, b]) => [Math.max(start, a), Math.min(end, b)]).filter(([a, b]) => b > a).sort((a, b) => a[0] - b[0]);
  let coverage = 0, coveredTo = start;
  for (const [a, b] of observed) { coverage += Math.max(0, b - Math.max(a, coveredTo)); coveredTo = Math.max(coveredTo, b); }
  const intervals = (signal?.events || []).filter(([a, b]) => a === b ? a >= start && a <= end : b > start && a < end).map(([a, b, seconds]) => {
    const from = Math.max(start, a), to = Math.min(end, b);
    return [from, to, b > a ? seconds * (to - from) / (b - a) : seconds];
  });
  const runs = [];
  for (const [a, b] of [...intervals].sort((a, b) => a[0] - b[0])) {
    const previous = runs.at(-1);
    if (previous && a <= previous[1] + 1e-7) previous[1] = Math.max(previous[1], b);
    else runs.push([a, b]);
  }
  return { available: Boolean(signal?.available), observed: coverage > 0,
    complete: coverage >= (end - start) * .98, coverage, runs, pointOnly: coverage === 0 && runs.length > 0,
    duration: intervals.reduce((sum, item) => sum + (finite(item[2]) ? item[2] : 0), 0),
    at: runs[0]?.[0], reason: signal?.reason };
}

export function controlStatus(signal, start, end) {
  const window = controlWindow(signal, start, end);
  if (!window.available) return window.reason === 'AGGREGATE_REFERENCE' ? '종합 기준: 단일 작동 신호 없음' : window.reason === 'NO_OBSERVATIONS' ? '작동 신호 관측 없음' : '작동 신호 없음';
  if (window.pointOnly) return '개입 관측 · 시간 확인 필요';
  if (!window.observed) return '선택 범위 관측 없음';
  if (!window.runs.length) return window.complete ? '개입 없음' : '관측 부분 개입 없음 · 일부 누락';
  return `${window.duration.toFixed(2)}초${window.complete ? '' : ' · 일부 누락'}`;
}
