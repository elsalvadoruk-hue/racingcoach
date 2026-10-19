import { NativeSelect } from './ui/native-select';
import { Button } from './ui/button';
import { Textarea } from './ui/textarea';
import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Field, FieldLabel } from './ui/field';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription, EmptyMedia } from './ui/empty';
import { FlaskConical } from 'lucide-react';
import { HelpHint } from './HelpGuide';
import { WorkflowSteps } from './WorkspaceShell';

export const WORKFLOW_KEY = 'iracing-tuner.workflow.v1';
const empty = () => ({ version: 1, activeTrial: null, history: [], drafts: {}, preferences: {} });
export function readWorkflow() {
  try { const value = JSON.parse(localStorage.getItem(WORKFLOW_KEY)); return value?.version === 1 ? { ...empty(), ...value } : empty(); }
  catch { return empty(); }
}
export function useTrialWorkflow() {
  const [workflow, setWorkflow] = useState(readWorkflow);
  const [storageError, setStorageError] = useState('');
  const current = useRef(workflow);
  const update = useCallback((transform) => {
    const next = transform(current.current); current.current = next; setWorkflow(next);
    try { localStorage.setItem(WORKFLOW_KEY, JSON.stringify(next)); setStorageError(''); }
    catch { setStorageError('브라우저에 초안을 저장하지 못했습니다. 현재 작업은 유지되지만 새로고침 후 복원할 수 없습니다.'); }
  }, []);
  const saveDraft = useCallback((filename, draft) => { if (filename) update((state) => ({ ...state, drafts: { ...state.drafts, [filename]: { ...draft, updated_at: new Date().toISOString() } } })); }, [update]);
  const readDraft = useCallback((filename) => current.current.drafts[filename] || {}, []);
  const patchTrial = useCallback((patch) => update((state) => state.activeTrial ? { ...state, activeTrial: { ...state.activeTrial, ...patch } } : state), [update]);
  const startTrial = useCallback((trial) => update((state) => ({ ...state, activeTrial: trial })), [update]);
  const completeTrial = useCallback((completion) => update((state) => state.activeTrial ? { ...state, activeTrial: null, history: [{ ...state.activeTrial, ...completion, completed_at: new Date().toISOString() }, ...state.history].slice(0, 100) } : state), [update]);
  const savePreferences = useCallback((patch) => update((state) => ({ ...state, preferences: { ...state.preferences, ...patch } })), [update]);
  return { ...workflow, storageError, saveDraft, readDraft, patchTrial, startTrial, completeTrial, savePreferences };
}
export const GOALS = [['COMFORT', '안정감과 운전 편의'], ['PACE', '단기 페이스'], ['ENDURANCE', '긴 주행의 안정성']];
export function GoalSelector({ value, onChange, disabled = false }) {
  const id = useId();
  return <Field data-disabled={disabled}><div className="coach-goal-title flex items-center gap-1"><FieldLabel htmlFor={id}>이번 변경의 목표</FieldLabel><HelpHint topic="goal" /></div><NativeSelect id={id} data-help="goal" aria-label="실험 목표" value={value} disabled={disabled} onChange={event => onChange(event.target.value)} className="coach-goal-select">{GOALS.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</NativeSelect></Field>;
}

export function TrialPanel({ trial, currentFile, canAttach, onAttach, onPatch, onComplete, onCompare }) {
  const [detailsOpen, setDetailsOpen] = useState(Boolean(trial?.candidate_file));
  useEffect(() => { setDetailsOpen(Boolean(trial?.candidate_file)); }, [trial?.id, trial?.candidate_file]);
  if (!trial) return null;
  const feedback = trial.driver_feedback || {};
  const patchFeedback = (patch) => onPatch({ driver_feedback: { ...feedback, ...patch }, comparison: null });
  const currentStep = !trial.candidate_file ? 1 : !feedback.change || !feedback.rating ? 2 : trial.comparison ? 4 : 3;
  return <section className="coach-trial" aria-label="진행 중 셋업 실험">
    <WorkflowSteps steps={['A 고정', 'B 연결', '체감 기록', '효과 확인']} current={currentStep} />
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><div className="flex items-center gap-1"><h2 data-help="trial" className="text-lg font-bold text-foreground">진행 중 실험 · 변경 전 A 고정</h2><HelpHint topic="trial" /></div>
        <p className="mt-2 text-xs text-muted-foreground">{GOALS.find(([key]) => key === trial.goal)?.[1]} · {trial.adjustment.label || trial.adjustment.setting}</p></div>
      <span className="text-xs text-muted-foreground">{new Date(trial.started_at).toLocaleDateString()}</span>
    </div>
    <p className="coach-trial-values">{trial.original_text} <span className="px-2 text-muted-foreground">→</span> {trial.target_text}</p>
    <div className="grid gap-2 text-xs sm:grid-cols-2"><p className="break-all text-muted-foreground">A: {trial.baseline_file}</p><p className="break-all text-muted-foreground">B: {trial.candidate_file || '변경 후 주행 연결 대기'}</p></div>
    <p className="mt-2 text-xs text-muted-foreground">비교 기준 {trial.reference_mode} · {trial.reference_files.length}개 고정 · 이 셋업 항목만 변경하세요.</p>
    {!trial.candidate_file && <div className="mt-4 border-t border-border pt-4"><p className="mb-3 text-sm text-foreground">다음: 차고에서 값을 바꾸고 새 주행을 기록한 뒤, 상단에서 새 파일을 선택하세요.</p><Button variant="default" type="button" data-help="attach" onClick={onAttach} disabled={!canAttach} className="coach-primary-action px-4 py-3 text-sm">현재 주행을 변경 후 B로 연결</Button><HelpHint topic="attach" /><p className="mt-2 text-xs text-muted-foreground">{currentFile === trial.baseline_file ? '현재 선택은 A입니다. 변경 후 생성된 새 파일을 선택해야 연결할 수 있습니다.' : !canAttach ? '동일 차량·트랙·레이아웃의 분석 가능한 주행만 연결할 수 있습니다.' : currentFile}</p></div>}
    <div className="mt-4 border-t border-border pt-3"><Button variant="surface" type="button" aria-expanded={detailsOpen} onClick={() => setDetailsOpen((value) => !value)} className="flex min-h-[40px] w-full items-center justify-between text-left text-sm"><span>체감 기록 및 실험 종료</span><span aria-hidden="true">{detailsOpen ? '−' : '+'}</span></Button></div>
    <div hidden={!detailsOpen}>
      <div className="mt-3"><div className="flex items-center gap-1"><h3 className="font-bold text-foreground">주행 후 체감 기록</h3><HelpHint topic="feeling" /><HelpHint topic="sideEffects" /></div><p className="mt-1 text-xs text-muted-foreground">1~2랩의 체감과 A/B 속도 효과는 별도로 남깁니다. 효과가 미확정이어도 체감을 저장할 수 있습니다.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-sm text-muted-foreground">증상 변화<NativeSelect data-help="feeling" aria-label="주행 후 증상 변화" value={feedback.change || ''} onChange={(event) => patchFeedback({ change: event.target.value })} className="mt-1 w-full"><option value="">선택</option><option value="IMPROVED">개선됨</option><option value="SAME">비슷함</option><option value="WORSENED">악화됨</option></NativeSelect></label><label className="text-sm text-muted-foreground">운전 편의 (1 어려움 ~ 5 편함)<NativeSelect data-help="feeling" aria-label="주행 후 운전 편의" value={feedback.rating || ''} onChange={(event) => patchFeedback({ rating: Number(event.target.value) || null })} className="mt-1 w-full"><option value="">선택</option>{[1,2,3,4,5].map((rating) => <option key={rating} value={rating}>{rating}</option>)}</NativeSelect></label></div>
        <Textarea data-help="sideEffects" aria-label="셋업 변경 부작용" value={feedback.side_effects || ''} onChange={(event) => patchFeedback({ side_effects: event.target.value })} placeholder="개선된 코너와 새로 생긴 불안정함을 적어주세요." className="mt-3 min-h-[70px] w-full border   p-3 text-sm " />
      </div>
      {trial.comparison && <p className="mt-3 border border-border p-3 text-sm text-foreground">A/B 효과: {trial.comparison.verdict} · {trial.comparison.reason}</p>}
      <div className="mt-4 flex flex-wrap gap-2"><Button variant="default" type="button" data-help="trialCompare" onClick={onCompare} disabled={!trial.candidate_file} className="coach-primary-action px-3 py-3 text-sm">고정한 A/B 효과 확인</Button><Button variant="default" type="button" data-help="finish" disabled={!trial.candidate_file || !feedback.change || !feedback.rating} onClick={() => onComplete({ outcome: 'OBSERVED', effect_status: trial.comparison?.verdict || 'UNCONFIRMED' })} className="coach-primary-action px-3 py-3 text-sm">체감과 현재 결과 저장 · 실험 종료</Button></div>
    </div>
    <Button variant="outline" type="button" data-help="cancel" onClick={() => onComplete({ outcome: 'CANCELLED', effect_status: 'UNCONFIRMED' })} className="mt-4 min-h-[40px] text-xs ">실험 중단 기록</Button>
  </section>;
}
const EFFECT_LABELS = { UNCONFIRMED: '미확정', INCONCLUSIVE: '미확정', IMPROVED: '개선', WORSENED: '악화', CONFIRMED: '확인됨', NO_CHANGE: '차이 없음', SAME: '동일', KEEP_BASELINE: '기존 설정 유지', KEEP_CANDIDATE: '변경 설정 유지' };
export function TrialHistory({ history }) {
  return <div>{history.length ? history.map((trial) => <article key={trial.id} className="coach-history-entry">
    <div className="coach-history-heading"><h3>{trial.adjustment.label || trial.adjustment.setting}</h3><span>{GOALS.find(([key]) => key === trial.goal)?.[1]}</span></div>
    <p className="coach-history-change">{trial.original_text} <span>→</span> {trial.target_text}</p>
    <dl className="coach-history-results">
      <div><dt>체감</dt><dd>{({ IMPROVED:'개선', SAME:'동일', WORSENED:'악화' })[trial.driver_feedback?.change] || '미기록'}</dd></div>
      <div><dt>운전 편의</dt><dd>{trial.driver_feedback?.rating || '—'} / 5</dd></div>
      <div><dt>A/B 효과</dt><dd>{EFFECT_LABELS[trial.effect_status] || trial.effect_status || '미확정'}</dd></div>
    </dl>
    <div className="coach-history-files"><p>A · {trial.baseline_file}</p><p>B · {trial.candidate_file || '미연결'}</p></div>
    {trial.driver_feedback?.side_effects && <p className="coach-muted-note">{trial.driver_feedback.side_effects}</p>}
  </article>) : <Empty className="min-h-[180px] border"><EmptyHeader><EmptyMedia variant="icon"><FlaskConical aria-hidden="true" /></EmptyMedia><EmptyTitle>종료한 실험이 없습니다.</EmptyTitle><EmptyDescription>셋업 실험을 완료하면 변경 전후의 체감과 검증 결과가 여기에 쌓입니다.</EmptyDescription></EmptyHeader></Empty>}</div>;
}

