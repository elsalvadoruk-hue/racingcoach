import React, { useRef } from 'react';
import { Table, TableHeader, TableBody, TableRow, TableCell, TableHead } from '../components/ui/table';
import { Button } from '../components/ui/button';
import { data, lapTime, number, signed } from './sessionData';
import { isBestLap, lapStatus } from './labPreferences';
import { lapReproducibilityLabel } from '../analysis/analysisShared';

export default function LapRecords() {
  const bestRow = useRef(null);
  const best = data.laps.find(lap => isBestLap(lap, data.laps));
  const summary = data.comparisonSummary || data.comparisons?.[data.defaultTarget]?.comparison || {};
  const metrics = [['파일 베스트', summary.file_best_time_s ?? best?.lap_time_s], ['세션 베스트', summary.session_best_time_s], ['이론상 최적', summary.theoretical_optimal_time_s], ['재현 가능 최적', summary.reproducible_optimal_time_s], ['롤링 베스트', summary.rolling_best_time_s]];
  return <div className="lap-records"><div className="record-summary">{metrics.map(([title, value], index) => <div key={title} className={index === 0 ? 'best-lap' : undefined}><span>{title}{index === 0 && best ? ` · ★ ${best.lap_number}랩` : ''}</span><strong>{lapTime(value)}</strong></div>)}</div>
    <p>재현 가능 최적까지 {number(summary.recoverable_time_s, 3)} s · 롤링 베스트: {summary.rolling_best_laps?.join('–') || '—'}랩의 연속 1랩 구간 · 기록에 없는 값은 —</p>
    <div className="record-list-heading"><span>전체 {data.laps.length}랩 · 목록을 스크롤해 비교하세요.</span>{best && <Button variant="outline" size="sm" onClick={() => bestRow.current?.scrollIntoView({ block: 'center' })}>★ 베스트 {best.lap_number}랩으로</Button>}</div>
    <div className="lap-records-table-scroll" role="region" aria-label="전체 랩 기록" tabIndex={0}><Table><TableHeader><TableRow>{['랩', '상태 / 제외 이유', '랩타임', '베스트 대비', '재현성', '연료 사용'].map(t => <TableHead key={t}>{t}</TableHead>)}</TableRow></TableHeader><TableBody>{data.laps.map((l, index) => {
      const isBest = isBestLap(l, data.laps);
      return <TableRow key={`${l.lap_number}-${l.status}-${index}`} ref={isBest ? bestRow : undefined} className={isBest ? 'best-lap-row' : undefined}><TableCell>{l.lap_number}랩{isBest ? ' · ★ 베스트' : ''}</TableCell><TableCell>{lapStatus(l)}<small>{l.invalid_reason}</small></TableCell><TableCell>{lapTime(l.lap_time_s)}</TableCell><TableCell>{signed(l.delta_to_file_best_s)} s</TableCell><TableCell>{l.status === 'VALID' ? <>{lapReproducibilityLabel(l.reproducibility_status).label}<small>비슷한 랩 {l.reproducibility_support ?? 0}개 · RMS {number(l.reproducibility_trace_rms_s, 3)} s</small></> : '—'}</TableCell><TableCell>{number(l.fuel_used_l, 2)} L</TableCell></TableRow>;
    })}</TableBody></Table></div>
  </div>;
}
