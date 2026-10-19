import React from 'react';
import { Button } from './ui/button';

// Pick the laps that count by lap time (RaceStudio 3's lap-time range slider): traffic and mistake laps
// drop out of the per-lap statistics without choosing them one by one.
const format = (seconds) => {
  if (!Number.isFinite(seconds)) return '--';
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(3).padStart(6, '0')}`;
};

export function lapsInRange(laps = [], range) {
  const valid = laps.filter((lap) => lap.status === 'VALID' && Number.isFinite(lap.lap_time_s));
  if (!range) return new Set(valid.map((lap) => lap.lap_number));
  return new Set(valid.filter((lap) => lap.lap_time_s >= range[0] - 1e-6 && lap.lap_time_s <= range[1] + 1e-6).map((lap) => lap.lap_number));
}

export default function LapRangeFilter({ laps = [], value, onChange }) {
  const times = laps.filter((lap) => lap.status === 'VALID' && Number.isFinite(lap.lap_time_s)).map((lap) => lap.lap_time_s);
  if (times.length < 3) return null;
  const low = Math.min(...times);
  const high = Math.max(...times);
  const [from, to] = value || [low, high];
  const step = 0.05;
  const count = lapsInRange(laps, value).size;
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-background p-3" data-testid="lap-range-filter">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-semibold text-foreground">랩 고르기</span>
        <span className="font-mono text-muted-foreground">{format(from)} ~ {format(to)} · 유효 랩 {times.length}개 중 {count}개</span>
        {value && <Button variant="outline" type="button" onClick={() => onChange(null)} className="border px-2 py-1 text-xs">전체 랩</Button>}
      </div>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">가장 빠른
        <input type="range" aria-label="포함할 가장 빠른 랩타임" min={low} max={high} step={step} value={from}
          onChange={(event) => { const next = Math.min(Number(event.target.value), to); onChange(next <= low + 1e-6 && to >= high - 1e-6 ? null : [next, to]); }}
          className="w-full accent-cyan-500" />
      </label>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">가장 느린
        <input type="range" aria-label="포함할 가장 느린 랩타임" min={low} max={high} step={step} value={to}
          onChange={(event) => { const next = Math.max(Number(event.target.value), from); onChange(from <= low + 1e-6 && next >= high - 1e-6 ? null : [from, next]); }}
          className="w-full accent-cyan-500" />
      </label>
      <p className="text-xs text-muted-foreground">트래픽이나 실수로 느렸던 랩을 빼고 보려면 "가장 느린" 쪽을 줄이세요. 랩별 시간 쓰임새와 코너별 일관성에 함께 적용됩니다.</p>
    </div>
  );
}
