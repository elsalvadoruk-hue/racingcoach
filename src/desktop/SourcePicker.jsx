import React, { useEffect, useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { Badge } from '../components/ui/badge';
import { data, labRequest, lapTime, analysisOptions } from './sessionData';
import { fileLapLabel, lapCounts, lapStatus, isBestLap, lapIsComplete, sessionTypeLabel, sessionTypeTone } from './labPreferences';
import ConnectionControls from './ConnectionControls';

export default function SourcePicker({ target, reference, external, onApply, analysisWorkspace }) {
  const [files, setFiles] = useState([]);
  const [file, setFile] = useState(data.filename || '');
  const [session, setSession] = useState(data);
  const [own, setOwn] = useState(target);
  const [base, setBase] = useState(reference);
  const [mode, setMode] = useState(external.length ? 'GARAGE61' : 'LOCAL');
  const [refs, setRefs] = useState([]);
  const [selected, setSelected] = useState(external);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const lockedReferences = useRef(analysisWorkspace?.activeTrial);
  lockedReferences.current = analysisWorkspace?.activeTrial;
  const workspaceRef = useRef(analysisWorkspace);
  workspaceRef.current = analysisWorkspace;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    labRequest('files', {}, controller.signal).then(p => { setFiles(p.files || []); if (!data.filename) setFile(p.files?.find(f => f.analyzable !== false)?.name || ''); }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!file) { setSession(null); return; }
    let live = true;
    setBusy(true); setError('');
    (async () => {
      const trial = lockedReferences.current;
      const next = file === data.filename ? data : await labRequest('session', { filename: file, preview: true, ...analysisOptions(workspaceRef.current, file),
        ...(trial ? { reference_mode: trial.reference_mode, external_reference_files: trial.reference_files || [] } : {}) });
      const references = await labRequest('references', { filename: file });
      if (!live) return;
      setSession(next); setRefs((references.files || []).filter(r => r.usable !== false && r.compatible !== false));
      setFiles(current => current.map(row => row.name === file ? { ...row, ...lapCounts(next.laps) } : row));
      if (file !== data.filename) {
        setOwn(next.defaultTarget == null ? '' : String(next.defaultTarget)); setBase(String(next.defaultReference ?? ''));
        setSelected(trial?.reference_files || []); setMode(trial?.reference_files?.length ? 'GARAGE61' : 'LOCAL');
      }
    })().catch(e => { if (live) { setError(e.message); setSession(null); } }).finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [file]);
  async function apply() {
    setBusy(true); setError('');
    try {
      const externalFiles = mode === 'GARAGE61' ? selected : [];
      const result = session.kind === 'LIVE_LOCAL' ? await labRequest('comparison', { filename: file, ...analysisOptions(analysisWorkspace, file), target_lap: Number(own), reference_lap: base ? Number(base) : null,
        reference_mode: externalFiles.length > 1 ? 'AGGREGATE' : externalFiles.length ? 'SINGLE' : 'LOCAL', external_reference_files: externalFiles, points: 10000 }) : null;
      if (mounted.current) await onApply(session, own, base, externalFiles, result);
    } catch (e) { if (mounted.current) setError(e.message); } finally { if (mounted.current) setBusy(false); }
  }
  const options = session?.laps || [];
  const ownLap = options.find(l => String(l.lap_number) === own && (l.status === 'VALID' || l.lap_number === session.defaultTarget));
  const best = options.find(l => isBestLap(l, options));
  const allowed = lap => (lap.status === 'VALID' || lap.lap_number === session.defaultTarget) && (session.kind === 'LIVE_LOCAL' || Boolean(session.comparisons?.[lap.lap_number]));
  const label = lap => `${lap.lap_number}랩 · ${lapTime(lap.lap_time_s)} · ${lapStatus(lap)}${isBestLap(lap, options) ? ' · ★ 베스트' : ''}${lap.invalid_reason ? ` · ${lap.invalid_reason}` : ''}`;
  const lapClass = lap => `${sessionTypeTone(session)}-lap-option${(!allowed(lap) || !lapIsComplete(lap)) ? ' lap-option-faint' : ''}${isBestLap(lap, options) ? ' best-lap-option' : ''}`;
  return <div className="source-picker"><FieldGroup>
    <Field><FieldLabel htmlFor="source-file">주행 파일 · IBT</FieldLabel><NativeSelect id="source-file" value={file} onChange={e => setFile(e.target.value)}>
      {!file && <NativeSelectOption value="">IBT 파일을 선택하세요</NativeSelectOption>}
      {data.filename && !files.some(f => f.name === data.filename) && <NativeSelectOption value={data.filename}>{data.filename}</NativeSelectOption>}
      {files.map(f => <NativeSelectOption key={f.name} value={f.name} disabled={f.analyzable === false} className={`${sessionTypeTone(f)}-file-option${f.valid_laps === 0 || f.complete_laps === 0 ? ' file-option-faint' : ''}`}>{f.track_name || f.name} · {fileLapLabel(f)} · {sessionTypeLabel(f)} · {f.car_name || ''} · {f.name}</NativeSelectOption>)}
    </NativeSelect><div className="source-lap-summary" data-testid="source-lap-summary">{busy ? '랩 상태 확인 중…' : session && <><Badge variant="secondary">{fileLapLabel(lapCounts(options))}</Badge><Badge variant="outline" className={`session-badge session-${sessionTypeTone(session)}`}>{sessionTypeLabel(session)}</Badge><span>완주 {lapCounts(options).complete_laps}랩</span>{best && <Badge variant="outline" className="best-lap">★ 베스트 {best.lap_number}랩 · {lapTime(best.lap_time_s)}</Badge>}</>}</div></Field>
    <Field><FieldLabel htmlFor="target-lap">내 주행</FieldLabel><NativeSelect id="target-lap" className={isBestLap(ownLap, options) ? 'best-lap-select' : ''} value={own} disabled={busy} onChange={e => setOwn(e.target.value)}>{options.map((l, index) => <NativeSelectOption key={`${l.lap_number}-${l.status}-${index}`} value={String(l.lap_number)} disabled={!allowed(l)} className={lapClass(l)}>{label(l)}</NativeSelectOption>)}</NativeSelect><p>{ownLap ? `${lapStatus(ownLap)} 랩${!lapIsComplete(ownLap) ? ' · 완주 랩이 아니며 관측된 정상 구간만 비교합니다.' : ownLap.status !== 'VALID' ? ' · 관측된 정상 구간만 비교합니다.' : ''}` : '비교할 수 있는 랩을 선택하세요.'} · 연한 표시는 완주 유효 랩이 아니거나 비교 자료가 없는 랩입니다.</p></Field>
    <Field><FieldLabel htmlFor="reference-mode">기준 종류</FieldLabel><NativeSelect id="reference-mode" value={mode} onChange={e => setMode(e.target.value)} disabled={busy || Boolean(analysisWorkspace?.activeTrial)}><NativeSelectOption value="LOCAL">내 기준 랩</NativeSelectOption><NativeSelectOption value="GARAGE61">Garage61 · 내려받은 기준</NativeSelectOption></NativeSelect></Field>
    {mode === 'LOCAL' ? <Field><FieldLabel htmlFor="reference-lap">비교 기준</FieldLabel><NativeSelect id="reference-lap" className={isBestLap(options.find(l => String(l.lap_number) === base), options) ? 'best-lap-select' : ''} value={base} onChange={e => setBase(e.target.value)} disabled={busy || Boolean(analysisWorkspace?.activeTrial)}>{options.map((l, index) => <NativeSelectOption key={`${l.lap_number}-${l.status}-${index}`} value={String(l.lap_number)} disabled={l.status !== 'VALID' || !allowed(l)} className={lapClass(l)}>{label(l)}</NativeSelectOption>)}</NativeSelect></Field> : <Field><FieldLabel htmlFor="reference-files">Garage61 기준 · Ctrl 키로 여러 랩 선택</FieldLabel><select id="reference-files" multiple size={5} value={selected} onChange={e => setSelected([...e.target.selectedOptions].map(o => o.value))} disabled={busy || Boolean(analysisWorkspace?.activeTrial)}>{refs.map(r => <option key={r.name} value={r.name}>{r.driver_name || '드라이버'} · {lapTime(r.lap_time_s)} · {r.car_name}</option>)}</select><p>1개: 실주행 비교 · 여러 개: 기존 조건 가중 종합 기준. 선택 {selected.length}개</p>{!refs.length && <p>이 차량·트랙과 맞는 내려받은 기준이 없습니다.</p>}</Field>}
  </FieldGroup><p className="dialog-copy">IBT 파일을 바꾸거나 Garage61 기준을 선택하면 지도·구간 수치가 함께 바뀝니다. 전문가 보기에서는 같은 트랙의 다른 IBT 랩도 겹칠 수 있습니다.</p>
    {error && <p role="alert" className="loss">{error}</p>}
    <Button disabled={busy || !session || !own || (mode === 'GARAGE61' && !selected.length)} onClick={apply}>{busy ? '주행 데이터 계산 중…' : '선택한 랩 비교하기'}</Button>
    {analysisWorkspace && <ConnectionControls workspace={analysisWorkspace} />}
  </div>;
}
