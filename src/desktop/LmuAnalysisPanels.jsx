import React, { useEffect, useId, useRef, useState } from 'react';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '../components/ui/card';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '../components/ui/empty';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '../components/ui/table';
import CornerConsistency from '../components/CornerConsistency';
import { requestJson } from '../analysis/analysisClient';
import { data, labRequest, measurableLap, number, signed, lapTime } from './sessionData';

export const lmuPost = (path, body, signal) => requestJson(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });

// Clearing the previous result when a request identity changes prevents a new
// file/lap heading from displaying the previous lap's measurements.
export function useLmuResource(path) {
  const [state, setState] = useState({ path: null, payload: null, error: '', loading: false });
  useEffect(() => {
    if (!path) return undefined;
    const controller = new AbortController();
    setState({ path, payload: null, error: '', loading: true });
    requestJson(path, { signal: controller.signal }).then(payload => {
      if (!controller.signal.aborted) setState({ path, payload, error: '', loading: false });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ path, payload: null, error: error.message, loading: false });
    });
    return () => controller.abort();
  }, [path]);
  return state.path === path ? state : { payload: null, error: '', loading: Boolean(path) };
}

export function LmuFeedback({ error, loading }) {
  return <>{error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}{loading && <p role="status">측정값을 불러오는 중…</p>}</>;
}

export function LmuNoSource({ onChooseSource }) {
  return <Empty><EmptyHeader><EmptyTitle>분석할 주행을 선택하세요</EmptyTitle><EmptyDescription>LMU 기록 파일에서 측정 가능한 랩을 선택하면 분석을 시작합니다.</EmptyDescription></EmptyHeader>{onChooseSource && <Button variant="outline" onClick={onChooseSource}>주행 선택</Button>}</Empty>;
}

