import React from 'react';
import { COMPARISON_COLORS } from '../chartTheme';
import { summarize } from './CornerConsistency';

// A and B lap times side by side (RaceStudio 3's box plots across sessions): is the change bigger than the
// usual lap-to-lap spread?
const format = (seconds) => {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(3).padStart(6, '0')}`;
};

export function verdict(a, b) {
  if (!a || !b) return null;
  const change = b.median - a.median;
  const spread = Math.max(a.iqr, b.iqr);
  const size = Math.abs(change) > spread ? 'BIGGER' : Math.abs(change) > spread / 2 ? 'SIMILAR' : 'SMALLER';
  return { change, spread, size };
}

export default function LapDistribution({ distribution }) {
  const toPasses = (laps) => (laps || []).map((item) => ({ lap: item.lap, time_s: item.time_s }));
  const a = summarize(toPasses(distribution?.baseline), null, null);
  const b = summarize(toPasses(distribution?.candidate), null, null);
  if (!a || !b) return <p className="text-sm text-muted-foreground">A와 B 모두 유효한 랩이 3개 이상 있어야 분포를 비교할 수 있습니다.</p>;
  const all = [...distribution.baseline, ...distribution.candidate].map((item) => item.time_s);
  const low = Math.min(...all);
  const high = Math.max(...all);
  const span = Math.max(0.5, high - low);
  const x = (time) => 6 + ((time - low) / span) * 88;
  const result = verdict(a, b);
  const rows = [['A · 변경 전', a, distribution.baseline, COMPARISON_COLORS.reference], ['B · 변경 후', b, distribution.candidate, COMPARISON_COLORS.target]];
  const message = {
    BIGGER: '차이가 평소 랩 간 흔들림보다 큽니다. 셋업 효과일 가능성이 높습니다.',
    SIMILAR: '차이가 평소 랩 간 흔들림과 비슷합니다. 랩을 더 모으면 판단이 분명해집니다.',
    SMALLER: '차이가 평소 랩 간 흔들림보다 작습니다. 이 랩들만으로는 차이라고 보기 어렵습니다.',
  }[result.size];
  return (
    <div className="flex flex-col gap-3" data-testid="lap-distribution">
      <p className="text-sm text-muted-foreground">A와 B의 모든 유효 랩입니다. 점이 각 랩, 상자가 가운데 절반의 랩(사분위), 세로선이 중앙값입니다.</p>
      <svg viewBox="0 0 100 34" className="h-auto w-full" role="img" aria-label="A와 B 랩타임 분포">
        {rows.map(([label, stats, laps, color], index) => {
          const y = 9 + index * 16;
          return (
            <g key={label}>
              <line x1={x(stats.whiskerLow)} x2={x(stats.whiskerHigh)} y1={y} y2={y} stroke={color} strokeWidth="0.3" />
              <rect x={x(stats.q1)} y={y - 4} width={Math.max(0.4, x(stats.q3) - x(stats.q1))} height="8" fill="none" stroke={color} strokeWidth="0.4" />
              <line x1={x(stats.median)} x2={x(stats.median)} y1={y - 4} y2={y + 4} stroke={color} strokeWidth="0.8" />
              {laps.map((item) => <circle key={item.lap} cx={x(item.time_s)} cy={y} r="0.7" fill={color} opacity="0.7" />)}
            </g>
          );
        })}
      </svg>
      <div className="grid gap-1 font-mono text-sm text-foreground sm:grid-cols-2">
        {rows.map(([label, stats, laps, color]) => (
          <div key={label}><span style={{ color }} className="font-sans font-semibold">{label}</span> 중앙값 {format(stats.median)} · 흔들림(IQR) {stats.iqr.toFixed(3)} s · {laps.length}랩</div>
        ))}
      </div>
      <p className="text-sm text-foreground" data-testid="distribution-verdict">
        B의 중앙값이 A보다 {Math.abs(result.change).toFixed(3)}초 {result.change < 0 ? '빠릅니다' : '느립니다'}. {message}
      </p>
    </div>
  );
}
