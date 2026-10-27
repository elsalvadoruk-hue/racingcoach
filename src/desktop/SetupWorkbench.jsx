import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, FlaskConical, Info, RotateCcw, Save } from 'lucide-react';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Field, FieldGroup, FieldLabel, FieldDescription } from '../components/ui/field';
import { Input } from '../components/ui/input';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Textarea } from '../components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { Table, TableHeader, TableBody, TableRow, TableCell, TableHead } from '../components/ui/table';
import { data, labRequest, signed, number } from './sessionData';
import { isSelectableRecording } from '../lib/recordingFiles';
import { SETUP_KEY, phases, speeds, symptoms, goals, decisions, settingLabels, labelFor, contextKey,
  emptyDraft, emptyFeedback, readSetupWorkflow, canPrepare, finishSetupTrial, nextBaseline, retryTrial, patchTrial,
  hydrateSetupWorkflow, toProductionTrial } from './setupWorkflow';

const effectLabels = { KEEP: 'B 개선 관측', REVERT: '악화 관측', INCONCLUSIVE: '효과 미확정' };
const axisLabels = { IMPROVED: '개선', WORSENED: '악화', NEUTRAL: '비슷함', UNAVAILABLE: '자료 부족', INCONCLUSIVE: '미확정' };
const shortFile = name => name?.replace(/\.ibt$/i, '') || '주행 선택 필요';
const formatValue = value => Number.isFinite(Number(value)) && value !== '' && value != null ? String(Number(value)) : '차고에서 확인';

function Choice({ title, value, onChange, options, disabled, className }) {
  return <Field data-disabled={disabled}><FieldLabel>{title}</FieldLabel><ToggleGroup className={className} type="single" variant="outline" value={value} onValueChange={next => next && onChange(next)} aria-label={title} disabled={disabled}>{options.map(([key, label]) => <ToggleGroupItem key={key} value={key}>{label}</ToggleGroupItem>)}</ToggleGroup></Field>;
}

function Findings({ comparison }) {
  return <div className="setup-findings" data-testid="setup-findings">
    <div className="setup-result-title"><strong>{effectLabels[comparison.verdict] || '효과 미확정'}</strong><Badge variant="outline">{comparison.effect_confirmed ? '반복 측정 확인' : '추가 확인 필요'}</Badge></div>
    <div className="setup-result-metrics"><div><span>중앙 랩타임 · B−A</span><strong>{signed(comparison.median_delta_s)}<small> s</small></strong></div><div><span>유효 랩 · A / B{comparison.minimum_valid_laps_per_file ? ` · 각 ${comparison.minimum_valid_laps_per_file}랩 필요` : ''}</span><strong>{comparison.baseline?.valid_laps ?? '—'} / {comparison.candidate?.valid_laps ?? '—'}</strong></div></div>
    <dl className="setup-axes">{[['pace', '페이스'], ['consistency', '일관성'], ['stability', '완주 안정성']].map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{axisLabels[comparison.performance_axes?.[key]?.status] || '자료 부족'}</dd></div>)}</dl>
    <p>{comparison.reason}</p>
    {comparison.memory_record?.saved === false && <p>장기 기록 미저장 · {comparison.memory_record.reason || '두 주행의 저장 유형과 조건을 확인하세요.'}</p>}
    {comparison.memory_record?.saved === true && <p>측정 비교를 장기 기록에 저장했습니다. 체감과 셋업 효과는 별도로 관리합니다.</p>}
    <div className="setup-condition-check"><span>조건 비교</span><strong>{comparison.conditions_quality?.status === 'MATCHED' ? '조건 일치' : '차이·누락 확인 필요'}</strong><span>계획 항목·값</span><strong>{comparison.plan_matches ? '실제 값 일치' : '불일치·확인 필요'}</strong></div>
    {comparison.conditions_quality?.reasons?.length > 0 && <p>{comparison.conditions_quality.reasons.join(' · ')}</p>}
    {!comparison.plan_matches && <p>IBT의 실제 변경 · {comparison.canonical_setup_changes?.length
      ? comparison.canonical_setup_changes.map(change => `${settingLabels[change.setting] || change.label || change.setting} ${formatValue(change.baseline_value)} → ${formatValue(change.candidate_value)}`).join(' / ')
      : '비교할 수 있는 변경 항목·값을 확인하지 못했습니다.'}</p>}
    {comparison.learning_eligibility?.confounded && <p>다른 항목도 바뀌었습니다. 원래 값으로 맞춰 한 항목만 다시 시험하세요.</p>}
  </div>;
}

