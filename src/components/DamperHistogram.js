import React from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { COMPARISON_COLORS } from '../chartTheme';

// Damper velocity histograms (RaceStudio 3's suspension analysis). Positive velocity is compression (bump).
// One run, or A (before the change) and B (after it) laid over each other.
const WHEELS = [['LF', '앞 왼쪽'], ['RF', '앞 오른쪽'], ['LR', '뒤 왼쪽'], ['RR', '뒤 오른쪽']];
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const pct = (value) => (finite(value) ? `${value.toFixed(1)}%` : '--');
const signed = (value) => (finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}%p` : '--');

function rows(edges, a, b) {
  return edges.slice(0, -1).map((edge, index) => ({
    v: edge + (edges[index + 1] - edge) / 2,
    a: a ? a.share_pct[index] : undefined,
    b: b ? b.share_pct[index] : undefined,
  }));
}

export default function DamperHistogram({ current, baseline, candidate }) {
  const pair = Boolean(baseline || candidate);
  const first = pair ? baseline : current;
  const second = pair ? candidate : null;
  const usable = [first, second].filter((item) => item?.available);
  if (!usable.length) {
    const reason = [first, second].find((item) => item?.reason)?.reason;
    return <p className="text-sm text-muted-foreground">{reason === 'NO_SHOCK_CHANNELS' ? '이 주행 파일에는 쇼크 속도 기록이 없습니다.' : '유효한 랩이 없어 댐퍼 속도를 계산하지 않았습니다.'}</p>;
  }
  const edges = usable[0].edges_mm_s;
  const slow = usable[0].slow_limit_mm_s;
  const nameA = pair ? 'A · 변경 전' : '이번 주행';
  return (
    <div className="flex flex-col gap-4" data-testid="damper-histogram">
      <p className="text-sm text-muted-foreground">
        유효한 랩 동안 쇼크가 각 속도로 움직인 시간 비율입니다. 오른쪽(+)이 압축(범프), 왼쪽(−)이 늘어남(리바운드)입니다.
        {` 빠르고 느림은 ${slow} mm/s를 기준으로 나눠 보기 위한 경계이며, 차의 댐퍼 저속·고속 경계와는 다를 수 있습니다.`}
        {usable[0].sample_hz ? ` 기록 간격은 초당 ${usable[0].sample_hz}회라 짧은 충격은 덜 잡힙니다.` : ''}
      </p>
      {pair && (
        <div className="flex flex-wrap gap-4 text-sm">
          <span style={{ color: COMPARISON_COLORS.reference }}>┄ A · 변경 전</span>
          <span style={{ color: COMPARISON_COLORS.target }}>━ B · 변경 후</span>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {WHEELS.map(([key, label]) => {
          const a = first?.wheels?.[key];
          const b = second?.wheels?.[key];
          if (!a && !b) return null;
          const delta = (field) => (a && b ? b[field] - a[field] : null);
          return (
            <div key={key} className="rounded-xl border border-border bg-background p-3" data-testid={`damper-${key}`}>
              <p className="text-sm font-semibold text-foreground">{label} ({key})</p>
              <div className="mt-2 h-40 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={rows(edges, a, b)} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke={COMPARISON_COLORS.grid} strokeDasharray="3 3" />
                    <XAxis dataKey="v" type="number" domain={[edges[0], edges[edges.length - 1]]} stroke={COMPARISON_COLORS.muted} tick={{ fontSize: 10 }} tickFormatter={(value) => `${value}`} />
                    <YAxis width={34} stroke={COMPARISON_COLORS.muted} tick={{ fontSize: 10 }} unit="%" />
                    <ReferenceLine x={0} stroke={COMPARISON_COLORS.muted} />
                    <Tooltip formatter={(value, name) => [pct(value), name]} labelFormatter={(value) => `${value > 0 ? '+' : ''}${value} mm/s`}
                      contentStyle={{ background: COMPARISON_COLORS.canvas, border: `1px solid ${COMPARISON_COLORS.grid}` }} />
                    {a && <Line dataKey="a" name={nameA} stroke={pair ? COMPARISON_COLORS.reference : COMPARISON_COLORS.target} strokeDasharray={pair ? '4 3' : undefined} dot={false} strokeWidth={2} isAnimationActive={false} />}
                    {b && <Line dataKey="b" name="B · 변경 후" stroke={COMPARISON_COLORS.target} dot={false} strokeWidth={2} isAnimationActive={false} />}
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground">
                {[['빠른 범프', 'fast_bump_pct'], ['빠른 리바운드', 'fast_rebound_pct'], ['느린 범프', 'slow_bump_pct'], ['느린 리바운드', 'slow_rebound_pct']].map(([name, field]) => (
                  <span key={field}>{name} {pair ? `${pct(a?.[field])} → ${pct(b?.[field])} (${signed(delta(field))})` : pct(a?.[field])}</span>
                ))}
                <span className="col-span-2">95% 속도 {pair ? `${a?.p95_mm_s ?? '--'} → ${b?.p95_mm_s ?? '--'}` : a?.p95_mm_s ?? '--'} mm/s</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
