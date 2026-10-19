import { analysisOptions, currentSessionResult } from './sessionData';
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

test('the app keeps its summary start page and explicit desktop navigation', () => {
  expect(workspaceFromHash('')).toBe('summary');
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
