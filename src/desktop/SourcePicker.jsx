import React, { useEffect, useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { Badge } from '../components/ui/badge';
import { data, labRequest, lapTime, analysisOptions, measurableLap } from './sessionData';
import { fileLapLabel, fileChoiceLabel, fileBestLap, lapCounts, lapReason, lapStatus, isBestLap, lapIsComplete, sessionTypeLabel, sessionTypeTone } from './labPreferences';
import ConnectionControls from './ConnectionControls';
import { requestJson } from '../analysis/analysisClient';
import { AnalysisProgressBar, useAnalysisProgress } from './AnalysisProgress';

// The server returns codes; the driver reads a sentence.
const REFERENCE_REASONS = {
  NO_TARGET_LAP: '비교할 내 랩이 없어 자동 기준을 찾지 못했습니다.',
  NO_COMPATIBLE_PERSONAL_LAP: '같은 차량·트랙·레이아웃에서 유효하게 완주한 랩이 없어 자동 기준이 없습니다.',
};
const referenceReason = reason => REFERENCE_REASONS[reason] || '조건이 맞는 내 기준 랩이 없습니다.';
// A downloaded recording is used like any other, but it is not the driver's own lap.
const importedLabel = item => (item?.imported ? ` · 가져온 기록${item.driver_name ? ` (${item.driver_name})` : ''}` : '');

export default function SourcePicker({ target, reference, external, onApply, analysisWorkspace }) {
  const lmu = analysisWorkspace?.game === 'lmu';
  const [files, setFiles] = useState([]);
  const [file, setFile] = useState(data.filename || '');
  const [session, setSession] = useState(data);
  const [own, setOwn] = useState(target);
  const [base, setBase] = useState(reference);
  const [referenceFile, setReferenceFile] = useState(data.reference_filename || data.filename || '');
  const [referenceLaps, setReferenceLaps] = useState(data.reference_laps || []);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [referenceError, setReferenceError] = useState('');
  const [referenceSelection, setReferenceSelection] = useState(data.reference_selection || (data.personalReference ? 'AUTO' : 'MANUAL'));
  const [personalReference, setPersonalReference] = useState(data.personalReference || null);
  const [referenceNotice, setReferenceNotice] = useState('');
  const [personalBusy, setPersonalBusy] = useState(false);
  const [mode, setMode] = useState(external.length ? 'GARAGE61' : 'LOCAL');
  const [refs, setRefs] = useState([]);
  const [selected, setSelected] = useState(external);
  const [busy, setBusy] = useState(false);
  const { view: analysisProgress, begin: beginProgress, end: endProgress } = useAnalysisProgress();
  const [error, setError] = useState('');
  const [importBusy, setImportBusy] = useState(false);
  const [importNotice, setImportNotice] = useState('');
  const [importError, setImportError] = useState('');
  const fileInput = useRef(null);
  const importTarget = useRef('reference');
  const importController = useRef(null);
  const mounted = useRef(true);
  const lockedReferences = useRef(analysisWorkspace?.activeTrial);
  lockedReferences.current = analysisWorkspace?.activeTrial;
  const workspaceRef = useRef(analysisWorkspace);
  workspaceRef.current = analysisWorkspace;
  const pauseAutomaticSelection = analysisWorkspace?.pauseAutomaticSelection;
  useEffect(() => pauseAutomaticSelection?.(), [pauseAutomaticSelection]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => () => importController.current?.abort(), [lmu]);
  useEffect(() => {
    const controller = new AbortController();
    labRequest('files', {}, controller.signal).then(p => { setFiles(current => [...(p.files || []), ...current.filter(row => !(p.files || []).some(item => item.name === row.name))]); if (!data.filename) setFile(current => current || p.files?.find(f => f.analyzable !== false)?.name || ''); }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!file) { setSession(null); return; }
    let live = true;
    let progressId = null;
    setBusy(true); setError('');
    (async () => {
      const trial = lockedReferences.current;
      if (file !== data.filename) progressId = beginProgress();
      const next = file === data.filename ? data : await labRequest('session', { filename: file, preview: true, progress_id: progressId, ...analysisOptions(workspaceRef.current, file),
        ...(trial ? { reference_mode: trial.reference_mode, external_reference_files: trial.reference_files || [] } : {}) });
      let references;
      try { references = await labRequest('references', { filename: file, target_lap: next.defaultTarget }); }
      catch (error) { references = { files: [], personalReference: next.personalReference, unavailable: error.message }; }
      if (!live) return;
      const available = (references.files || []).filter(r => r.usable !== false && r.compatible !== false);
      const personal = references.personalReference || next.personalReference;
      setSession(next); setRefs(available); setPersonalReference(personal || null);
      const missingExternal = !trial && external.length && !external.some(name => available.some(row => row.name === name));
      setReferenceNotice(references.unavailable || missingExternal ? 'Garage61 기준을 사용할 수 없어 조건이 비슷한 내 베스트 랩을 사용합니다.' : '');
      if (missingExternal) { setMode('LOCAL'); setSelected([]); }
      if (personal && (file !== data.filename || data.reference_selection !== 'MANUAL') && !trial) {
        setReferenceSelection('AUTO');
        setReferenceFile(personal.filename || next.defaultReferenceFile || file);
        setBase(String(personal.lap ?? personal.lap_number ?? next.defaultReference ?? ''));
      }
      setFiles(current => current.map(row => row.name === file ? { ...row, ...lapCounts(next.laps), best_lap_s: fileBestLap(next.laps), recording_time: next.session?.recording_time || row.recording_time } : row));
      if (file !== data.filename) {
        setOwn(next.defaultTarget == null ? '' : String(next.defaultTarget));
        if (!personal) setBase(String(next.defaultReference ?? ''));
        setSelected(trial?.reference_files || []); setMode(trial?.reference_files?.length ? 'GARAGE61' : 'LOCAL');
      }
    })().catch(e => { if (live) { setError(e.message); setSession(null); } }).finally(() => { if (progressId) endProgress(progressId); if (live) setBusy(false); });
    return () => { live = false; if (progressId) endProgress(progressId); };
  }, [file, external, beginProgress, endProgress]);
  useEffect(() => {
    if (!referenceFile || referenceFile === file) { setReferenceBusy(false); setReferenceError(''); return undefined; }
    const controller = new AbortController();
    setReferenceBusy(true); setReferenceError(''); setReferenceLaps([]);
    labRequest('file-laps', { filename: referenceFile }, controller.signal).then(next => {
      if (controller.signal.aborted) return;
      const available = next.laps || [];
      setReferenceLaps(available);
      setBase(current => available.some(l => String(l.lap_number) === current && measurableLap(l))
        ? current : String(available.filter(measurableLap).at(-1)?.lap_number ?? ''));
    }).catch(e => { if (!controller.signal.aborted) setReferenceError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setReferenceBusy(false); });
    return () => controller.abort();
  }, [file, referenceFile]);
  useEffect(() => {
    if (referenceSelection !== 'AUTO' || !own || !session || session.filename !== file) return undefined;
    const controller = new AbortController();
    setPersonalBusy(true);
    labRequest('references', { filename: file, target_lap: Number(own) }, controller.signal).then(next => {
      if (controller.signal.aborted || !next.personalReference) return;
      setPersonalReference(next.personalReference);
      setReferenceFile(next.personalReference.filename || file);
      setBase(String(next.personalReference.lap ?? next.personalReference.lap_number ?? ''));
    }).catch(error => { if (!controller.signal.aborted) setReferenceNotice(error.message); })
      .finally(() => { if (!controller.signal.aborted) setPersonalBusy(false); });
    return () => { controller.abort(); setPersonalBusy(false); };
  }, [file, own, referenceSelection, session]);
  function chooseAutomatic() {
    setImportError('');
    setReferenceSelection('AUTO');
    if (personalReference) {
      setReferenceFile(personalReference.filename || file);
      setBase(String(personalReference.lap ?? personalReference.lap_number ?? ''));
    }
  }
  function openDuckdb(target) {
    importTarget.current = target;
    fileInput.current?.click();
  }
  async function importDuckdb(event) {
    const selectedFile = event.target.files?.[0];
    event.target.value = '';
    if (!selectedFile) return;
    if (!selectedFile.name.toLowerCase().endsWith('.duckdb')) {
      setImportError('완료된 LMU .duckdb 파일을 선택하세요. 선택한 파일은 적용되지 않았습니다.'); return;
    }
    const destination = importTarget.current;
    const controller = new AbortController();
    importController.current = controller;
    setImportBusy(true); setError(''); setImportError(''); setImportNotice('');
    try {
      const result = await requestJson(`/api/lmu/telemetry/import?filename=${encodeURIComponent(selectedFile.name)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: selectedFile,
        signal: controller.signal, timeoutMs: 600000,
      });
      if (!mounted.current || controller.signal.aborted) return;
      const imported = result.file;
      setFiles(current => [...current.filter(row => row.name !== imported.name), imported]);
      if (destination === 'source') {
        setFile(imported.name); setReferenceFile(imported.name); setReferenceLaps([]);
      } else {
        setMode('LOCAL'); setReferenceSelection('MANUAL'); setReferenceFile(imported.name); setBase('');
      }
      setImportNotice(`${selectedFile.name} 파일을 가져왔습니다. ${destination === 'source' ? '주행' : '기준'} 랩을 선택하세요.`);
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) {
        const reason = error.status === 404
          ? '실행 중인 분석 서버가 이전 버전입니다. 실행.bat 창을 닫고 다시 실행한 뒤 파일을 다시 선택하세요.'
          : error.message;
        setImportError(`${selectedFile.name} 파일을 가져오지 못해 적용하지 않았습니다. ${reason}`);
      }
    } finally {
      if (mounted.current) setImportBusy(false);
      if (importController.current === controller) importController.current = null;
    }
  }
  async function apply() {
    if (importBusy || importError) return;
    setBusy(true); setError('');
    const progressId = session.kind === 'LIVE_LOCAL' ? beginProgress() : null;
    try {
      let externalFiles = mode === 'GARAGE61' ? selected : [];
      const result = session.kind === 'LIVE_LOCAL' ? await labRequest('comparison', { filename: file, progress_id: progressId, ...analysisOptions(analysisWorkspace, file), target_lap: Number(own), reference_lap: base ? Number(base) : null,
        reference_filename: referenceFile || file, reference_selection: referenceSelection,
        reference_mode: externalFiles.length > 1 ? 'AGGREGATE' : externalFiles.length ? 'SINGLE' : 'LOCAL', external_reference_files: externalFiles, points: 10000 }) : null;
      if (result?.referenceFallback) externalFiles = [];
      if (mounted.current) await onApply({ ...session, reference_filename: result?.reference_filename || referenceFile || file,
        reference_selection: result?.referenceFallback ? 'AUTO' : referenceSelection,
        referenceFallback: result?.referenceFallback, personalReference: result?.personalReference || personalReference,
        reference_file_name: files.find(f => f.name === referenceFile)?.file_name || session.file_name,
        reference_laps: baseOptions }, own, String(result?.reference_lap ?? base), externalFiles, result);
    } catch (e) { if (mounted.current) setError(e.message); } finally { if (progressId) endProgress(progressId); if (mounted.current) setBusy(false); }
  }
  const options = session?.laps || [];
  const baseOptions = referenceFile && referenceFile !== file ? referenceLaps : options;
  const ownLap = options.find(l => String(l.lap_number) === own && (measurableLap(l) || l.lap_number === session.defaultTarget));
  const best = options.find(l => isBestLap(l, options));
  const allowed = lap => (measurableLap(lap) || lap.lap_number === session.defaultTarget) && (session.kind === 'LIVE_LOCAL' || Boolean(session.comparisons?.[lap.lap_number]));
  const label = lap => `${lap.lap_number}랩 · ${lapTime(lap.lap_time_s)} · ${lapStatus(lap)}${isBestLap(lap, options) ? ' · ★ 베스트' : ''}${lap.invalid_reason ? ` · ${lapReason(lap.invalid_reason)}` : ''}`;
  const lapClass = lap => `${sessionTypeTone(session)}-lap-option${(!allowed(lap) || !lapIsComplete(lap)) ? ' lap-option-faint' : ''}${isBestLap(lap, options) ? ' best-lap-option' : ''}`;
  return <div className="source-picker">{lmu && <input ref={fileInput} type="file" accept=".duckdb" aria-label="열 DuckDB 파일" hidden onChange={importDuckdb} />}<FieldGroup>
    <Field><FieldLabel htmlFor="source-file">주행 파일 · {lmu ? 'LMU DuckDB' : 'IBT'}</FieldLabel><NativeSelect id="source-file" disabled={importBusy} value={file} onChange={e => { setImportError(''); setFile(e.target.value); setReferenceFile(e.target.value); setReferenceLaps([]); }}>
      {!file && <NativeSelectOption value="">{lmu ? 'LMU 파일을 선택하세요' : 'IBT 파일을 선택하세요'}</NativeSelectOption>}
      {data.filename && !files.some(f => f.name === data.filename) && <NativeSelectOption value={data.filename} title={data.file_name || data.filename}>{fileChoiceLabel({ ...data.session, ...lapCounts(data.laps), name: data.filename, file_name: data.file_name, best_lap_s: fileBestLap(data.laps) })}</NativeSelectOption>}
      {files.map(f => <NativeSelectOption key={f.name} value={f.name} title={f.display_name || f.file_name || f.name} disabled={f.analyzable === false} className={`${sessionTypeTone(f)}-file-option${f.valid_laps === 0 || f.complete_laps === 0 ? ' file-option-faint' : ''}`}>{fileChoiceLabel(f)}</NativeSelectOption>)}
    </NativeSelect>{lmu && <Button variant="outline" disabled={importBusy || busy || Boolean(analysisWorkspace?.activeTrial)} onClick={() => openDuckdb('source')}>DuckDB 파일 열기…</Button>}<div className="source-lap-summary" data-testid="source-lap-summary">{busy ? '랩 상태 확인 중…' : session && <><Badge variant="secondary">{fileLapLabel(lapCounts(options))}</Badge><Badge variant="outline" className={`session-badge session-${sessionTypeTone(session)}`}>{sessionTypeLabel(session)}</Badge><span>완주 {lapCounts(options).complete_laps}랩</span>{best && <Badge variant="outline" className="best-lap">★ 베스트 {best.lap_number}랩 · {lapTime(best.lap_time_s)}</Badge>}</>}</div></Field>
    <Field><FieldLabel htmlFor="target-lap">내 주행</FieldLabel><NativeSelect id="target-lap" className={isBestLap(ownLap, options) ? 'best-lap-select' : ''} value={own} disabled={importBusy || busy} onChange={e => setOwn(e.target.value)}>{options.map((l, index) => <NativeSelectOption key={`${l.lap_number}-${l.status}-${index}`} value={String(l.lap_number)} disabled={!allowed(l)} className={lapClass(l)}>{label(l)}</NativeSelectOption>)}</NativeSelect><p>{ownLap ? `${lapStatus(ownLap)} 랩${!lapIsComplete(ownLap) ? ' · 완주 랩이 아니며 관측된 정상 구간만 비교합니다.' : ownLap.measurement_eligible ? ' · 기록된 측정값을 비교하며 게임 유효성은 확인 필요입니다.' : ownLap.status !== 'VALID' ? ' · 관측된 정상 구간만 비교합니다.' : ''}` : '비교할 수 있는 랩을 선택하세요.'} · 연한 표시는 완주 유효 랩이 아니거나 비교 자료가 없는 랩입니다.</p></Field>
    <Field><FieldLabel htmlFor="reference-mode">기준 종류</FieldLabel><NativeSelect id="reference-mode" value={mode} onChange={e => { setMode(e.target.value); if (e.target.value === 'LOCAL') chooseAutomatic(); }} disabled={importBusy || busy || Boolean(analysisWorkspace?.activeTrial)}><NativeSelectOption value="LOCAL">내 기준 랩</NativeSelectOption><NativeSelectOption disabled={lmu || !refs.length} value="GARAGE61">Garage61 · 내려받은 기준</NativeSelectOption></NativeSelect></Field>
    {mode === 'LOCAL' && <Field><FieldLabel htmlFor="reference-selection">기준 선택 방식</FieldLabel><NativeSelect id="reference-selection" value={referenceSelection} disabled={importBusy || busy || Boolean(analysisWorkspace?.activeTrial)} onChange={e => { if (e.target.value === 'AUTO') chooseAutomatic(); else setReferenceSelection('MANUAL'); }}><NativeSelectOption value="AUTO">{lmu ? '비슷한 조건의 가장 빠른 랩 · 자동 (가져온 기록 포함)' : '비슷한 조건의 내 베스트 랩 · 자동'}</NativeSelectOption><NativeSelectOption value="MANUAL">파일·랩 직접 선택</NativeSelectOption></NativeSelect><p>{lmu ? '같은 차량·트랙·레이아웃의 가장 빠른 기록을 다른 파일까지 찾아 사용합니다. 비 여부만 크게 구분하고 온도·타이어 차이는 제한하지 않습니다. 강수 정보가 없으면 건조·젖음 정보로 구분하며 날씨 미상 기록도 허용합니다.' : '같은 차량·트랙·레이아웃에서 조건이 비슷한 가장 빠른 기록을 다른 파일까지 찾아 사용합니다. 젖은 노면과 마른 노면은 섞지 않습니다.'}</p>{referenceSelection === 'AUTO' && personalReference && <p role="status">{personalReference.filename ? `자동 기준: ${personalReference.file_name || files.find(f => f.name === personalReference.filename)?.file_name || personalReference.filename} · ${personalReference.lap ?? personalReference.lap_number}랩 · ${lapTime(personalReference.lap_time_s)}${importedLabel(personalReference)}` : referenceReason(personalReference.reason)}{personalReference.filename === file && String(personalReference.lap ?? personalReference.lap_number) === own ? ' · 선택한 내 주행이 베스트 랩입니다.' : ''}</p>}</Field>}
    {mode === 'LOCAL' && <Field><FieldLabel htmlFor="lmu-reference-file">기준 주행 파일 · {lmu ? 'LMU' : 'IBT'}</FieldLabel><NativeSelect id="lmu-reference-file" value={referenceFile || file} onChange={e => { setImportError(''); setReferenceSelection('MANUAL'); setReferenceFile(e.target.value); }} disabled={importBusy || busy || referenceSelection === 'AUTO' || Boolean(analysisWorkspace?.activeTrial)}>{referenceFile && !files.some(f => f.name === referenceFile) && <NativeSelectOption value={referenceFile} title={referenceFile}>{fileChoiceLabel({ name: referenceFile })}</NativeSelectOption>}{files.filter(f => f.analyzable !== false).map(f => <NativeSelectOption key={f.name} value={f.name}>{fileChoiceLabel(f)}{f.name === file ? ' · 같은 파일' : ''}{importedLabel(f)}</NativeSelectOption>)}</NativeSelect>{lmu && <Button variant="outline" disabled={importBusy || busy || Boolean(analysisWorkspace?.activeTrial)} onClick={() => openDuckdb('reference')}>기준 DuckDB 파일 열기…</Button>}<p>같은 차량·트랙·레이아웃의 다른 기록도 기준으로 선택할 수 있습니다.</p></Field>}
    {mode === 'LOCAL' ? <Field><FieldLabel htmlFor="reference-lap">비교 기준</FieldLabel><NativeSelect id="reference-lap" className={isBestLap(baseOptions.find(l => String(l.lap_number) === base), baseOptions) ? 'best-lap-select' : ''} value={base} onChange={e => { setReferenceSelection('MANUAL'); setBase(e.target.value); }} disabled={importBusy || busy || referenceBusy || referenceSelection === 'AUTO' || Boolean(analysisWorkspace?.activeTrial)}>{baseOptions.map((l, index) => <NativeSelectOption key={`${l.lap_number}-${l.status}-${index}`} value={String(l.lap_number)} disabled={!measurableLap(l)} className={!measurableLap(l) ? 'lap-option-faint' : undefined}>{label(l)}</NativeSelectOption>)}</NativeSelect>{referenceBusy && <p role="status">기준 파일의 랩 확인 중…</p>}</Field> : <Field><FieldLabel htmlFor="reference-files">Garage61 기준 · Ctrl 키로 여러 랩 선택</FieldLabel><select id="reference-files" multiple size={5} value={selected} onChange={e => setSelected([...e.target.selectedOptions].map(o => o.value))} disabled={importBusy || busy || Boolean(analysisWorkspace?.activeTrial)}>{refs.map(r => <option key={r.name} value={r.name}>{r.driver_name || '드라이버'} · {lapTime(r.lap_time_s)} · {r.car_name}</option>)}</select><p>1개: 실주행 비교 · 여러 개: 기존 조건 가중 종합 기준. 선택 {selected.length}개</p>{!refs.length && <p>이 차량·트랙과 맞는 내려받은 기준이 없습니다.</p>}</Field>}
  </FieldGroup><p className="dialog-copy">{lmu ? '기록된 랩타임으로 비교합니다. 파일에 별도의 유효·무효 판정값은 없어 게임 판정은 확인 필요로 표시합니다. 날씨·타이어 조건은 확인 필요이며 셋업 효과로 단정하지 않습니다.' : 'IBT 파일을 바꾸거나 Garage61 기준을 선택하면 지도·구간 수치가 함께 바뀝니다. 전문가 보기에서는 같은 트랙의 다른 IBT 랩도 겹칠 수 있습니다.'}</p>
    {referenceSelection === 'AUTO' && personalReference?.conditions?.warnings?.includes('SURFACE_CONDITION_UNKNOWN') && <p role="status">{lmu ? '날씨 정보가 없는 기록도 다른 파일까지 찾아 비교 기준으로 사용합니다. 강수·노면 조건은 확인 필요입니다.' : '노면 상태를 확인할 수 없어 같은 파일에서만 자동 기준을 선택했습니다.'}</p>}
    {lmu && <p className="dialog-copy">다운로드한 .duckdb도 열 수 있습니다. 파일 선택 창에서 고르면 앱에 복사본을 보관합니다. 원본은 그대로 남습니다.</p>}
    {importBusy && <p role="status">DuckDB 파일을 가져오는 중…</p>}
    {importNotice && <p role="status">{importNotice}</p>}
    {importError && <p role="alert" className="loss">{importError}</p>}
    {error && <p role="alert" className="loss">{error}</p>}
    {referenceError && <p role="alert">{referenceError}</p>}
    {referenceNotice && <p role="status">{referenceNotice}</p>}
    <Button disabled={importBusy || Boolean(importError) || busy || referenceBusy || personalBusy || Boolean(referenceError) || !session || !own || (mode === 'LOCAL' && !baseOptions.some(l => String(l.lap_number) === base && measurableLap(l))) || (mode === 'GARAGE61' && !selected.length)} onClick={apply}>{busy ? '주행 데이터 계산 중…' : '선택한 랩 비교하기'}</Button>
    <AnalysisProgressBar view={analysisProgress} />
    {analysisWorkspace && !lmu && <ConnectionControls workspace={analysisWorkspace} />}
  </div>;
}
