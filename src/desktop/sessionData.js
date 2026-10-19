import { requestJson } from '../analysis/analysisClient';
import { projectComparisonPaths } from '../lib/trackMapProjection';
import { lossColour, lossRates, lossScale, rateAt } from '../lib/lossColours';
import { LAST_SESSION_KEY } from '../analysis/desktopSelection';
export { LAST_SESSION_KEY } from '../analysis/desktopSelection';

export let data = { kind: 'LIVE_LOCAL', filename: '', defaultTarget: null, defaultReference: null,
  session: { track_name: '주행 파일을 선택하세요', car_name: '', track_config: '', track_length_m: null },
  laps: [], corners: [], initialComparison: {}, consistency: {}, external: [] };
export function analysisOptions(workspace, filename = data.filename) {
  if (!workspace) return {};
  const currentFile = filename === workspace.selectedFile;
  const source = currentFile ? workspace : workspace.workflow?.readDraft?.(filename) || {};
  return { ...(currentFile ? { session_mode: workspace.sessionMode, pit_rule: workspace.pitRule } : {}),
    user_feedback: source.userFeedback || '', feedback_phase: source.feedbackPhase || null,
    feedback_speed: source.feedbackSpeed || null, feedback_symptom: source.feedbackSymptom || null,
    setup_mode: source.setupMode || 'EXPLORE' };
}
// A lap change returns fresh analysis alongside the trace. Keep all workspaces
// on that result instead of retaining the file picker session's old summary.
export function currentSessionResult(session, result = {}, external = []) {
  return { ...session, external: external || [], initialComparison: result || {},
    analysisResult: result?.analysisResult || session.analysisResult,
    practice: result?.practice ?? session.practice,
    practiceFocuses: result?.practiceFocuses ?? session.practiceFocuses,
    corners: result?.corners || session.corners };
}
export function rememberSession(session, target, reference, external) {
  try {
    if (!session.filename) localStorage.removeItem(LAST_SESSION_KEY);
    else localStorage.setItem(LAST_SESSION_KEY, JSON.stringify({ filename: session.filename, target_lap: Number(target), reference_lap: reference ? Number(reference) : null,
      external_reference_files: external, reference_mode: external.length > 1 ? 'AGGREGATE' : external.length ? 'SINGLE' : 'LOCAL' }));
  } catch { /* Browsing still works if local storage is unavailable. */ }
}
export let length = 1;
export let laps = [];
export function activateSession(next) {
  data = next; length = Number.isFinite(next.session.track_length_m) && next.session.track_length_m > 0 ? next.session.track_length_m : 1;
  laps = data.laps.filter(lap => lap.status === 'VALID' || lap.lap_number === data.defaultTarget);
}
export async function labRequest(route, body = {}, signal) {
  return requestJson(`/api/workspace/${route}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
}
export function normalizeComparison(payload) {
  const rows = (payload.trace || []).map(p => ({ ...p, distance: p.distance_pct * length,
    delta: p.delta_s, speed: p.target_speed_kph, referenceSpeed: p.reference_speed_kph,
    brake: finite(p.target_brake) ? p.target_brake * 100 : null, referenceBrake: finite(p.reference_brake) ? p.reference_brake * 100 : null,
    throttle: finite(p.target_throttle) ? p.target_throttle * 100 : null, referenceThrottle: finite(p.reference_throttle) ? p.reference_throttle * 100 : null,
    steering: p.target_steering_deg, referenceSteering: p.reference_steering_deg, gear: p.target_gear, referenceGear: p.reference_gear,
    speedBand: finite(p.reference_speed_p25_kph) && finite(p.reference_speed_p75_kph) ? [p.reference_speed_p25_kph, p.reference_speed_p75_kph] : null,
  }));
  const rates = lossRates(rows, length), scale = lossScale(rates);
  return { ...payload, controlEvents: payload.controlEvents?.abs ? { abs: payload.controlEvents.abs } : {}, rows, corners: (payload.corners || []).map(c => ({ ...c, target: c.target || {}, reference: c.reference || {}, evidence: c.evidence || [] })).sort((a, b) => a.apex_pct - b.apex_pct),
    paths: projectComparisonPaths(payload.track_path || [], payload.reference_track_path || []),
    loss: scale ? { scale, colourAt: pct => lossColour(rateAt(rates, pct), scale) } : null };
}
export const finite = Number.isFinite;
export const number = (value, digits = 1) => finite(value) ? value.toFixed(digits) : '—';
export const signed = (value, digits = 3) => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}` : '—';
export const lapTime = value => finite(value) ? `${Math.floor(value / 60)}:${(value % 60).toFixed(3).padStart(6, '0')}` : '—';
const difference = (a, b) => finite(a) && finite(b) ? a - b : null;
export const distanceDifference = (a, b) => { const delta = difference(a, b); return finite(delta) ? delta * length : null; };
export const CHANNELS = [
  { key: 'delta', label: '누적 차이', unit: 's' },
  { key: 'speed', reference: 'referenceSpeed', label: '속도', unit: 'km/h' },
  { key: 'brake', reference: 'referenceBrake', label: '브레이크', unit: '%' , domain: [0, 100] },
  { key: 'throttle', reference: 'referenceThrottle', label: '스로틀', unit: '%', domain: [0, 100] },
  { key: 'steering', reference: 'referenceSteering', label: '조향', unit: '°' },
  { key: 'gear', reference: 'referenceGear', label: '기어', unit: '' },
];

export function comparisonFor() { return normalizeComparison(data.initialComparison || {}); }
export function loadOverlay({ laps: chosen, base_key, start_pct = 0, end_pct = 1, ...rest }) {
  return labRequest('overlay', { filename: data.filename, laps: chosen, base_key, start_pct, end_pct, ...rest });
}
export function readNames() {
  return Object.fromEntries((data?.corners || []).filter(c => c.official_turn_number || c.turn_name || c.spoken)
    .map(c => [c.id, { number: c.official_turn_number || '', name: c.turn_name || '', spoken: c.spoken || '' }]));
}
export function cornerLabel(corner, names) {
  const entry = names[corner.id];
  return [entry?.number ? `${entry.number}번` : '', entry?.name].filter(Boolean).join(' · ') || corner.label || corner.id;
}