export function LmuLapSelection({ selections, onChange, disabled = false }) {
  const id = useId();
  const [files, setFiles] = useState([]), [filename, setFilename] = useState(data.filename || '');
  const [available, setAvailable] = useState([]), [error, setError] = useState(''), [loading, setLoading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    labRequest('files', {}, controller.signal).then(result => {
      if (!controller.signal.aborted) setFiles((result.files || []).filter(file => file.analyzable !== false));
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    setAvailable([]);
    if (!filename) return undefined;
    const controller = new AbortController(); setLoading(true); setError('');
    labRequest('file-laps', { filename }, controller.signal).then(result => {
      if (!controller.signal.aborted) setAvailable((result.laps || []).filter(measurableLap));
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filename]);
  const fileLabel = key => files.find(file => file.name === key)?.display_name || files.find(file => file.name === key)?.file_name || key;
  return <FieldGroup><Field><FieldLabel htmlFor={`${id}-file`}>측정 랩을 추가할 주행</FieldLabel><NativeSelect id={`${id}-file`} value={filename} onChange={event => setFilename(event.target.value)} disabled={disabled}><NativeSelectOption value="">주행 선택</NativeSelectOption>{files.map(file => <NativeSelectOption key={file.name} value={file.name}>{file.display_name || file.file_name || file.name}</NativeSelectOption>)}</NativeSelect></Field>
    <LmuFeedback error={error} loading={loading} />
    <div className="flex flex-wrap gap-2" aria-label="추가할 측정 랩">{available.map(lap => {
      const selected = selections.some(item => item.file_id === filename && item.lap_number === lap.lap_number);
      return <Button key={lap.lap_number} size="sm" variant={selected ? 'secondary' : 'outline'} disabled={disabled || (!selected && selections.length >= 6)} aria-pressed={selected} onClick={() => onChange(selected ? selections.filter(item => item.file_id !== filename || item.lap_number !== lap.lap_number) : [...selections, { file_id: filename, lap_number: lap.lap_number }])}>랩 {lap.lap_number} · {lapTime(lap.lap_time_s)}</Button>;
    })}</div>
    <p>서로 다른 측정 랩 3~6개를 직접 선택하세요. 여러 파일에서 추가할 수 있습니다. 선택 {selections.length}개</p>
    {selections.length > 0 && <Table><TableHeader><TableRow><TableHead>선택한 주행</TableHead><TableHead>랩</TableHead><TableHead>선택 해제</TableHead></TableRow></TableHeader><TableBody>{selections.map(item => <TableRow key={`${item.file_id}-${item.lap_number}`}><TableCell>{fileLabel(item.file_id)}</TableCell><TableCell>{item.lap_number}</TableCell><TableCell><Button variant="ghost" size="sm" disabled={disabled} aria-label={`랩 ${item.lap_number} 선택 해제`} onClick={() => onChange(selections.filter(other => other !== item))}>해제</Button></TableCell></TableRow>)}</TableBody></Table>}
  </FieldGroup>;
}

export function LmuConsistencyTable({ report }) {
  if (!report) return null;
  return <>{report.reason && <p>{report.reason}</p>}<Table><TableHeader><TableRow><TableHead>구간</TableHead><TableHead>측정 횟수</TableHead><TableHead>중앙 시간 (s)</TableHead><TableHead>시간 편차 (s)</TableHead><TableHead>제동 시작 편차 (m)</TableHead><TableHead>풀스로틀 편차 (m)</TableHead></TableRow></TableHeader><TableBody>{(report.corners || []).map(row => <TableRow key={row.id}><TableCell>{row.label || row.id}</TableCell><TableCell>{row.sample_count}</TableCell><TableCell>{number(row.median_duration_s, 3)}</TableCell><TableCell>{number(row.spread_s, 3)}</TableCell><TableCell>{number(row.input_consistency?.brake_start_m?.spread_m)}</TableCell><TableCell>{number(row.input_consistency?.full_throttle_m?.spread_m)}</TableCell></TableRow>)}</TableBody></Table>{(report.focus || []).map(focus => <p key={`${focus.id}-${focus.metric}`}><strong>{focus.label}</strong> · {focus.observation} {focus.next_check}</p>)}</>;
}

function ZoneTable({ zones = [], braking = false }) {
  return zones.length ? <Table><TableHeader><TableRow><TableHead>구간</TableHead><TableHead>시작 (m)</TableHead><TableHead>종료 (m)</TableHead><TableHead>시간 (s)</TableHead>{braking && <><TableHead>진입 속도 (km/h)</TableHead><TableHead>종료 속도 (km/h)</TableHead><TableHead>평균 감속 (g)</TableHead></>}<TableHead>관측</TableHead></TableRow></TableHeader><TableBody>{zones.map((zone, index) => <TableRow key={`${zone.start_elapsed_s}-${index}`}><TableCell>{index + 1}</TableCell><TableCell>{number(zone.start_distance_m)}</TableCell><TableCell>{number(zone.end_distance_m)}</TableCell><TableCell>{number(zone.duration_s, 3)}</TableCell>{braking && <><TableCell>{number(zone.shared_measurement?.metrics?.brake_entry_speed_kph)}</TableCell><TableCell>{number(zone.shared_measurement?.metrics?.brake_end_speed_kph)}</TableCell><TableCell>{number(zone.shared_measurement?.metrics?.avg_decel_g, 2)}</TableCell></>}<TableCell>{zone.status === 'COMPLETE' ? '구간 전체' : '경계·결측 포함'}</TableCell></TableRow>)}</TableBody></Table> : <p>측정 가능한 구간이 없습니다.</p>;
}

export function LmuConsistencyPanel({ onCorner }) {
  const [selections, setSelections] = useState([]), [report, setReport] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const flight = useRef(null);
  useEffect(() => () => flight.current?.abort(), []);
  async function analyze() {
    const controller = new AbortController(); flight.current?.abort(); flight.current = controller;
    setBusy(true); setError(''); setReport(null);
    try { const result = await lmuPost('/api/lmu/telemetry/corner-consistency', { selections }, controller.signal); if (!controller.signal.aborted) setReport(result.consistency); }
    catch (e) { if (!controller.signal.aborted) setError(e.message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  const targetIndex = selections.findIndex(item => item.file_id === data.filename && item.lap_number === Number(data.defaultTarget));
  const currentReference = selections[0]?.file_id === (data.reference_filename || data.filename) && selections[0]?.lap_number === Number(data.defaultReference);
  const distribution = report && { available: (report.corners || []).some(row => row.passes?.length >= 3),
    target_lap: targetIndex,
    corners: (report.corners || []).map(row => ({ ...row, passes: (row.passes || [])
      .filter(pass => Number.isInteger(pass.selection_index) && pass.selection_index >= 0 && pass.selection_index < selections.length && Number.isFinite(pass.duration_s) && pass.duration_s > 0)
      .map(pass => ({ lap: pass.selection_index, time_s: pass.duration_s })) })) };
  return <Card><CardHeader><CardTitle>코너 일관성</CardTitle><CardDescription>같은 차량·트랙·입력 처리 단계의 3~6랩에서 반복성을 계산합니다.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><LmuLapSelection selections={selections} onChange={next => { setSelections(next); setReport(null); }} disabled={busy} /><Button disabled={busy || selections.length < 3} onClick={analyze}>일관성 분석</Button><LmuFeedback error={error} loading={busy} />{distribution?.available && <CornerConsistency consistency={distribution} targetLabel={targetIndex >= 0 ? `${data.defaultTarget} (현재 내 파일)` : '미포함'} onCorner={currentReference ? onCorner : undefined} />}<LmuConsistencyTable report={report} /></CardContent></Card>;
}

function VehiclePanel() {
  const id = useId(), [channel, setChannel] = useState('TyresPressure'), [catalog, setCatalog] = useState([]);
  const source = `file_id=${encodeURIComponent(data.filename)}&lap_number=${data.defaultTarget}`;
  const resource = useLmuResource(`/api/lmu/telemetry/vehicle-channels?${source}&channel_name=${encodeURIComponent(channel)}`);
  const vehicle = resource.payload?.vehicle;
  useEffect(() => { if (vehicle?.catalog) setCatalog(vehicle.catalog); }, [vehicle]);
  const referenceFile = data.reference_filename || data.filename;
  const compare = data.defaultReference != null && (referenceFile !== data.filename || data.defaultReference !== data.defaultTarget);
  const comparison = useLmuResource(compare ? `/api/lmu/telemetry/vehicle-comparison?reference_file_id=${encodeURIComponent(referenceFile)}&target_file_id=${encodeURIComponent(data.filename)}&reference_lap_number=${data.defaultReference}&target_lap_number=${data.defaultTarget}&channel_name=${encodeURIComponent(channel)}` : null);
  const delta = comparison.payload?.comparison;
  return <Card><CardHeader><CardTitle>차량 상태 · 타이어·연료·서스펜션</CardTitle><CardDescription>선택한 내 랩 {data.defaultTarget} · 기준 랩 {data.defaultReference ?? '—'}. 기록된 단위와 관측 구간 안에서 비교합니다.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><Field><FieldLabel htmlFor={`${id}-channel`}>차량 채널</FieldLabel><NativeSelect id={`${id}-channel`} value={channel} onChange={event => setChannel(event.target.value)}>{catalog.length ? catalog.map(item => <NativeSelectOption key={item.name} value={item.name}>{item.label}{item.status !== 'AVAILABLE' ? ' · 기록 없음' : ''}</NativeSelectOption>) : <NativeSelectOption value="TyresPressure">타이어 압력</NativeSelectOption>}</NativeSelect></Field><LmuFeedback error={resource.error || comparison.error} loading={resource.loading || comparison.loading} />{vehicle && <><p>{vehicle.reason || `표시 단위: ${vehicle.channel?.unit || '확인 필요'}`}</p><Table><TableHeader><TableRow><TableHead>기록 성분</TableHead><TableHead>최솟값</TableHead><TableHead>평균</TableHead><TableHead>최댓값</TableHead><TableHead>정상 / 전체 표본</TableHead></TableRow></TableHeader><TableBody>{(vehicle.channel?.components || []).map(item => <TableRow key={item.id}><TableCell>{item.label}</TableCell><TableCell>{number(item.minimum, 2)}</TableCell><TableCell>{number(item.mean, 2)}</TableCell><TableCell>{number(item.maximum, 2)}</TableCell><TableCell>{item.valid_samples} / {item.sample_count}</TableCell></TableRow>)}</TableBody></Table><p>{vehicle.channel?.interpretation_note}</p></>}{delta && <><h3>기준 대비 차량 상태</h3>{delta.reason && <p>{delta.reason}</p>}<Table><TableHeader><TableRow><TableHead>기록 성분</TableHead><TableHead>기준 평균</TableHead><TableHead>내 랩 평균</TableHead><TableHead>차이</TableHead><TableHead>공통 관측 거리 (m)</TableHead></TableRow></TableHeader><TableBody>{(delta.channel?.components || []).map(item => <TableRow key={item.id}><TableCell>{item.label}</TableCell><TableCell>{number(item.reference?.mean, 2)}</TableCell><TableCell>{number(item.target?.mean, 2)}</TableCell><TableCell>{signed(item.delta?.mean, 2)}</TableCell><TableCell>{number(item.supported_distance_m)}</TableCell></TableRow>)}</TableBody></Table><p>{delta.note}</p></>}</CardContent></Card>;
}

export default function LmuAnalysisPanels({ mode = 'summary', onChooseSource }) {
  const resource = useLmuResource(mode === 'summary' && data.filename && data.defaultTarget != null ? `/api/lmu/telemetry/lap-analysis?file_id=${encodeURIComponent(data.filename)}&lap_number=${data.defaultTarget}` : null);
  const report = resource.payload?.analysis;
  if (!data.filename) return <LmuNoSource onChooseSource={onChooseSource} />;
  return <div className="flex flex-col gap-4" aria-label="LMU 심화 분석"><LmuFeedback error={resource.error} loading={resource.loading} />{mode === 'summary' && report && <><Card><CardHeader><CardTitle>제동 분석 · 랩 {report.lap_number}</CardTitle><CardDescription>제동 시작 8% · 해제 5% · 최소 0.15초. 페달 시간 분포의 양쪽 5% 기준과 별개인 구간 감지 기준입니다.</CardDescription></CardHeader><CardContent><ZoneTable zones={report.braking?.zones} braking /></CardContent></Card><Card><CardHeader><CardTitle>풀스로틀 구간</CardTitle><CardDescription>시작 95% · 해제 90% · 최소 0.15초</CardDescription></CardHeader><CardContent><ZoneTable zones={report.full_throttle?.zones} /></CardContent></Card><Card><CardHeader><CardTitle>채널과 관측 상태</CardTitle><CardDescription>값이 없거나 단위를 확인할 수 없는 채널은 측정값을 만들지 않습니다.</CardDescription></CardHeader><CardContent><Table><TableHeader><TableRow><TableHead>채널</TableHead><TableHead>상태</TableHead><TableHead>원본</TableHead><TableHead>입력 처리 단계</TableHead></TableRow></TableHeader><TableBody>{Object.entries(report.channels || {}).map(([key, item]) => <TableRow key={key}><TableCell>{key}</TableCell><TableCell><Badge variant="outline">{item.status === 'AVAILABLE' ? '관측 가능' : item.status === 'PARTIAL' ? '일부 관측' : '확인 필요'}</Badge></TableCell><TableCell>{item.source_name || item.reason || '—'}</TableCell><TableCell>{item.input_stage || '—'}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card></>}{mode === 'expert' && <><VehiclePanel /><LmuConsistencyPanel /></>}</div>;
}
