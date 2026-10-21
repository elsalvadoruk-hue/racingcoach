import React, { useMemo } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { comparisonColors } from '../chartTheme';

// Where each lap's time goes (RaceStudio 3's brake / coast / full-throttle / corner lap statistics).
// The first four split the lap; cornering overlaps them and is shown on its own.
export const PARTS = [
  { key: 'brake', label: '제동', color: '#e5675a' },
  { key: 'coast', label: '코스팅', color: '#8a96a8' },
  { key: 'part_throttle', label: '부분 스로틀', color: '#e6b450' },
  { key: 'full_throttle', label: '풀스로틀', color: '#5fbf77' },
];
const CORNER = { key: 'corner', label: '코너링' };
export const LMU_PARTS = [
  { key: 'BRAKE_ONLY', label: '제동만', color: '#e5675a' },
  { key: 'THROTTLE_ONLY', label: '가속만', color: '#5fbf77' },
  { key: 'OVERLAP', label: '동시입력', color: '#e6b450' },
  { key: 'BOTH_LOW', label: '두 페달 5% 미만', color: '#8a96a8' },
];
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const pct = (value) => (finite(value) ? `${value.toFixed(1)}%` : '--');
const lapTime = seconds => finite(seconds) ? `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(3).padStart(6, '0')}` : '—';

export default function LapTimeUse({ laps = [], provider = 'iracing' }) {
  // LMU recordings analysed by the shared analysis carry the same time_use as iRacing;
  // only the older measurement-only payload has pedal_usage.
  const lmu = provider === 'lmu' && laps.some(lap => lap.pedal_usage) && !laps.some(lap => lap.time_use?.share_pct);
  const parts = lmu ? LMU_PARTS : PARTS;
  const corner = { ...CORNER, color: comparisonColors().target };
  const rows = useMemo(() => lmu ? laps.filter(lap => lap.measurement_eligible && lap.pedal_usage?.observed_time_s > 0).map(lap => ({ ...lap,
    time_use: { share_pct: Object.fromEntries(lap.pedal_usage.categories.map(part => [part.key, part.observed_percent])) } }))
    : laps.filter((lap) => lap.status === 'VALID' && lap.time_use?.share_pct), [laps, lmu]);
  const best = rows.find((lap) => lap.is_file_best);
  const average = useMemo(() => {
    const out = {};
    [...parts, CORNER].forEach(({ key }) => {
      const values = rows.map((lap) => lap.time_use.share_pct[key]).filter(finite);
      out[key] = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    });
    return out;
  }, [rows, parts]);
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">{lmu ? '선택한 랩에 비교 가능한 브레이크·스로틀 관측 구간이 없습니다.' : '유효한 랩에 브레이크·스로틀 기록이 없어 계산하지 않았습니다.'}</p>;
  }
  const trend = rows.map((lap) => ({ lap: lap.lap_number, ...lap.time_use.share_pct }));
  const hasCorner = rows.some((lap) => finite(lap.time_use.share_pct.corner));
  return (
    <div className="flex flex-col gap-5" data-testid="lap-time-use">
      <p className="text-sm text-muted-foreground">
        {lmu ? '두 페달 모두 5% 이상을 입력으로 셉니다. 제동만·가속만·동시입력·두 페달 5% 미만으로 나누며 비율은 관측된 시간 기준입니다. 게임의 랩 유효성은 확인 필요입니다.' : '유효한 랩마다 시간을 어디에 썼는지 나눴습니다. 제동은 브레이크 8% 넘게, 풀스로틀은 스로틀 95% 이상, 코스팅은 둘 다 밟지 않은(스로틀 5% 미만) 시간입니다.'}
        {hasCorner ? ' 코너링은 횡가속 0.5g 이상인 시간이라 다른 칸과 겹칩니다.' : ''}
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {[...parts, ...(hasCorner ? [corner] : [])].map((part) => (
          <span key={part.key} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: part.color }} />{part.label} 평균 {pct(average[part.key])}
          </span>
        ))}
      </div>
      <div className="flex flex-col gap-1.5">
        {rows.map((lap) => {
          const share = lap.time_use.share_pct;
          return (
            <div key={lap.lap_number} className="grid grid-cols-[3.5rem_5.5rem_1fr_4.5rem] items-center gap-2 text-sm" data-testid={`time-use-lap-${lap.lap_number}`}>
              <span className="font-mono text-muted-foreground">랩 {lap.lap_number}</span>
              <span className={`font-mono ${lap === best ? 'font-bold text-warning' : 'text-foreground'}`}>{lap.lap_time || lapTime(lap.lap_time_s)}</span>
              <div className="flex h-4 overflow-hidden rounded-sm bg-muted" title={parts.map((part) => `${part.label} ${pct(share[part.key])}${lmu ? ` · ${lap.pedal_usage.categories.find(c => c.key === part.key)?.duration_s?.toFixed(3)} s` : ''}`).join(' · ')}>
                {parts.map((part) => (finite(share[part.key]) && share[part.key] > 0
                  ? <div key={part.key} style={{ width: `${share[part.key]}%`, background: part.color }} aria-label={`${part.label} ${pct(share[part.key])}`} />
                  : null))}
              </div>
              <span className="text-right font-mono text-xs text-muted-foreground" title={lmu ? `관측 ${lap.pedal_usage.observed_time_s.toFixed(3)} s · 미관측 ${lap.pedal_usage.unobserved_time_s.toFixed(3)} s` : undefined}>{lmu ? `관측 ${pct(lap.pedal_usage.coverage_ratio * 100)}` : hasCorner ? `코너 ${pct(share.corner)}` : ''}</span>
              {lmu && <div className="col-span-4 flex flex-wrap gap-x-4 text-xs text-muted-foreground">{parts.map(part => <span key={part.key}>{part.label} {lap.pedal_usage.categories.find(item => item.key === part.key)?.duration_s?.toFixed(3)} s</span>)}<span>미관측 {lap.pedal_usage.unobserved_time_s.toFixed(3)} s</span><span>{lap.pedal_usage.input_stage === 'UNFILTERED' ? '원본 페달 입력' : '게임 처리 입력'}</span></div>}
            </div>
          );
        })}
      </div>
      {rows.length >= 3 && (
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trend} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
              <CartesianGrid stroke={comparisonColors().grid} strokeDasharray="3 3" />
              <XAxis dataKey="lap" stroke={comparisonColors().muted} tick={{ fontSize: 10 }} tickFormatter={(value) => `랩 ${value}`} />
              <YAxis width={40} stroke={comparisonColors().muted} tick={{ fontSize: 10 }} unit="%" domain={[0, 'auto']} />
              <Tooltip formatter={(value, name) => [pct(value), name]} labelFormatter={(value) => `랩 ${value}`}
                contentStyle={{ background: comparisonColors().canvas, border: `1px solid ${comparisonColors().grid}` }} />
              {[...parts, ...(hasCorner ? [corner] : [])].map((part) => (
                <Line key={part.key} type="monotone" dataKey={part.key} name={part.label} stroke={part.color} dot={{ r: 2 }} strokeWidth={2} isAnimationActive={false} connectNulls={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
