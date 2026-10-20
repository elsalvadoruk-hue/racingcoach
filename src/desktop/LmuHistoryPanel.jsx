import React, { useEffect, useRef, useState } from 'react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '../components/ui/empty';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { Input } from '../components/ui/input';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '../components/ui/table';
import { Textarea } from '../components/ui/textarea';
import { requestJson } from '../analysis/analysisClient';
import { data, lapTime, rememberSession } from './sessionData';
import { LmuFeedback, lmuPost } from './LmuAnalysisPanels';
import LmuPracticePanel from './LmuPracticePanel';

const emptyHistory = { records: [], total: 0, tracks: [], cars: [], session_types: [] };
const time = value => value ? new Date(value).toLocaleString() : '—';

export default function LmuHistoryPanel({ onChooseSource, onOpenSession, session = data }) {
  const [history, setHistory] = useState(emptyHistory);
  const [filters, setFilters] = useState({ q: '', track: '', car: '', session_type: '' });
  const [note, setNote] = useState(''), [notice, setNotice] = useState('');
  const [error, setError] = useState(''), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const flight = useRef(null);
  useEffect(() => () => flight.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value));
    requestJson(`/api/lmu/history${query.toString() ? `?${query}` : ''}`, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setHistory({ ...emptyHistory, ...result });
    }).catch(e => { if (!controller.signal.aborted) { setHistory(emptyHistory); setError(e.message); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, revision]);

  async function run(record) {
    flight.current?.abort();
    const controller = new AbortController(); flight.current = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      if (record) {
        const result = await lmuPost(`/api/lmu/history/${encodeURIComponent(record.record_id)}/open`, {}, controller.signal);
        if (!controller.signal.aborted) {
          rememberSession(result.session, result.session.defaultTarget, result.session.defaultReference, []);
          onOpenSession?.(result.session);
          setNotice('저장한 파일과 랩으로 분석을 다시 열었습니다.');
        }
      } else {
        await lmuPost('/api/lmu/history', {
          filename: session.filename, target_lap: session.defaultTarget,
          reference_filename: session.reference_filename || session.filename,
          reference_lap: session.defaultReference, note,
        }, controller.signal);
        if (!controller.signal.aborted) { setNotice('현재 분석을 LMU 기록에 저장했습니다. 같은 파일·랩·기준의 기록은 갱신됩니다.'); setRevision(value => value + 1); }
      }
    } catch (e) { if (!controller.signal.aborted) setError(e.message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  const canSave = ['lmu', 'LMU', 'LMU_DUCKDB'].includes(session.provider) && Boolean(session.filename)
    && Number.isInteger(session.defaultTarget) && Number.isInteger(session.defaultReference);
  return <section className="wide-workspace gap-4 overflow-auto" aria-label="LMU 기록">
    <div className="wide-heading"><div><h2>기록</h2><p>저장한 주행 분석과 반복 연습의 결과를 확인합니다.</p></div>{onChooseSource && <Button variant="outline" onClick={onChooseSource}>주행 선택</Button>}</div>
    <LmuFeedback error={error} loading={loading || busy} />
    {notice && <p role="status">{notice}</p>}
    <Card><CardHeader><CardTitle>개인 주행 기록</CardTitle><CardDescription>현재 비교 중인 파일·내 랩·기준 랩과 측정 요약을 저장합니다.</CardDescription></CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p>{session.file_name || (canSave ? session.filename : '분석할 주행을 선택하세요.')} {canSave && `· 내 ${session.defaultTarget}랩 · 기준 ${session.defaultReference}랩`}</p>
        <FieldGroup><Field><FieldLabel htmlFor="lmu-history-note">주행 메모</FieldLabel><Textarea id="lmu-history-note" value={note} maxLength={2000} disabled={busy || !canSave} onChange={event => setNote(event.target.value)} placeholder="다음 주행에서 확인할 점" /></Field></FieldGroup>
        <Button disabled={busy || !canSave} onClick={() => run()}>현재 분석 기록 저장</Button>
      </CardContent><CardFooter><p>기록은 이 PC에 보관합니다. 게임 유효성은 확인 필요로 유지하며 음성·AI 응답을 저장하지 않습니다.</p></CardFooter>
    </Card>
    <Card><CardHeader><CardTitle>주행 분석 이력 <Badge variant="secondary">{history.total}개</Badge></CardTitle><CardDescription>분석 열기는 원본 파일에서 선택한 랩을 다시 계산합니다. 파일이 이동되거나 삭제되면 다시 선택해야 합니다.</CardDescription></CardHeader>
      <CardContent className="flex flex-col gap-4">
        <FieldGroup className="grid md:grid-cols-4">
          <Field><FieldLabel htmlFor="lmu-history-search">기록 검색</FieldLabel><Input id="lmu-history-search" value={filters.q} maxLength={200} onChange={event => setFilters(previous => ({ ...previous, q: event.target.value }))} placeholder="파일·차량·트랙·메모" /></Field>
          {[
            ['track', '트랙', history.tracks], ['car', '차량', history.cars], ['session_type', '세션', history.session_types],
          ].map(([key, label, values]) => <Field key={key}><FieldLabel htmlFor={`lmu-history-${key}`}>{label}</FieldLabel><NativeSelect id={`lmu-history-${key}`} value={filters[key]} onChange={event => setFilters(previous => ({ ...previous, [key]: event.target.value }))}><NativeSelectOption value="">전체</NativeSelectOption>{values.map(value => <NativeSelectOption key={value} value={value}>{value}</NativeSelectOption>)}</NativeSelect></Field>)}
        </FieldGroup>
        {loading ? <p role="status">주행 분석 이력 확인 중…</p> : history.records.length ? <Table><TableHeader><TableRow>{['저장 시각', '차량 · 트랙', '주행 파일', '내 랩 / 기준', '메모', '분석'].map(label => <TableHead key={label}>{label}</TableHead>)}</TableRow></TableHeader><TableBody>{history.records.map(record => <TableRow key={record.record_id}>
          <TableCell>{time(record.updated_at)}<small>{record.summary.session_type || '세션 확인 필요'}</small></TableCell>
          <TableCell>{record.summary.car_name || '차량 확인 필요'} · {record.summary.track_name || '트랙 확인 필요'}<small>측정 {record.summary.measured_lap_count ?? '—'} / 전체 {record.summary.lap_count ?? '—'}랩</small></TableCell>
          <TableCell>{record.summary.file_name || record.selection.filename}<small>기준: {record.summary.reference_file_name || record.selection.reference_filename}</small></TableCell>
          <TableCell>{record.selection.target_lap}랩 · {lapTime(record.summary.target_time_s)}<small>기준 {record.selection.reference_lap}랩 · {lapTime(record.summary.reference_time_s)}</small><Badge variant="outline">유효성 확인 필요</Badge></TableCell>
          <TableCell>{record.note || '—'}</TableCell><TableCell><Button size="sm" variant="outline" disabled={busy || !onOpenSession} onClick={() => run(record)} aria-label={`분석 열기 · ${record.summary.file_name || record.selection.filename} · ${record.selection.target_lap}랩`}>분석 열기</Button></TableCell>
        </TableRow>)}</TableBody></Table> : <Empty><EmptyHeader><EmptyTitle>{Object.values(filters).some(Boolean) ? '조건에 맞는 기록이 없습니다.' : '저장된 주행 분석이 없습니다.'}</EmptyTitle><EmptyDescription>현재 분석 기록을 저장하면 다음 실행에서도 같은 파일과 랩을 다시 열 수 있습니다.</EmptyDescription></EmptyHeader></Empty>}
        {history.total > history.records.length && <p>최근 {history.records.length}개를 표시합니다. 검색과 필터로 범위를 좁히세요.</p>}
        <Button variant="outline" disabled={loading || busy} onClick={() => setRevision(value => value + 1)}>분석 이력 새로고침</Button>
      </CardContent>
    </Card>
    <LmuPracticePanel embedded />
  </section>;
}
