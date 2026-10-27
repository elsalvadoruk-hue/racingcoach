import { analysisOptions, currentSessionResult, labRequest, selectGame, rememberSession, LMU_SESSION_KEY, LAST_SESSION_KEY, normalizeComparison } from './sessionData';
import { workspaceFromHash } from './labControlState';

test('a new comparison updates the summary, practice and corner context together', () => {
  const session = { filename: 'run.ibt', analysisResult: { old: true }, practice: { id: 'old' },
    practiceFocuses: ['old'], corners: [{ id: 'old' }] };
  const result = { analysisResult: { lap: 4 }, practice: { id: 'current' }, practiceFocuses: ['C2'], corners: [{ id: 'C2' }] };
  const next = currentSessionResult(session, result, ['reference.csv']);
  expect(next).toMatchObject({ external: ['reference.csv'], analysisResult: { lap: 4 },
    practice: { id: 'current' }, practiceFocuses: ['C2'], corners: [{ id: 'C2' }] });
  expect(next.initialComparison).toBe(result);
  expect(session.practice.id).toBe('old');
});

test('initial traces without duplicated analysis retain the root production result', () => {
  const analysisResult = { lap: 3 }, practice = { current: null };
  const next = currentSessionResult({ analysisResult, practice, corners: [] }, { trace: [] }, ['a.csv', 'b.csv']);
  expect(next.analysisResult).toBe(analysisResult);
  expect(next.practice).toBe(practice);
  expect(next.external).toEqual(['a.csv', 'b.csv']);
});

test('Garage61 fallback clears the external identity and later successful selection clears fallback', () => {
  const fallback = currentSessionResult({ filename: 'a.ibt' },
    { referenceFallback: 'GARAGE61_UNAVAILABLE', reference_filename: 'b.ibt' }, ['old.csv']);
  expect(fallback.external).toEqual([]);
  expect(fallback.reference_selection).toBe('AUTO');
  expect(fallback.reference_filename).toBe('b.ibt');
  const external = currentSessionResult(fallback, { referenceFallback: null }, ['ready.csv']);
  expect(external.external).toEqual(['ready.csv']);
  expect(external.referenceFallback).toBeNull();
});

test('the app starts on the driver report and keeps explicit desktop navigation', () => {
  expect(workspaceFromHash('')).toBe('driver');
  expect(workspaceFromHash('#corners')).toBe('corners');
  expect(workspaceFromHash('#guide-setup')).toBe('guide');
  expect(workspaceFromHash('#unexpected')).toBe('summary');
});

test('comparison requests retain evidence mode and structured driver feedback', () => {
  const workspace = { selectedFile: 'A.ibt', sessionMode: 'ENDURANCE', pitRule: 'CONCURRENT',
    setupMode: 'EVIDENCE', feedbackPhase: 'EXIT', feedbackSpeed: 'HIGH', feedbackSymptom: 'OVERSTEER', userFeedback: 'exit note',
    workflow: { readDraft: () => ({ setupMode: 'EXPLORE', feedbackPhase: 'ENTRY' }) } };
  expect(analysisOptions(workspace, 'A.ibt')).toMatchObject({ session_mode: 'ENDURANCE', pit_rule: 'CONCURRENT',
    setup_mode: 'EVIDENCE', feedback_phase: 'EXIT', feedback_symptom: 'OVERSTEER', user_feedback: 'exit note' });
  expect(analysisOptions(workspace, 'B.ibt')).toMatchObject({ setup_mode: 'EXPLORE', feedback_phase: 'ENTRY' });
  expect(analysisOptions(workspace, 'B.ibt').session_mode).toBeUndefined();
});

test('a response from the previous game cannot activate a new game session', async () => {
  selectGame('lmu');
  let complete;
  global.fetch = jest.fn(() => new Promise(resolve => { complete = resolve; }));
  const pending = labRequest('session', { filename: 'lmu-id' });
  selectGame('iracing');
  complete({ ok: true, json: async () => ({ filename: 'old-lmu-response' }) });
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  expect(global.fetch.mock.calls[0][0]).toContain('/api/lmu/workspace/session');
  delete global.fetch;
});

test('LMU remembers selection separately and retains schematic coordinate gaps', () => {
  localStorage.setItem(LAST_SESSION_KEY, 'existing-iracing-selection');
  rememberSession({ filename: 'lmu-id', provider: 'LMU' }, 5, 4, []);
  expect(localStorage.getItem(LAST_SESSION_KEY)).toBe('existing-iracing-selection');
  expect(JSON.parse(localStorage.getItem(LMU_SESSION_KEY)).target_lap).toBe(5);
  const result = normalizeComparison({ provider: 'LMU', track_path: [
    { x_m: 1, y_m: 2, distance_pct: .1, segment: 0 },
    { x_m: 2, y_m: 3, distance_pct: .2, segment: 1 },
  ] });
  expect(result.paths.target[1]).toMatchObject({ x: 2, y: -3, breakBefore: true });
  expect(result.paths.geographic).toBe(false);
});
