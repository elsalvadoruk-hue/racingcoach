import React, { useMemo, useState } from 'react';
import { Button } from './ui/button';
import { comparisonColors } from '../chartTheme';

// How much each corner varies from lap to lap (RaceStudio 3's box plots), and the target lap against the
// corner's three fastest passes (its "fastest N sectors"), over the laps picked with the lap filter.
const quantile = (sorted, q) => {
  const at = (sorted.length - 1) * q;
  const low = Math.floor(at);
  return sorted[low] + (sorted[Math.min(low + 1, sorted.length - 1)] - sorted[low]) * (at - low);
};
const signed = (value) => (Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(3)} s` : '--');

export function summarize(passes, allowed, targetLap) {
  const kept = passes.filter((item) => !allowed || allowed.has(item.lap));
  if (kept.length < 3) return null;
  const times = kept.map((item) => item.time_s).sort((a, b) => a - b);
  const q1 = quantile(times, 0.25);
  const q3 = quantile(times, 0.75);
  const iqr = q3 - q1;
  const inside = times.filter((time) => time >= q1 - 1.5 * iqr && time <= q3 + 1.5 * iqr);
  const best3 = quantile(times.slice(0, 3), 0.5);
  const target = kept.find((item) => item.lap === Number(targetLap));
  return {
    count: times.length, min: times[0], q1, median: quantile(times, 0.5), q3, iqr,
    whiskerLow: inside[0], whiskerHigh: inside[inside.length - 1],
    outliers: times.filter((time) => time < q1 - 1.5 * iqr || time > q3 + 1.5 * iqr),
    best3, target: target ? target.time_s : null, targetVsBest3: target ? target.time_s - best3 : null,
  };
}

function Box({ stats, scale }) {
  const x = (time) => Math.min(100, ((time - stats.min) / scale) * 100);
  return (
    <svg viewBox="0 0 100 14" preserveAspectRatio="none" className="h-4 w-full" aria-hidden="true">
      <line x1={x(stats.whiskerLow)} x2={x(stats.whiskerHigh)} y1="7" y2="7" stroke={comparisonColors().muted} strokeWidth="0.6" vectorEffect="non-scaling-stroke" />
      <rect x={x(stats.q1)} y="2" width={Math.max(0.6, x(stats.q3) - x(stats.q1))} height="10" fill={comparisonColors().target} fillOpacity={.25} stroke={comparisonColors().target} strokeWidth="1" vectorEffect="non-scaling-stroke" />
      <line x1={x(stats.median)} x2={x(stats.median)} y1="2" y2="12" stroke={comparisonColors().target} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      {stats.outliers.map((time, index) => <circle key={index} cx={x(time)} cy="7" r="1.2" fill={comparisonColors().muted} />)}
      {Number.isFinite(stats.target) && <circle cx={x(stats.target)} cy="7" r="2" fill={comparisonColors().reference} />}
    </svg>
  );
}

export default function CornerConsistency({ consistency, allowedLaps, targetLabel, onCorner }) {
  const [order, setOrder] = useState('spread');
  const rows = useMemo(() => (consistency?.corners || [])
    .map((corner) => ({ corner, stats: summarize(corner.passes, allowedLaps, consistency.target_lap) }))
    .filter((row) => row.stats), [consistency, allowedLaps]);
  if (!consistency?.available) return <p className="text-sm text-muted-foreground">유효한 랩이 3개 이상 있어야 코너별 일관성을 볼 수 있습니다.</p>;
  if (!rows.length) return <p className="text-sm text-muted-foreground">고른 랩이 3개보다 적습니다. 랩 고르기 범위를 넓히세요.</p>;
  const sorted = order === 'spread' ? [...rows].sort((a, b) => b.stats.iqr - a.stats.iqr) : rows;
  const scale = Math.max(0.2, Math.min(2, Math.max(...rows.map((row) => row.stats.whiskerHigh - row.stats.min))));
  return (
    <div className="flex flex-col gap-3" data-testid="corner-consistency">
      <p className="text-sm text-muted-foreground">
        코너마다 고른 랩들의 통과 시간 분포입니다. 상자는 가운데 절반의 랩(사분위), 세로선은 중앙값, 회색 점은 크게 벗어난 랩, 주황 점은 대상 랩 {targetLabel ?? consistency.target_lap ?? '--'}입니다.
        가로축은 그 코너의 가장 빠른 통과에서 늦은 정도(최대 {scale.toFixed(2)}초)입니다. "흔들림"이 크면 매번 다르게 달리는 코너, 작은데 "빠른 3번 대비"가 크면 이번 랩만 느렸던 코너입니다.
      </p>
      <div className="flex gap-2">
        <Button variant="outline" type="button" aria-pressed={order === 'spread'} onClick={() => setOrder('spread')} className="border px-3 py-1 text-xs">흔들림 큰 순</Button>
        <Button variant="outline" type="button" aria-pressed={order === 'track'} onClick={() => setOrder('track')} className="border px-3 py-1 text-xs">트랙 순서</Button>
      </div>
      <div className="grid grid-cols-[minmax(6rem,9rem)_1fr_5.5rem_7.5rem] items-center gap-x-3 gap-y-2 text-sm">
        <span className="text-xs text-muted-foreground">구간</span>
        <span className="text-xs text-muted-foreground">분포</span>
        <span className="text-right text-xs text-muted-foreground">흔들림</span>
        <span className="text-right text-xs text-muted-foreground">대상 랩 · 빠른 3번 대비</span>
        {sorted.map(({ corner, stats }) => (
          <React.Fragment key={corner.id}>
            {onCorner ? <Button variant="ghost" className="truncate" data-testid={`consistency-${corner.id}`} onClick={() => onCorner(corner.id)}>{corner.label || corner.id}</Button> : <span className="truncate text-foreground" data-testid={`consistency-${corner.id}`}>{corner.label || corner.id}</span>}
            <Box stats={stats} scale={scale} />
            <span className="text-right font-mono text-foreground">{stats.iqr.toFixed(3)} s</span>
            <span className={`text-right font-mono ${stats.targetVsBest3 > 0.05 ? 'text-destructive' : 'text-muted-foreground'}`}>{signed(stats.targetVsBest3)}</span>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
