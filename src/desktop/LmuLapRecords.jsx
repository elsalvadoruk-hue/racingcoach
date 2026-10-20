import React from 'react';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '../components/ui/table';
import { data, lapTime, number, signed } from './sessionData';

const statuses = { PIT_LAP: '피트 포함', UNTIMED: '기록 시간 없음', INCOMPLETE: '불완전',
  TIMING_MISMATCH: '시간 불일치', DISTANCE_DISCONTINUITY: '거리 불연속' };

export default function LmuLapRecords() {
  const stats = data.lap_statistics || {}, splits = data.sector_summary || {};
  return <div className="lap-records overflow-auto"><div className="record-summary">
    {[['기록 최저', lapTime(stats.fastest_recorded_time_s)], ['기록 중앙값', lapTime(stats.median_time_s)],
      ['랩타임 편차', `${number(stats.spread_s, 3)} s`], ['표준편차', `${number(stats.stddev_s, 3)} s`]]
      .map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
    </div><p>측정 가능 {stats.count ?? data.laps.filter(l => l.measurement_eligible).length}랩의 기록입니다. 게임 유효성은 별도 확인이 필요합니다.</p>
    <div className="lap-records-table-scroll min-h-48 shrink-0" role="region" aria-label="전체 랩 기록" tabIndex={0}>
      <Table><TableHeader><TableRow><TableHead>랩</TableHead><TableHead>기록 상태</TableHead><TableHead>랩타임</TableHead><TableHead>기록 최저 대비</TableHead></TableRow></TableHeader><TableBody>
        {data.laps.map(lap => <TableRow key={lap.lap_number}><TableCell>{lap.lap_number}랩</TableCell>
          <TableCell>{lap.measurement_eligible ? '기록 랩 · 분석 가능' : statuses[lap.status] || '측정 확인 필요'}</TableCell>
          <TableCell>{lapTime(lap.lap_time_s)}</TableCell><TableCell>{signed(lap.measurement_eligible && Number.isFinite(stats.fastest_recorded_time_s) ? lap.lap_time_s - stats.fastest_recorded_time_s : null)} s</TableCell></TableRow>)}
      </TableBody></Table></div>
    <h3>거리 구간별 최소 통과 조합</h3><p>{splits.reason || '구간 조합에는 측정 랩이 2개 이상 필요합니다.'}</p>
    {splits.status === 'OBSERVED_ONLY' && <><p>관측 {number(splits.start_m, 1)}–{number(splits.end_m, 1)} m · 조합 시간 {lapTime(splits.observed_combined_minimum_s)} · 완주 예상 시간이 아닙니다.</p>
      <Table><TableHeader><TableRow><TableHead>관측 거리 (m)</TableHead><TableHead>최소 통과 시간</TableHead><TableHead>사용한 기록 랩</TableHead></TableRow></TableHeader><TableBody>
        {(splits.rows || []).map(row => <TableRow key={row.start_m}><TableCell>{number(row.start_m, 1)}–{number(row.end_m, 1)}</TableCell><TableCell>{number(row.minimum_s, 3)} s</TableCell><TableCell>{row.source_lap_number}랩</TableCell></TableRow>)}
      </TableBody></Table></>}
  </div>;
}
