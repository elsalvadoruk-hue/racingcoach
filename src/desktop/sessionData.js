import { requestJson } from '../analysis/analysisClient';
import { projectComparisonPaths } from '../lib/trackMapProjection';
import { lossColour, lossRates, lossScale, rateAt } from '../lib/lossColours';
import { LAST_SESSION_KEY } from '../analysis/desktopSelection';
export { LAST_SESSION_KEY } from '../analysis/desktopSelection';

export let activeGame = 'iracing';
let generation = 0;
const pending = new Set();
export const LMU_SESSION_KEY = 'lmu.desktop.last-session.v1';
export const measurableLap = lap => lap.status === 'VALID' || lap.measurement_eligible === true;
export function selectGame(game) {
  activeGame = game === 'lmu' ? 'lmu' : 'iracing';
  generation += 1;
  pending.forEach(controller => controller.abort());
  pending.clear();
  activateSession({ kind: 'LIVE_LOCAL', provider: activeGame, filename: '', defaultTarget: null, defaultReference: null,
    session: { track_name: '주행 파일을 선택하세요', car_name: '', track_config: '', track_length_m: null },
    laps: [], corners: [], initialComparison: {}, consistency: {}, external: [] });
}

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
  const referenceFallback = Object.prototype.hasOwnProperty.call(result, 'referenceFallback')
    ? result.referenceFallback : session.referenceFallback;
  return { ...session, external: referenceFallback ? [] : external || [], initialComparison: result || {},
    referenceFallback,
    reference_selection: referenceFallback ? 'AUTO' : result.reference_selection || session.reference_selection,
    reference_filename: result?.reference_filename || session.reference_filename || session.defaultReferenceFile,
    personalReference: result?.personalReference ?? session.personalReference,
    analysisResult: result?.analysisResult || session.analysisResult,
    practice: result?.practice ?? session.practice,
    practiceFocuses: result?.practiceFocuses ?? session.practiceFocuses,
    corners: result?.corners || session.corners };
}
export function rememberSession(session, target, reference, external) {
  const key = ['lmu', 'LMU', 'LMU_DUCKDB'].includes(session.provider) ? LMU_SESSION_KEY : LAST_SESSION_KEY;
  try {
    if (!session.filename) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify({ filename: session.filename, target_lap: Number(target), reference_lap: reference ? Number(reference) : null,
      external_reference_files: external, reference_mode: external.length > 1 ? 'AGGREGATE' : external.length ? 'SINGLE' : 'LOCAL',
      ...(session.reference_filename ? { reference_filename: session.reference_filename } : {}),
      ...(session.reference_selection ? { reference_selection: session.reference_selection } : {}) }));
  } catch { /* Browsing still works if local storage is unavailable. */ }
}
// The earlier LMU route returned measurement-only payloads with their own
// fields; recordings analysed by the shared analysis say session.game === 'lmu'
// and use every iRacing view.
export const legacyLmu = (payload = data) => ['lmu', 'LMU', 'LMU_DUCKDB'].includes(payload?.provider)
  && payload?.session?.game !== 'lmu';
