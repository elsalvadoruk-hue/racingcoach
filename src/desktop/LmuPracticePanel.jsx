import React, { useEffect, useRef, useState } from 'react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '../components/ui/table';
import { requestJson } from '../analysis/analysisClient';
import { number, signed } from './sessionData';
import { LmuConsistencyTable, LmuFeedback, LmuLapSelection, lmuPost } from './LmuAnalysisPanels';

export default function LmuPracticePanel({ onChooseSource, embedded = false }) {
  const [tasks, setTasks] = useState([]), [selections, setSelections] = useState([]), [detail, setDetail] = useState(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const initial = useRef(null), flight = useRef(null);
  useEffect(() => {
    const controller = new AbortController(); initial.current = controller;
    requestJson('/api/lmu/practice/tasks', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setTasks(result.tasks || []);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); flight.current?.abort(); };
  }, []);
  async function run(action, taskId) {
    initial.current?.abort(); setLoading(false); flight.current?.abort();
    const controller = new AbortController(); flight.current = controller;
    setBusy(true); setError('');
    try {
      if (action === 'open') {
        const result = await requestJson(`/api/lmu/practice/tasks/${encodeURIComponent(taskId)}`, { signal: controller.signal });
        if (!controller.signal.aborted) setDetail(result);
      } else if (action === 'create') {
        const result = await lmuPost('/api/lmu/practice/tasks', { selections }, controller.signal);
        if (!controller.signal.aborted) { setTasks(previous => [...previous, result.task]); setDetail({ task: result.task, comparisons: [] }); setSelections([]); }
      } else {
        const result = await lmuPost(`/api/lmu/practice/tasks/${encodeURIComponent(taskId)}/compare`, { selections }, controller.signal);
        if (!controller.signal.aborted) { setDetail(previous => ({ ...previous, comparisons: [...(previous?.comparisons || []), result.comparison] })); setSelections([]); }
      }
    } catch (e) { if (!controller.signal.aborted) setError(e.message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  const baseline = detail?.task?.baseline;
  const countOK = selections.length >= 3 && selections.length <= 6;
  const reused = baseline && selections.some(item => baseline.sources?.some(source => source.file_id === item.file_id && source.lap_number === item.lap_number));
  const Container = embedded ? 'div' : 'section';
  return <Container className={embedded ? 'flex flex-col gap-4' : 'wide-workspace gap-4 overflow-auto'} aria-label="LMU 연습 기록"><div className="wide-heading"><div><h2>연습 기록 · 반복 연습</h2><p>같은 구간의 입력 위치와 구간 시간을 다음 주행과 비교합니다.</p></div>{onChooseSource && <Button variant="outline" onClick={onChooseSource}>주행 선택</Button>}</div><LmuFeedback error={error} loading={loading || busy} />
    <Card><CardHeader><CardTitle>다음 주행에서 확인할 과제</CardTitle><CardDescription>같은 차량·트랙의 측정 랩 3~6개로 기준을 저장하고 다음 주행의 새 랩으로 확인합니다.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><LmuLapSelection selections={selections} onChange={setSelections} disabled={busy} /><div className="flex flex-wrap gap-2"><Button disabled={busy || !countOK} onClick={() => run('create')}>선택한 랩으로 연습 기준 저장</Button><Button variant="outline" disabled={busy || !countOK || !baseline || reused} onClick={() => run('compare', detail.task.task_id)}>선택한 새 랩으로 과제 비교</Button></div>{reused && <p>기준에 사용한 랩이 포함되어 있습니다. 다음 주행의 새 랩을 선택하세요.</p>}</CardContent><CardFooter><p>공통 구간의 입력 위치 편차를 관측하는 과제입니다. 위치는 최대 2m 격자의 근사 관측이며 게임 유효성과 인과 효과를 확정하지 않습니다.</p></CardFooter></Card>
    <Card><CardHeader><CardTitle>저장한 연습</CardTitle><CardDescription>LMU 전용 로컬 저장소의 기록입니다.</CardDescription></CardHeader><CardContent>{tasks.length ? <Table><TableHeader><TableRow><TableHead>저장 시각</TableHead><TableHead>기준 측정 랩</TableHead><TableHead>과제</TableHead><TableHead>조회</TableHead></TableRow></TableHeader><TableBody>{[...tasks].reverse().map(task => <TableRow key={task.task_id}><TableCell>{task.created_at}</TableCell><TableCell>{task.baseline?.lap_count ?? task.lap_count ?? '—'}</TableCell><TableCell>{(task.baseline?.focus || task.focus || []).map(focus => focus.label).join(' · ') || '반복성 확인'}</TableCell><TableCell><Button size="sm" variant="outline" disabled={busy} onClick={() => run('open', task.task_id)}>과제 열기</Button></TableCell></TableRow>)}</TableBody></Table> : !loading && <p>저장된 LMU 연습 기록이 없습니다. 위에서 측정 랩을 선택해 기준을 저장하세요.</p>}</CardContent></Card>
    {baseline && <Card><CardHeader><CardTitle>현재 과제의 기준 관측</CardTitle><CardDescription>{detail.task.created_at} · {baseline.lap_count}랩</CardDescription></CardHeader><CardContent><LmuConsistencyTable report={baseline} /></CardContent></Card>}
    {(detail?.comparisons || []).map((report, index) => <Card key={report.comparison_id || index}><CardHeader><CardTitle>주행 후 확인 {index + 1}</CardTitle><CardDescription>{report.created_at} · 새 측정 {report.lap_count}랩</CardDescription></CardHeader><CardContent><Badge variant="outline">입력 편차 관측</Badge><Table><TableHeader><TableRow><TableHead>구간</TableHead><TableHead>기준 시간 (s)</TableHead><TableHead>새 주행 시간 (s)</TableHead><TableHead>제동 시작 편차 변화 (m)</TableHead><TableHead>스로틀 20% 관측 편차 변화 (m)</TableHead><TableHead>풀스로틀 편차 변화 (m)</TableHead></TableRow></TableHeader><TableBody>{(report.corners || []).map(row => <TableRow key={row.id}><TableCell>{row.label || row.id}</TableCell><TableCell>{number(row.baseline_median_duration_s, 3)}</TableCell><TableCell>{number(row.current_median_duration_s, 3)}</TableCell><TableCell>{signed(row.spread_delta_m?.brake_start_m, 1)}</TableCell><TableCell>{signed(row.spread_delta_m?.throttle_first_m, 1)}</TableCell><TableCell>{signed(row.spread_delta_m?.full_throttle_m, 1)}</TableCell></TableRow>)}</TableBody></Table><p>{report.note || '음수는 입력 위치 편차가 줄었다는 뜻입니다. 조건과 주행 개선 효과는 별도로 확인해야 합니다.'}</p></CardContent></Card>)}
  </Container>;
}
