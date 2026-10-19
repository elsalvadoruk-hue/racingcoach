import React, { useMemo, useState } from 'react';
import LapRangeFilter, { lapsInRange } from '../components/LapRangeFilter';
import LapRecords from './LapRecords';
import LapTimeUse from '../components/LapTimeUse';
import { summarize } from '../components/CornerConsistency';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { Button } from '../components/ui/button';
import { data, laps, signed, cornerLabel } from './sessionData';

export default function LapAnalysis({ target, names, onCorner, range, onRange, chosen, onChosen, tab, onTab }) {
  const [order, setOrder] = useState('spread');
  const allowed = useMemo(() => { const inRange = lapsInRange(laps, range); return new Set(chosen.map(Number).filter(n => inRange.has(n))); }, [chosen, range]);
  const rows = (data.consistency?.corners || []).map(corner => ({ corner, stats: summarize(corner.passes, allowed, Number(target)) })).filter(row => row.stats);
  if (order === 'spread') rows.sort((a, b) => b.stats.iqr - a.stats.iqr);
  const scale = Math.max(.2, ...rows.map(row => Math.max(row.stats.whiskerHigh, row.stats.target ?? row.stats.min, ...row.stats.outliers) - row.stats.min));
  return <section className="wide-workspace lap-analysis"><div className="wide-heading"><div><h2>랩 분석</h2><p>시간 쓰임새와 반복 주행의 일관성을 함께 확인하세요.</p></div><ToggleGroup type="multiple" variant="outline" value={chosen} onValueChange={onChosen} aria-label="분석할 랩">{laps.map(lap => <ToggleGroupItem key={lap.lap_number} value={String(lap.lap_number)}>{lap.lap_number}랩</ToggleGroupItem>)}</ToggleGroup></div>
    <LapRangeFilter laps={laps} value={range} onChange={onRange} />
    <Tabs value={tab} onValueChange={onTab} className="wide-tabs"><TabsList><TabsTrigger value="time">랩별 시간 쓰임새</TabsTrigger><TabsTrigger value="consistency">코너별 일관성</TabsTrigger><TabsTrigger value="records">랩 기록·최적 랩</TabsTrigger></TabsList>
      <TabsContent value="records" className="records-panel"><LapRecords /></TabsContent>
      <TabsContent value="time" className="time-use-panel"><LapTimeUse laps={laps.filter(lap => allowed.has(lap.lap_number))} /></TabsContent>
      <TabsContent value="consistency" className="consistency-panel"><div className="consistency-heading"><p>선택 {allowed.size}랩 · 상자: 가운데 50% · 회색 점: 이상치 · 주황 점: {target}랩 · 가로축: 해당 코너 최저 통과 시간 대비 차이</p><ToggleGroup type="single" variant="outline" size="sm" value={order} onValueChange={v => v && setOrder(v)} aria-label="일관성 정렬"><ToggleGroupItem value="spread">흔들림 큰 순</ToggleGroupItem><ToggleGroupItem value="track">트랙 순서</ToggleGroupItem></ToggleGroup></div>
        {rows.length ? <div className="consistency-columns">{[rows.slice(0, Math.ceil(rows.length / 2)), rows.slice(Math.ceil(rows.length / 2))].map((group, i) => <div className="consistency-table" key={i}><div className="consistency-row headings"><span>구간</span><span>통과 시간 분포</span><span>흔들림</span><span>빠른 3회 대비</span></div>{group.map(({corner, stats}) => { const x = time => 5 + (time - stats.min) / scale * 190; return <Button variant="ghost" key={corner.id} className="consistency-row" onClick={() => onCorner(corner.id)} title={`${cornerLabel(corner, names)} · ${stats.count}회 · 클릭하여 코너 비교`}><span title={cornerLabel(corner, names)}>{names[corner.id]?.number ? `${names[corner.id].number}번` : corner.id}</span><svg viewBox="0 0 200 22" preserveAspectRatio="none" aria-label={`${corner.id} 통과 시간 분포`}><line x1={x(stats.whiskerLow)} x2={x(stats.whiskerHigh)} y1="11" y2="11" stroke="hsl(var(--muted-foreground))" /><rect x={x(stats.q1)} y="4" width={Math.max(1, x(stats.q3) - x(stats.q1))} height="14" fill="hsl(var(--primary) / .25)" stroke="var(--lab-target)" /><line x1={x(stats.median)} x2={x(stats.median)} y1="4" y2="18" stroke="var(--lab-target)" />{stats.outliers.map((v, i) => <circle key={i} cx={x(v)} cy="11" r="2" fill="hsl(var(--muted-foreground))" />)}{stats.target != null && <circle cx={x(stats.target)} cy="11" r="3.5" fill="var(--lab-reference)" />}</svg><span>{stats.iqr.toFixed(3)} s</span><span>{signed(stats.targetVsBest3)} s</span></Button>; })}</div>)}</div> : <p className="empty-data">유효한 랩을 3개 이상 선택하면 코너별 일관성을 볼 수 있습니다.</p>}
      </TabsContent>
    </Tabs>
  </section>;
}