export let length = 1;
export let laps = [];
export function activateSession(next) {
  data = next; length = Number.isFinite(next.session.track_length_m) && next.session.track_length_m > 0 ? next.session.track_length_m : 1;
  laps = data.laps.filter(lap => measurableLap(lap) || lap.lap_number === data.defaultTarget);
}
export async function labRequest(route, body = {}, signal) {
  const ticket = generation, controller = new AbortController();
  const cancel = () => controller.abort();
  if (signal?.aborted) cancel();
  signal?.addEventListener('abort', cancel, { once: true });
  pending.add(controller);
  try {
    const result = await requestJson(`${activeGame === 'lmu' ? '/api/lmu/workspace' : '/api/workspace'}/${route}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal });
    if (ticket !== generation || controller.signal.aborted) throw new DOMException('Game selection changed', 'AbortError');
    return result;
  } finally {
    pending.delete(controller); signal?.removeEventListener('abort', cancel);
  }
}
// LMU records steering as % of lock; the shared analysis turns it into an angle
// with the car's wheel range, so the chart turns it back into the recorded %.
const steeringPercent = (deg, half) => finite(deg) && finite(half) && half > 0 ? deg / half * 100 : null;
export function normalizeComparison(payload) {
  const lmu = ['lmu', 'LMU', 'LMU_DUCKDB'].includes(payload.provider);
  const halfRange = data?.session?.steering_half_range_deg;
  const sharedLmu = lmu && finite(halfRange);
  const rows = (payload.trace || []).map(p => ({ ...p, distance: p.distance_pct * length,
    delta: p.delta_s, speed: p.target_speed_kph, referenceSpeed: p.reference_speed_kph,
    brake: finite(p.target_brake) ? p.target_brake * 100 : null, referenceBrake: finite(p.reference_brake) ? p.reference_brake * 100 : null,
    throttle: finite(p.target_throttle) ? p.target_throttle * 100 : null, referenceThrottle: finite(p.reference_throttle) ? p.reference_throttle * 100 : null,
    steering: sharedLmu ? steeringPercent(p.target_steering_deg, halfRange) : lmu ? (finite(p.target_steering_ratio) ? p.target_steering_ratio * 100 : null) : p.target_steering_deg,
    referenceSteering: sharedLmu ? steeringPercent(p.reference_steering_deg, halfRange) : lmu ? (finite(p.reference_steering_ratio) ? p.reference_steering_ratio * 100 : null) : p.reference_steering_deg,
    gear: p.target_gear, referenceGear: p.reference_gear,
    speedBand: finite(p.reference_speed_p25_kph) && finite(p.reference_speed_p75_kph) ? [p.reference_speed_p25_kph, p.reference_speed_p75_kph] : null,
  }));
  const rates = lossRates(rows, length), scale = lossScale(rates);
  return { ...payload, controlEvents: payload.controlEvents?.abs ? { abs: payload.controlEvents.abs } : {}, rows, corners: (payload.corners || []).map(c => ({ ...c, target: c.target || {}, reference: c.reference || {}, evidence: c.evidence || [] })).sort((a, b) => a.apex_pct - b.apex_pct),
    // LMU coordinates are consistent but do not match the real world: no satellite imagery.
    paths: sharedLmu ? { ...projectComparisonPaths(payload.track_path || [], payload.reference_track_path || []), geographic: false, mapStatus: 'schematic' }
    : lmu ? {
      target: schematicPath(payload.track_path), reference: schematicPath(payload.reference_track_path), geographic: false,
      mapStatus: 'schematic',
    } : projectComparisonPaths(payload.track_path || [], payload.reference_track_path || []),
    loss: scale ? { scale, colourAt: pct => lossColour(rateAt(rates, pct), scale) } : null };
}
function schematicPath(points = []) {
  return points.map((p, sourceIndex) => ({ ...p, sourceIndex }))
    .filter(p => finite(p.x_m) && finite(p.y_m) && finite(p.distance_pct)).map((p, index, all) => ({
    ...p, x: p.x_m, y: -p.y_m, time_s: p.elapsed_s,
    breakBefore: Boolean(p.breakBefore || (index > 0 && (p.sourceIndex > all[index - 1].sourceIndex + 1 || p.segment !== all[index - 1].segment))),
  }));
}
export const finite = Number.isFinite;
export const number = (value, digits = 1) => finite(value) ? value.toFixed(digits) : '—';
export const signed = (value, digits = 3) => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}` : '—';
export const lapTime = value => finite(value) ? `${Math.floor(value / 60)}:${(value % 60).toFixed(3).padStart(6, '0')}` : '—';
export function durationTime(value) {
  if (!finite(value) || value < 0) return '—';
  const seconds = Math.floor(value);
  return `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
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
