import { fromProductionTrial, toProductionTrial, hydrateSetupWorkflow, emptyWorkflow,
  finishSetupTrial, retryTrial } from './setupWorkflow';

const legacy = () => ({ id: 'existing-trial', started_at: '2026-10-18T00:00:00Z',
  baseline_file: 'before.ibt', candidate_file: 'after.ibt', goal: 'ENDURANCE', session_mode: 'ENDURANCE',
  context: { car_name: 'Car', track_name: 'Track', track_config: 'Layout' },
  adjustment: { setting: 'brake_bias_pct', original_value: 52, value: 53 },
  original_text: '52%', target_text: '53%', reference_mode: 'SINGLE', reference_files: ['cached.csv'],
  feedback: { phase: 'ENTRY', speed: 'LOW', symptom: 'BRAKING', text: 'Original issue' },
  driver_feedback: { change: 'SAME', rating: 3, side_effects: 'Observed result' } });

test('existing trials keep issue, feeling, reference selection and measured values', () => {
  const original = legacy();
  const ui = fromProductionTrial(original);
  expect(ui.issue).toMatchObject({ phase: 'ENTRY', symptom: 'BRAKING', note: 'Original issue', goal: 'ENDURANCE' });
  expect(ui.feedback).toEqual(original.driver_feedback);
  const restored = toProductionTrial(ui);
  expect(restored).toMatchObject({ id: original.id, reference_mode: 'SINGLE', reference_files: ['cached.csv'],
    driver_feedback: original.driver_feedback, original_value: 52, target_value: 53,
    feedback: { text: 'Original issue' },
    context: { car_name: 'Car', track_name: 'Track', track_config: 'Layout' } });
  expect(original.context).not.toHaveProperty('name');
});

test('production trial and history supersede stale UI copies without removing saved drafts', () => {
  const saved = { ...emptyWorkflow(), active: { id: 'stale' }, history: [{ id: 'stale-history' }], drafts: { track: { filename: 'draft.ibt' } } };
  const result = hydrateSetupWorkflow(saved, { activeTrial: null, history: [{ ...legacy(), completed_at: 'done' }] });
  expect(result.active).toBeNull();
  expect(result.history.map(trial => trial.id)).toEqual(['existing-trial']);
  expect(result.drafts).toBe(saved.drafts);
});

test('choosing B from subjective feeling leaves setup effect unconfirmed', () => {
  const active = fromProductionTrial({ ...legacy(), driver_feedback: { change: 'IMPROVED', rating: 5 } });
  const completed = finishSetupTrial({ ...emptyWorkflow(), active }, 'KEEP', 'finished');
  const stored = toProductionTrial(completed.history[0]);
  expect(stored.decision).toBe('KEEP');
  expect(stored.outcome).toBe('OBSERVED');
  expect(stored.effect_status).toBe('UNCONFIRMED');
  expect(stored.driver_feedback.change).toBe('IMPROVED');
});

test('retest preserves original plan and references but discards previous B and result', () => {
  const prior = fromProductionTrial({ ...legacy(), comparison: { verdict: 'KEEP' }, completed_at: 'done', decision: 'RETEST' });
  const retried = retryTrial(prior, 'new-id', 'later');
  const stored = toProductionTrial(retried);
  expect(stored).toMatchObject({ id: 'new-id', parent_id: 'existing-trial', candidate_file: '',
    reference_mode: 'SINGLE', reference_files: ['cached.csv'], comparison: null, effect_status: 'UNCONFIRMED' });
  expect(stored.driver_feedback).toMatchObject({ change: '', rating: null });
});