export default function SetupWorkbench({ onChooseSource, onCorner, analysisWorkspace }) {
  const context = contextKey(data.session);
  const shared = analysisWorkspace?.workflow;
  const restore = () => hydrateSetupWorkflow(readSetupWorkflow(localStorage), shared);
  const [workflow, setWorkflow] = useState(restore);
  const [draft, setDraft] = useState(() => {
    const saved = restore();
    return { ...emptyDraft(data.filename), ...(saved.active?.issue || saved.drafts[context] || {}) };
  });
  const [files, setFiles] = useState([]), [facts, setFacts] = useState(null), [plan, setPlan] = useState(null);
  const selectableFile = filename => isSelectableRecording(files.find(file => file.name === filename) || {});
  const draftSelectable = selectableFile(draft.filename);
  const [busy, setBusy] = useState(false), [comparing, setComparing] = useState(false), [error, setError] = useState('');
  const [original, setOriginal] = useState(''), [target, setTarget] = useState('');
  const [decision, setDecision] = useState('RETEST'), [dialog, setDialog] = useState(null), [storageError, setStorageError] = useState('');
  const active = workflow.active;
  const current = useRef(workflow), comparisonFlight = useRef(null), reviewPane = useRef(null);
  current.current = workflow;
  const changeWorkflow = transform => {
    const previous = current.current;
    const next = transform(previous);
    if (next === previous) return;
    current.current = next; setWorkflow(next);
    if (!shared) return;
    const completed = next.history.find(trial => trial.id === previous.active?.id && trial.completed_at);
    if (previous.active && completed && next.active?.id !== previous.active.id) {
      shared.completeTrial(toProductionTrial(completed));
    }
    if (next.active) {
      const converted = toProductionTrial(next.active);
      if (next.active.id === previous.active?.id && !completed) shared.patchTrial(converted);
      else shared.startTrial(converted);
    }
  };
  useEffect(() => {
    if (!shared) return;
    setWorkflow(state => {
      const next = hydrateSetupWorkflow(state, shared);
      if (JSON.stringify([state.active, state.history]) === JSON.stringify([next.active, next.history])) return state;
      current.current = next; return next;
    });
    // Draft/preference updates must not replace the driver's current selections.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shared?.activeTrial, shared?.history]);
  useEffect(() => {
    try { localStorage.setItem(SETUP_KEY, JSON.stringify(workflow)); setStorageError(''); }
    catch { setStorageError('브라우저에 저장하지 못했습니다. 현재 작업은 유지되지만 새로고침 후 복원할 수 없습니다.'); }
  }, [workflow]);
  useEffect(() => {
    const controller = new AbortController();
    labRequest('files', {}, controller.signal).then(result => setFiles(result.files || [])).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  const query = JSON.stringify([draft.filename, draft.phase, draft.speed, draft.symptom, draft.corner_id]);
  useEffect(() => {
    const controller = new AbortController();
    setPlan(null); setOriginal(''); setTarget(''); setError('');
    if (!draft.filename || !draftSelectable) { setFacts(null); setBusy(false); return () => controller.abort(); }
    setBusy(true);
    const timer = setTimeout(() => {
      labRequest('setup-plan', { filename: draft.filename, phase: draft.phase, speed: draft.speed,
        symptom: draft.symptom, corner_id: draft.corner_id }, controller.signal).then(result => {
        if (controller.signal.aborted) return;
        setFacts(result); setPlan(result);
      }).catch(e => { if (!controller.signal.aborted) { setFacts(null); setError(e.message); } })
        .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
    // Only structured selections affect the existing rules. Free text stays in the log.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, draftSelectable]);
  useEffect(() => {
    setOriginal(draft.manual_setting ? plan?.setup?.[draft.manual_setting] ?? '' : plan?.candidate?.original_value ?? '');
    const setting = draft.manual_setting || plan?.candidate?.setting;
    setTarget(setting && draft.target_setting === setting && typeof draft.target_value === 'string' ? draft.target_value
      : draft.manual_setting ? '' : plan?.candidate?.value ?? '');
    // Restore a typed target once its measured setup is available. Typing itself does not reset the input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, draft.manual_setting]);
  useEffect(() => () => comparisonFlight.current?.abort(), []);
  useEffect(() => {
    const pane = reviewPane.current;
    const result = pane?.querySelector('[data-testid="setup-findings"]');
    if (result) pane.scrollTop += Math.max(0, result.getBoundingClientRect().bottom - pane.getBoundingClientRect().bottom + 12);
  }, [active?.comparison]);

  function patchDraft(patch) {
    if (active) return;
    const next = { ...draft, ...patch };
    if ('filename' in patch) { next.corner_id = ''; next.manual_setting = ''; setFacts(null); }
    if (['filename', 'phase', 'speed', 'symptom', 'corner_id', 'manual_setting'].some(key => key in patch && patch[key] !== draft[key])) {
      delete next.target_setting; delete next.target_value;
      if (!('manual_setting' in patch)) setPlan(null);
    }
    setDraft(next);
    changeWorkflow(state => ({ ...state, drafts: { ...state.drafts, [context]: next } }));
  }
  function prepare() {
    if (active || busy || !draftSelectable || !canPrepare(candidate, original, target, draft)) return;
    const baselineIsCurrent = analysisWorkspace?.analysisSnapshot?.filename === draft.filename;
    const trial = { id: crypto.randomUUID(), started_at: new Date().toISOString(), context: contextKey(plan.session), session: plan.session,
      baseline_file: draft.filename, candidate_file: '', issue: { ...draft },
      adjustment: { ...candidate, original_value: Number(original), value: Number(target) },
      feedback: emptyFeedback(), comparison: null,
      reference_mode: baselineIsCurrent ? analysisWorkspace.analysisSnapshot.referenceMode : 'LOCAL',
      reference_files: baselineIsCurrent ? [...(analysisWorkspace.analysisSnapshot.references || [])] : [],
      target_lap: draft.filename === data.filename ? data.defaultTarget || null : null,
      reference_lap: draft.filename === data.filename ? data.defaultReference || null : null };
    changeWorkflow(state => ({ ...state, active: trial })); setDecision('RETEST');
  }
  function patchActive(patch) {
    comparisonFlight.current?.abort(); setComparing(false);
    changeWorkflow(state => patchTrial(state, patch));
  }
  function patchFeedback(patch) { patchActive({ feedback: { ...active.feedback, ...patch } }); }
  async function refreshFiles() {
    try { const result = await labRequest('files', {}); setFiles(result.files || []); setError(''); }
    catch (e) { setError(e.message); }
  }
  async function compare() {
    if (!active?.candidate_file || !selectableFile(active.baseline_file) || !selectableFile(active.candidate_file) || comparing) return;
    const controller = new AbortController(); comparisonFlight.current?.abort(); comparisonFlight.current = controller;
    const snapshot = JSON.stringify(active);
    setComparing(true); setError('');
    try {
      const result = await labRequest('setup-compare', { baseline_file: active.baseline_file, candidate_file: active.candidate_file,
        goal: active.issue.goal, session_mode: active.session_mode || (active.issue.goal === 'ENDURANCE' ? 'ENDURANCE' : 'BASELINE_3LAP'),
        trial_id: active.id, reference_mode: active.reference_mode || 'LOCAL', reference_files: active.reference_files || [],
        planned_change: { setting: active.adjustment.setting, original_value: active.adjustment.original_value, value: active.adjustment.value },
        driver_feedback: active.feedback.change ? { ...active.feedback, rating: Number(active.feedback.rating) || null } : null }, controller.signal);
      if (!controller.signal.aborted && JSON.stringify(current.current.active) === snapshot) {
        changeWorkflow(state => patchTrial(state, { comparison: result }));
      }
    } catch (e) { if (!controller.signal.aborted) setError(e.message); }
    finally { if (!controller.signal.aborted) setComparing(false); }
  }
  function complete() {
    const next = finishSetupTrial(workflow, decision);
    if (next === workflow) return;
    const filename = nextBaseline(next.history[0]);
    const nextDraft = { ...emptyDraft(filename), goal: draft.goal };
    changeWorkflow(() => ({ ...next, active: decision === 'RETEST' ? retryTrial(next.history[0], crypto.randomUUID()) : null,
      drafts: { ...next.drafts, [context]: nextDraft } }));
    setDraft(decision === 'RETEST' ? { ...next.history[0].issue } : nextDraft); setDialog(null);
  }
  const selectedCorner = facts?.corners.find(c => c.id === draft.corner_id);
  const observation = selectedCorner || facts?.corners.find(c => c.id === plan?.candidate?.corner_id);
  const candidate = active?.adjustment || (draft.manual_setting && plan?.setup?.[draft.manual_setting] != null ? {
    setting: draft.manual_setting, label: settingLabels[draft.manual_setting], original_value: plan.setup[draft.manual_setting],
    evidence: '운전자가 직접 고른 실험입니다. IBT에서 읽은 현재 값에서 한 항목만 바꾸어 비교합니다.',
    tradeoff: '선택한 증상뿐 아니라 다른 코너의 안정성·타이어·페이스가 나빠지는지 함께 확인하세요.',
    expected_effect: '원하는 변화와 부작용을 메모하고 동일한 구간에서 확인하세요.', source: 'DRIVER_SELECTED',
  } : plan?.candidate);
  const visibleHistory = workflow.history;
  const source = active?.session || facts?.session || data.session;
  const compatibleFiles = files.filter(file => file.name !== active?.baseline_file && isSelectableRecording(file)
    && (!file.car_name || file.car_name === source.car_name) && (!file.track_name || file.track_name === source.track_name)
    && (file.metadata_pending || (file.track_config || '') === (source.track_config || '')));
  const fileOptions = (selection, rows) => <>{selection && !rows.some(f => f.name === selection) && <NativeSelectOption value={selection} disabled={!selectableFile(selection)}>{selection} · 저장한 선택</NativeSelectOption>}{rows.map(file => <NativeSelectOption key={file.name} value={file.name}>{shortFile(file.name)}{file.valid_laps != null ? ` · 유효 ${file.valid_laps}랩` : ''}</NativeSelectOption>)}</>;
  const stage = !active ? '문제와 변경 선택' : !active.candidate_file ? '변경 후 주행 연결' : '체감과 결과 비교';

  return <section className="setup-workbench" aria-label="셋업 실험 작업 화면">
    <div className="setup-heading"><div><div className="eyebrow">SETUP / TEST / REFINE</div><h1>셋업 실험</h1><p>불편한 곳을 정하고, 한 가지씩 바꿔 내 셋업을 찾아갑니다.</p></div><div className="flex items-center gap-3"><Badge variant="outline">이 브라우저에 자동 저장</Badge><Button size="sm" variant="outline" onClick={() => setDialog('method')}><Info data-icon="inline-start" />실험 방법·참고 도구</Button><Button size="sm" variant="outline" onClick={onChooseSource}>주행 바꾸기</Button></div></div>
    <div className="setup-progress"><span><b>01</b> 문제를 고르고</span><ArrowRight /><span><b>02</b> 한 항목만 바꾸고</span><ArrowRight /><span><b>03</b> 다시 달려 비교하고</span><ArrowRight /><span><b>04</b> 유지·원복·재시험</span><Badge variant="secondary">현재 · {stage}</Badge></div>
    {(error || storageError || shared?.storageError) && <Alert variant="destructive" className="setup-error"><AlertDescription>{error || storageError || shared?.storageError}</AlertDescription></Alert>}
    <div className="setup-columns">
      <Card className="setup-card setup-problem"><CardHeader><div className="setup-card-title"><Badge variant="outline">01</Badge><CardTitle>어디가 불편한가요?</CardTitle>{active && <Badge variant="secondary">A 고정됨</Badge>}</div><CardDescription>느낀 증상과 관측 수치를 함께 남깁니다.</CardDescription></CardHeader><CardContent>
        <FieldGroup>
          <Field><FieldLabel htmlFor="setup-baseline">변경 전 주행 · A</FieldLabel><NativeSelect id="setup-baseline" value={draft.filename} disabled={Boolean(active)} onChange={e => patchDraft({ filename: e.target.value })}><NativeSelectOption value="">기준으로 사용할 IBT 선택</NativeSelectOption>{fileOptions(draft.filename, files.filter(isSelectableRecording))}</NativeSelect></Field>
          <Choice title="이번 실험의 목표" options={goals} value={draft.goal} onChange={goal => patchDraft({ goal })} disabled={Boolean(active)} />
          <Choice title="언제 불편한가요?" options={phases} value={draft.phase} onChange={phase => patchDraft({ phase })} disabled={Boolean(active)} />
          <Choice title="어떤 증상인가요?" options={symptoms} value={draft.symptom} onChange={symptom => patchDraft({ symptom })} disabled={Boolean(active)} className="setup-symptoms" />
          <FieldGroup className="setup-two-fields"><Field><FieldLabel htmlFor="setup-speed">코너 속도</FieldLabel><NativeSelect id="setup-speed" disabled={Boolean(active)} value={draft.speed} onChange={e => patchDraft({ speed: e.target.value, corner_id: '' })}><NativeSelectOption value="">속도·구간 선택</NativeSelectOption>{speeds.map(([key, label]) => <NativeSelectOption key={key} value={key}>{label}</NativeSelectOption>)}</NativeSelect></Field><Field><FieldLabel htmlFor="setup-severity">불편한 정도 · 체감</FieldLabel><NativeSelect id="setup-severity" disabled={Boolean(active)} value={draft.severity} onChange={e => patchDraft({ severity: e.target.value })}>{[['1','1 · 조금'],['2','2 · 가끔 신경 쓰임'],['3','3 · 반복해서 불편'],['4','4 · 주행을 방해'],['5','5 · 제어하기 어려움']].map(([v,l]) => <NativeSelectOption key={v} value={v}>{l}</NativeSelectOption>)}</NativeSelect></Field></FieldGroup>
          <Field><FieldLabel htmlFor="setup-corner">문제가 생기는 구간</FieldLabel><NativeSelect id="setup-corner" disabled={Boolean(active) || !facts} value={draft.corner_id} onChange={e => { const corner = facts?.corners.find(c => c.id === e.target.value); patchDraft({ corner_id: e.target.value, ...(corner?.speed_class ? { speed: corner.speed_class } : {}) }); }}><NativeSelectOption value="">선택한 속도의 구간 전체</NativeSelectOption>{(facts?.corners || []).map(c => <NativeSelectOption key={c.id} value={c.id}>{c.label} · {number(c.min_speed_kph)} km/h</NativeSelectOption>)}</NativeSelect></Field>
          <Field><FieldLabel htmlFor="setup-note">상황 메모</FieldLabel><Textarea id="setup-note" maxLength={2000} rows={2} disabled={Boolean(active)} value={draft.note} onChange={e => patchDraft({ note: e.target.value })} placeholder="예: 브레이크를 놓고 돌릴 때 앞이 밀림. 연석은 밟지 않음." /><FieldDescription>메모는 실험 기록으로 남습니다. 원인을 자동으로 단정하지 않습니다.</FieldDescription></Field>
        </FieldGroup>
        <div className="setup-observation"><div className="setup-observation-heading"><strong>관측 근거</strong><Badge variant="outline">체감과 별도</Badge></div><p>{observation ? `${observation.label} · 최저 속도 ${number(observation.min_speed_kph)} km/h · 기준 대비 ${signed(observation.delta_s)} s` : facts ? `현재 주행의 유효 랩 ${facts.valid_laps}개 · 선택한 조건에서 비교합니다.` : 'IBT를 고르면 실제 코너 수치와 현재 셋업을 읽습니다.'}</p>{facts?.filename === data.filename && observation && <Button size="sm" variant="ghost" onClick={() => onCorner(observation.id)}>코너 비교에서 관측 확인<ArrowRight data-icon="inline-end" /></Button>}</div>
      </CardContent></Card>

      <Card className="setup-card setup-plan"><CardHeader><div className="setup-card-title"><Badge variant="outline">02</Badge><CardTitle>{active ? '이번 주행에서 바꿀 한 가지' : '이번에는 한 가지만 바꿉니다'}</CardTitle></div><CardDescription>{active ? `${source.car_name} · ${source.track_name}` : '현재 차고 값과 작은 변경을 한 쌍으로 고정합니다.'}</CardDescription></CardHeader><CardContent>
        <div className="setup-next-action"><span>지금 할 일</span><h2>{active ? active.candidate_file ? '오른쪽에 체감을 남기고 A/B를 비교하세요' : '차고에서 한 항목을 바꾸고 새 주행을 기록하세요' : busy ? '현재 셋업과 코너 관측을 확인하고 있습니다' : candidate ? '변경 전후의 실제 차고 값을 확인하세요' : draft.filename ? '선택한 증상과 주행 자료를 확인하세요' : '왼쪽에서 변경 전 주행 A를 선택하세요'}</h2></div>
        {active && <FieldGroup className="setup-candidate-file"><Field><FieldLabel htmlFor="setup-candidate">변경 후 새 주행 · B</FieldLabel><NativeSelect id="setup-candidate" value={active.candidate_file} onChange={e => patchActive({ candidate_file: e.target.value })}><NativeSelectOption value="">같은 차량·트랙의 다른 IBT 선택</NativeSelectOption>{fileOptions(active.candidate_file, compatibleFiles)}</NativeSelect><Button size="sm" variant="outline" onClick={refreshFiles}>새 주행 찾기 · 목록 새로고침</Button></Field></FieldGroup>}
        {plan && !active && <FieldGroup className="setup-setting-picker"><Field><FieldLabel htmlFor="setup-setting">이번에 시험할 항목</FieldLabel><NativeSelect id="setup-setting" value={draft.manual_setting || ''} onChange={e => patchDraft({ manual_setting: e.target.value })}><NativeSelectOption value="">증상에 맞는 변경 가설</NativeSelectOption>{Object.entries(settingLabels).filter(([key]) => plan.setup?.[key] != null).map(([key, label]) => <NativeSelectOption key={key} value={key}>{label} · 직접 선택</NativeSelectOption>)}</NativeSelect><FieldDescription>직접 선택하면 변경할 값을 차고에서 확인해 입력하세요.</FieldDescription></Field></FieldGroup>}
        {candidate ? <>
          <div className="setup-change-heading"><FlaskConical /><div><h2>{settingLabels[candidate.setting] || candidate.label || candidate.setting}</h2><span>{candidate.source === 'DRIVER_SELECTED' ? '직접 선택한 변경' : '작은 변경 가설'} · 효과 확인 전</span></div></div>
          <FieldGroup className="setup-values"><Field><FieldLabel htmlFor="setup-original">A · 변경 전</FieldLabel><Input id="setup-original" type="number" step="any" value={active ? active.adjustment.original_value : original} disabled={Boolean(active)} readOnly /></Field><ArrowRight /><Field><FieldLabel htmlFor="setup-target">B · 이번에 시험할 값</FieldLabel><Input id="setup-target" type="number" step="any" value={active ? active.adjustment.value : target} disabled={Boolean(active)} onChange={e => { setTarget(e.target.value); patchDraft({ target_value: e.target.value, target_setting: candidate.setting }); }} placeholder="차고의 실제 값 입력" /></Field></FieldGroup>
          <p className="setup-value-note">단위 · {candidate.unit || '차고 표기 확인'}{candidate.step_text ? ` · ${candidate.step_text}` : ''}</p>
          <div className="setup-explanation"><h3>무엇이 달라지는지 봅니다</h3><p>{candidate.expected_effect || '같은 증상 구간에서 다루기 편해지는지 확인합니다.'}</p><h3>함께 확인할 부작용</h3><p>{candidate.tradeoff}</p><h3>이 변경을 시험하는 이유</h3><p>{candidate.evidence}</p></div>
        </> : <div className="setup-plan-guide"><h2>{busy ? '측정 자료를 불러오는 중입니다' : '실험 계획에 필요한 것'}</h2><p>{plan?.hypothesis?.reason || '실제 주행 A와 증상 선택이 있어야 현재 값에 맞는 변경을 제안할 수 있습니다.'}</p><ol><li><strong>언제·어디서</strong><span>진입 / 중간 / 탈출과 코너 속도를 정합니다.</span></li><li><strong>실제 현재값</strong><span>IBT에서 읽고 차고에서 확인합니다.</span></li><li><strong>한 항목의 작은 변화</strong><span>알 수 없는 클릭 크기나 효과를 만들어내지 않습니다.</span></li></ol></div>}
        <div className="setup-run-plan"><h3>같은 조건으로 다시 달리기</h3><div><Check /><span>연료·타이어·노면 조건을 가능한 한 맞춥니다.</span></div><div><Check /><span>다른 셋업과 운전 방법은 함께 바꾸지 않습니다.</span></div><div><Check /><span>1~2랩으로 체감을 보고, 효과는 A/B 각각 {draft.goal === 'ENDURANCE' ? '5' : '3'}개 이상의 유효 랩으로 확인합니다.</span></div></div>
      </CardContent><CardFooter>{active ? <><Badge variant="secondary">A와 변경 계획 고정됨</Badge><Button size="sm" variant="ghost" onClick={() => setDialog('cancel')}><RotateCcw data-icon="inline-start" />실험 중단</Button></> : <Button disabled={busy || !draftSelectable || !canPrepare(candidate, original, target, draft)} onClick={prepare}><FlaskConical data-icon="inline-start" />이 변경으로 실험 시작</Button>}</CardFooter></Card>

      <Card className="setup-card setup-review"><CardHeader><div className="setup-card-title"><Badge variant="outline">03</Badge><CardTitle>달린 뒤, 무엇이 달라졌나요?</CardTitle></div><CardDescription>체감·부작용과 실제 측정 결과를 함께 봅니다.</CardDescription></CardHeader><CardContent ref={reviewPane}>
        <FieldGroup className="setup-post-feedback">
          <Choice title="처음 느낀 증상은?" value={active?.feedback.change || ''} options={[["IMPROVED", "줄었음"], ["SAME", "비슷함"], ["WORSENED", "더 심해짐"]]} disabled={!active?.candidate_file} onChange={change => patchFeedback({ change })} />
          <Choice title="운전 편의 · 1 어려움 / 5 편함" value={String(active?.feedback.rating || '')} options={[["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]]} disabled={!active?.candidate_file} onChange={rating => patchFeedback({ rating })} />
          <Field><FieldLabel htmlFor="setup-side-effects">좋아진 곳·새로 생긴 부작용</FieldLabel><Textarea id="setup-side-effects" rows={2} maxLength={2000} disabled={!active?.candidate_file} value={active?.feedback.side_effects || ''} onChange={e => patchFeedback({ side_effects: e.target.value })} placeholder="예: 중간 회전은 편해졌지만 탈출 때 뒤가 흐름." /></Field>
        </FieldGroup>
        <div className="setup-compare-action"><Button variant="outline" disabled={!active?.candidate_file || !selectableFile(active.baseline_file) || !selectableFile(active.candidate_file) || comparing} onClick={compare}>{comparing ? 'A/B 측정 비교 중' : 'A/B 측정 결과 확인'}</Button><span>베스트 한 랩보다 중앙값·편차·완주 안정성을 봅니다.</span></div>
        {active?.comparison ? <Findings comparison={active.comparison} /> : <div className="setup-result-wait"><strong>체감과 셋업 효과는 구분합니다</strong><p>편해졌다는 기록은 바로 남길 수 있습니다. 속도 효과는 조건·반복 표본·실제 변경이 맞을 때만 확인합니다.</p><dl className="setup-axes"><div><dt>페이스</dt><dd>A/B 비교 대기</dd></div><div><dt>일관성</dt><dd>A/B 비교 대기</dd></div><div><dt>완주 안정성</dt><dd>A/B 비교 대기</dd></div></dl></div>}
      </CardContent><CardFooter><FieldGroup className="setup-save-decision"><Choice title="다음 주행에는 어떻게 할까요?" value={decision} options={decisions} disabled={!active?.candidate_file} onChange={setDecision} /></FieldGroup><Button size="sm" disabled={!active?.candidate_file || !active.feedback.change || !active.feedback.rating || comparing} onClick={complete}><Save data-icon="inline-start" />결과 기록·다음 실험 준비</Button></CardFooter></Card>
    </div>

    <Card className="setup-log"><CardHeader><div className="flex items-center justify-between gap-4"><CardTitle>어떤 변경이 나에게 맞았나요?</CardTitle><Badge variant="outline">실험 기록 {visibleHistory.length}개</Badge></div><CardDescription>체감과 측정 효과를 구분해 누적합니다. 행을 누르면 당시 조건과 메모를 봅니다.</CardDescription></CardHeader><CardContent><Table><TableHeader><TableRow><TableHead>실험</TableHead><TableHead>문제</TableHead><TableHead>변경</TableHead><TableHead>체감</TableHead><TableHead>측정 효과</TableHead><TableHead>내 결정</TableHead></TableRow></TableHeader><TableBody>{visibleHistory.length ? visibleHistory.map((trial, index) => <TableRow key={trial.id} onClick={() => setDialog(trial)}><TableCell><Button variant="ghost" size="sm" onClick={e => { e.stopPropagation(); setDialog(trial); }}>#{visibleHistory.length - index} · {trial.session?.track_name || '기록 보기'}</Button></TableCell><TableCell>{labelFor(phases, trial.issue.phase)} · {labelFor(symptoms, trial.issue.symptom)}</TableCell><TableCell>{settingLabels[trial.adjustment.setting] || trial.adjustment.label} · {formatValue(trial.adjustment.original_value)} → {formatValue(trial.adjustment.value)}</TableCell><TableCell>{labelFor([['IMPROVED','개선'],['SAME','비슷함'],['WORSENED','악화']], trial.feedback.change)} · {trial.feedback.rating || '—'}/5</TableCell><TableCell>{effectLabels[trial.comparison?.verdict] || '효과 미확정'}</TableCell><TableCell>{trial.decision === 'CANCELLED' ? '실험 중단' : labelFor(decisions, trial.decision)}</TableCell></TableRow>) : <TableRow><TableCell colSpan={6}>첫 실험을 마치면 변경값·체감·측정 결과·다음 결정이 이곳에 쌓입니다.</TableCell></TableRow>}</TableBody></Table></CardContent></Card>

    <Dialog open={Boolean(dialog)} onOpenChange={open => !open && setDialog(null)}><DialogContent className="setup-dialog max-w-2xl"><DialogHeader><DialogTitle>{dialog === 'method' ? '좋은 셋업을 찾는 실험 방법' : dialog === 'cancel' ? '진행 중 실험 중단' : '이 실험의 조건과 피드백'}</DialogTitle><DialogDescription>{dialog === 'method' ? '한 번에 한 항목을 바꾸고, 반복해서 내게 맞는 타협점을 찾습니다.' : '실험 기록과 체감을 저장합니다. 셋업 효과는 조건이 맞는 A/B 측정으로만 확인합니다.'}</DialogDescription></DialogHeader>
      {dialog === 'method' ? <div className="setup-method"><ol><li>기준 주행 A에서 불편한 구간·증상·정도를 고릅니다.</li><li>한 항목만 작은 폭으로 바꾸고 B를 기록합니다. 게임 설정은 직접 바꿉니다.</li><li>체감·새 부작용과 중앙 랩타임·편차·완주 안정성을 함께 봅니다.</li><li>B 유지 / A 원복 / 같은 변경 재시험을 기록하고 다음 실험을 이어갑니다.</li></ol><p>노면·연료·타이어 조건이 다르거나 변경 항목이 여러 개면 개별 셋업 효과는 미확정입니다. 체감만으로 셋업 효과를 학습하지 않습니다.</p><h3>참고한 실제 도구</h3><p><a href="https://github.com/SeriousOldMan/Simulator-Controller/wiki/Setup-Workbench" target="_blank" rel="noreferrer">Setup Workbench</a> · 증상·심각도와 반복 실험 기록</p><p><a href="https://store.steampowered.com/app/1220160/Virtual_Race_Car_Engineer_2020/" target="_blank" rel="noreferrer">Virtual Race Car Engineer</a> · 전체 랩·특정 코너의 체감으로 조정</p><p><a href="https://coachdaveacademy.com/delta/setup-iq/" target="_blank" rel="noreferrer">Setup IQ</a> · 변경 버전을 남기고 다시 주행하는 흐름. 현재 안내상 LMU·ACC용</p><p>이 페이지는 기존 로컬 계산을 사용하며 AI 분석·차고 자동 변경은 실행하지 않습니다.</p></div> : dialog === 'cancel' ? <><p>중단한 시도도 기록에 남깁니다. 차고 값은 원래 값으로 직접 되돌려 주세요.</p><Button variant="destructive" onClick={() => { comparisonFlight.current?.abort(); setComparing(false); changeWorkflow(state => ({ ...state, active: null, history: [{ ...state.active, decision: 'CANCELLED', completed_at: new Date().toISOString() }, ...state.history].slice(0, 100) })); setDialog(null); }}>중단 기록 남기기</Button></> : dialog && <div className="setup-method"><p><strong>{dialog.session?.car_name} · {dialog.session?.track_name}</strong></p><p>A · {dialog.baseline_file}</p><p>B · {dialog.candidate_file || '미연결'}</p><p>처음 체감 · {labelFor(phases, dialog.issue.phase)} / {labelFor(speeds, dialog.issue.speed)} / {labelFor(symptoms, dialog.issue.symptom)} / 불편도 {dialog.issue.severity}/5</p><p>상황 · {dialog.issue.note || '메모 없음'}</p><p>주행 후 · {dialog.feedback.side_effects || '추가 메모 없음'}</p>{dialog.comparison && <Findings comparison={dialog.comparison} />}<p>내 결정 · {dialog.decision === 'CANCELLED' ? '실험 중단' : labelFor(decisions, dialog.decision)}</p><Button variant="outline" disabled={Boolean(active) || !selectableFile(nextBaseline(dialog))} onClick={() => { patchDraft({ ...dialog.issue, filename: nextBaseline(dialog), corner_id: '' }); setDialog(null); }}>이 기준으로 다음 실험</Button></div>}
    </DialogContent></Dialog>
  </section>;
}
