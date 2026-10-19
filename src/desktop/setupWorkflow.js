export const SETUP_KEY = 'iracing-tuner.setup-workbench.v1';
export const phases = [['ENTRY', '진입'], ['MID_CORNER', '코너 중간'], ['EXIT', '탈출']];
export const speeds = [['LOW', '저속'], ['MEDIUM', '중속'], ['HIGH', '고속']];
export const symptoms = [['UNDERSTEER', '앞이 밀림'], ['OVERSTEER', '뒤가 흐름'], ['BRAKING', '제동 불안'], ['KERB', '연석에서 튐']];
export const goals = [['COMFORT', '다루기 편하게'], ['PACE', '랩타임 개선'], ['ENDURANCE', '긴 주행 안정성']];
export const decisions = [['KEEP', 'B 유지'], ['REVERT', 'A로 원복'], ['RETEST', '같은 변경 재시험']];
export const settingLabels = { front_arb_blades: '앞 안티롤바', rear_arb_blades: '뒤 안티롤바', rear_wing_angle_deg: '뒤 윙 각도', diff_preload_nm: '디퍼렌셜 프리로드', front_ride_height: '앞 차고 높이', rear_ride_height: '뒤 차고 높이', brake_bias_pct: '브레이크 바이어스', front_toe_mm: '앞 토' };
export const labelFor = (options, value) => options.find(([key]) => key === value)?.[1] || '확인 필요';
export const contextKey = session => JSON.stringify([session.car_name || '', session.track_name || '', session.track_config || '']);
export const emptyDraft = filename => ({ filename: filename || '', phase: '', speed: '', symptom: '',
  goal: 'COMFORT', severity: '3', corner_id: '', note: '', manual_setting: '' });
export const emptyFeedback = () => ({ change: '', rating: '', side_effects: '' });
export const emptyWorkflow = () => ({ version: 1, active: null, history: [], drafts: {} });

// Keep the established workflow as the authority for active trials and history.
// Older trials stored the original issue in feedback and the result in driver_feedback.
export function fromProductionTrial(trial) {
  if (!trial) return null;
  const session = trial.session || (trial.context && typeof trial.context === 'object' ? trial.context : {});
  const issue = { ...emptyDraft(trial.baseline_file), ...(trial.issue || trial.feedback || {}),
    filename: trial.baseline_file, note: trial.issue?.note ?? trial.feedback?.text ?? '',
    goal: trial.issue?.goal || trial.goal || 'COMFORT' };
  const original = trial.adjustment?.original_value ?? trial.original_value;
  const value = trial.adjustment?.value ?? trial.target_value;
  return { ...trial, session, context: contextKey(session), issue,
    candidate_file: trial.candidate_file || '',
    adjustment: { ...trial.adjustment, original_value: original, value },
    feedback: { ...emptyFeedback(), ...(trial.driver_feedback || (trial.issue ? trial.feedback : null)) },
    comparison: trial.comparison || null,
    decision: trial.decision || (trial.outcome === 'CANCELLED' ? 'CANCELLED' : undefined) };
}

export function toProductionTrial(trial) {
  if (!trial) return null;
  const issue = trial.issue || emptyDraft(trial.baseline_file);
  const adjustment = trial.adjustment || {};
  return { ...trial, goal: issue.goal || trial.goal || 'COMFORT',
    session_mode: trial.session_mode || (issue.goal === 'ENDURANCE' ? 'ENDURANCE' : 'BASELINE_3LAP'),
    reference_mode: trial.reference_mode || 'LOCAL', reference_files: trial.reference_files || [],
    original_value: adjustment.original_value, target_value: adjustment.value,
    original_text: trial.original_text || String(adjustment.original_value ?? '확인 필요'),
    target_text: trial.target_text || String(adjustment.value ?? '확인 필요'),
    context: { ...(trial.session || {}), name: trial.baseline_file },
    feedback: { phase: issue.phase, speed: issue.speed, symptom: issue.symptom, text: issue.note || '' },
    driver_feedback: { ...emptyFeedback(), ...trial.feedback, rating: Number(trial.feedback?.rating) || null },
    outcome: trial.decision === 'CANCELLED' ? 'CANCELLED' : trial.completed_at ? 'OBSERVED' : trial.outcome,
    effect_status: trial.comparison?.verdict || 'UNCONFIRMED' };
}

export function hydrateSetupWorkflow(saved, production) {
  if (!production) return saved;
  return { ...saved, active: fromProductionTrial(production.activeTrial),
    history: (production.history || []).map(fromProductionTrial).slice(0, 100) };
}

export function readSetupWorkflow(storage) {
  try {
    const saved = JSON.parse(storage.getItem(SETUP_KEY));
    if (saved?.version !== 1 || !Array.isArray(saved.history) || typeof saved.drafts !== 'object' || !saved.drafts) return emptyWorkflow();
    const validTrial = trial => trial && typeof trial.id === 'string' && typeof trial.baseline_file === 'string'
      && trial.adjustment && Number.isFinite(trial.adjustment.original_value) && Number.isFinite(trial.adjustment.value)
      && typeof trial.adjustment.setting === 'string' && trial.feedback && trial.issue && typeof trial.issue.filename === 'string'
      && typeof trial.candidate_file === 'string' && typeof trial.context === 'string';
    const drafts = Object.fromEntries(Object.entries(saved.drafts).filter(([, value]) => value && typeof value === 'object' && typeof value.filename === 'string'));
    return { ...emptyWorkflow(), drafts, active: validTrial(saved.active) ? saved.active : null,
      history: saved.history.filter(validTrial).slice(0, 100) };
  } catch { return emptyWorkflow(); }
}

export function canPrepare(candidate, original, target, draft) {
  const numeric = value => value !== '' && value !== null && Number.isFinite(Number(value));
  return Boolean(candidate?.setting && draft.filename && phases.some(([key]) => key === draft.phase)
    && speeds.some(([key]) => key === draft.speed) && symptoms.some(([key]) => key === draft.symptom)
    && numeric(original) && numeric(target) && Number(original) !== Number(target));
}

export function finishSetupTrial(state, decision, now = new Date().toISOString()) {
  const trial = state.active;
  if (!trial || !decisions.some(([key]) => key === decision) || !trial.candidate_file
    || !['IMPROVED', 'SAME', 'WORSENED'].includes(trial.feedback.change) || ![1,2,3,4,5].includes(Number(trial.feedback.rating))) return state;
  // This is the driver's choice. It never promotes feel into verified learning.
  return { ...state, active: null, history: [{ ...trial, decision, completed_at: now }, ...state.history].slice(0, 100) };
}

export function nextBaseline(trial) {
  return trial.decision === 'KEEP' ? trial.candidate_file : trial.baseline_file;
}

export function retryTrial(trial, id, now = new Date().toISOString()) {
  return { ...trial, id, parent_id: trial.id, started_at: now, completed_at: undefined, decision: undefined,
    candidate_file: '', feedback: emptyFeedback(), comparison: null };
}

export function patchTrial(state, patch) {
  if (!state.active) return state;
  return { ...state, active: { ...state.active, ...patch,
    ...(('candidate_file' in patch || 'feedback' in patch) ? { comparison: null } : {}) } };
}
