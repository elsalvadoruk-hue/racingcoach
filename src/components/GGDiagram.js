import React from 'react';
import { comparisonColors } from '../chartTheme';

// G-G diagram (RaceStudio 3's scatter of lateral against longitudinal acceleration): right turns to the
// right, acceleration up, braking down. The line is the grip actually used in each direction.
const SIZE = 260;
const finite = (value) => typeof value === 'number' && Number.isFinite(value);

function envelopePath(data, toXY) {
  const step = data.bin_deg;
  const points = data.envelope_g
    .map((radius, index) => (finite(radius) ? toXY(radius, (index + 0.5) * step) : null))
    .filter(Boolean);
  return points.length > 2 ? `M${points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L')} Z` : null;
}

export default function GGDiagram({ current, baseline, candidate }) {
  const pair = Boolean(baseline || candidate);
  const runs = (pair ? [[baseline, comparisonColors().reference, 'A · 변경 전', '5 4'], [candidate, comparisonColors().target, 'B · 변경 후', null]]
    : [[current, comparisonColors().target, '이번 주행', null]]).filter(([data]) => data?.available);
  if (!runs.length) {
    const reason = [current, baseline, candidate].find((item) => item?.reason)?.reason;
    return <p className="text-sm text-muted-foreground">{reason === 'NO_ACCELERATION_CHANNELS' ? '이 주행 파일에는 가속도 기록이 없습니다.' : '유효한 랩이 없어 G-G 그림을 그리지 않았습니다.'}</p>;
  }
  const largest = Math.max(1, ...runs.flatMap(([data]) => data.envelope_g.filter(finite)));
  const limit = Math.ceil(largest * 2) / 2;
  const scale = (SIZE / 2 - 12) / limit;
  const toXY = (radius, degrees) => {
    const angle = (degrees * Math.PI) / 180;
    return [SIZE / 2 + radius * Math.cos(angle) * scale, SIZE / 2 - radius * Math.sin(angle) * scale];
  };
  const rings = [];
  for (let g = 0.5; g <= limit + 1e-6; g += 0.5) rings.push(g);
  return (
    <div className="flex flex-col gap-3" data-testid="gg-diagram">
      <p className="text-sm text-muted-foreground">
        유효한 랩 동안의 가속도입니다. 오른쪽은 오른쪽 코너, 위는 가속, 아래는 제동입니다. 점은 실제 순간들이고, 선은 방향마다 쓴 그립의 바깥선(상위 2%)입니다. 선이 바깥으로 넓을수록 그 방향의 그립을 더 썼다는 뜻입니다.
      </p>
      <div className="flex flex-wrap items-start gap-6">
        <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="h-auto w-full max-w-[320px]" role="img" aria-label="G-G 그림">
          <rect width={SIZE} height={SIZE} fill={comparisonColors().canvas} rx="8" />
          {rings.map((g) => <circle key={g} cx={SIZE / 2} cy={SIZE / 2} r={g * scale} fill="none" stroke={comparisonColors().grid} strokeWidth="1" />)}
          <line x1={SIZE / 2} x2={SIZE / 2} y1="6" y2={SIZE - 6} stroke={comparisonColors().grid} />
          <line y1={SIZE / 2} y2={SIZE / 2} x1="6" x2={SIZE - 6} stroke={comparisonColors().grid} />
          {rings.filter((g) => Number.isInteger(g)).map((g) => <text key={g} x={SIZE / 2 + 3} y={SIZE / 2 - g * scale + 10} fontSize="9" fill={comparisonColors().muted}>{g}g</text>)}
          <text x={SIZE / 2} y="14" textAnchor="middle" fontSize="9" fill={comparisonColors().muted}>가속</text>
          <text x={SIZE / 2} y={SIZE - 6} textAnchor="middle" fontSize="9" fill={comparisonColors().muted}>제동</text>
          <text x="8" y={SIZE / 2 - 4} fontSize="9" fill={comparisonColors().muted}>왼쪽</text>
          <text x={SIZE - 8} y={SIZE / 2 - 4} textAnchor="end" fontSize="9" fill={comparisonColors().muted}>오른쪽</text>
          {runs.map(([data, color], runIndex) => data.points.map(([x, y], index) => (
            <circle key={`${runIndex}-${index}`} cx={SIZE / 2 + x * scale} cy={SIZE / 2 - y * scale} r="0.9" fill={color} opacity={pair ? 0.18 : 0.3} />
          )))}
          {runs.map(([data, color, label, dash]) => {
            const path = envelopePath(data, toXY);
            return path ? <path key={label} d={path} fill="none" stroke={color} strokeWidth="2" strokeDasharray={dash || undefined} /> : null;
          })}
        </svg>
        <div className="grid gap-1 font-mono text-sm text-foreground">
          {runs.map(([data, color, label]) => (
            <div key={label} className="mb-2" data-testid={`gg-stats-${label}`}>
              <div style={{ color }} className="font-sans font-semibold">{label}</div>
              <div>최대 제동 {data.max_brake_g} g</div>
              <div>최대 가속 {data.max_accel_g} g</div>
              <div>최대 횡가속 {data.max_lateral_g} g</div>
              <div>평균 합성 {data.mean_combined_g} g</div>
            </div>
          ))}
          <p className="max-w-[16rem] font-sans text-xs text-muted-foreground">최대값은 상위 1% 값입니다(연석 충격 같은 순간값 제외).</p>
        </div>
      </div>
    </div>
  );
}
