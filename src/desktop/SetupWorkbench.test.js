import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import SetupWorkbench from './SetupWorkbench';
import { WORKFLOW_KEY, useTrialWorkflow } from '../components/TrialWorkflow';
import { labRequest } from './sessionData';

jest.mock('./sessionData', () => ({
  data: { filename: 'before.ibt', defaultTarget: 2, defaultReference: 1,
    session: { car_name: 'Car', track_name: 'Track', track_config: 'Layout' } },
  labRequest: jest.fn(), signed: value => String(value ?? '—'), number: value => String(value ?? '—'),
}));

function Harness() {
  const workflow = useTrialWorkflow();
  return <SetupWorkbench analysisWorkspace={{ workflow }} onChooseSource={() => {}} onCorner={() => {}} />;
}

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(global, 'crypto', { configurable: true, value: { randomUUID: () => 'retry-id' } });
  labRequest.mockImplementation(async route => route === 'files' ? { files: [
    { name: 'before.ibt', car_name: 'Car', track_name: 'Track', track_config: 'Layout' },
    { name: 'after.ibt', car_name: 'Car', track_name: 'Track', track_config: 'Layout' },
  ] } : route === 'setup-plan' ? { corners: [], session: {}, setup: {} } : {
    verdict: 'INCONCLUSIVE', reason: 'Not enough laps', plan_matches: true, memory_record: { saved: true },
  });
});

test('resumed production trial attaches B, records feeling and comparison, and retests in the established workflow', async () => {
  localStorage.setItem(WORKFLOW_KEY, JSON.stringify({ version: 1,
    preferences: { selectedFile: 'before.ibt', output: 'keep' }, drafts: { unrelated: { text: 'keep' } }, history: [],
    activeTrial: { id: 'legacy-id', baseline_file: 'before.ibt', candidate_file: null,
      goal: 'COMFORT', reference_mode: 'SINGLE', reference_files: ['cached.csv'],
      context: { car_name: 'Car', track_name: 'Track', track_config: 'Layout' },
      adjustment: { setting: 'brake_bias_pct', original_value: 52, value: 53 },
      feedback: { phase: 'ENTRY', speed: 'LOW', symptom: 'BRAKING', text: 'Original note' },
      driver_feedback: { change: '', rating: null, side_effects: '' } },
  }));
  render(<Harness />);
  expect(screen.getByLabelText('상황 메모')).toHaveValue('Original note');
  expect(screen.getByLabelText('변경 전 주행 · A')).toBeDisabled();
  await waitFor(() => expect(screen.getByLabelText('변경 후 새 주행 · B').options).toHaveLength(2));
  fireEvent.change(screen.getByLabelText('변경 후 새 주행 · B'), { target: { value: 'after.ibt' } });
  fireEvent.click(within(screen.getByRole('radiogroup', { name: '처음 느낀 증상은?' })).getByRole('radio', { name: '줄었음' }));
  fireEvent.click(within(screen.getByRole('radiogroup', { name: '운전 편의 · 1 어려움 / 5 편함' })).getByRole('radio', { name: '4' }));
  fireEvent.click(screen.getByRole('button', { name: 'A/B 측정 결과 확인' }));
  await screen.findByTestId('setup-findings');
  expect(labRequest).toHaveBeenCalledWith('setup-compare', expect.objectContaining({
    baseline_file: 'before.ibt', candidate_file: 'after.ibt', trial_id: 'legacy-id',
    reference_mode: 'SINGLE', reference_files: ['cached.csv'], driver_feedback: expect.objectContaining({ change: 'IMPROVED', rating: 4 }),
  }), expect.any(AbortSignal));
  fireEvent.click(screen.getByRole('button', { name: '결과 기록·다음 실험 준비' }));
  const stored = JSON.parse(localStorage.getItem(WORKFLOW_KEY));
  expect(stored.activeTrial).toMatchObject({ id: 'retry-id', candidate_file: '', reference_files: ['cached.csv'] });
  expect(stored.history).toHaveLength(1);
  expect(stored.history[0]).toMatchObject({ id: 'legacy-id', decision: 'RETEST',
    comparison: { verdict: 'INCONCLUSIVE' }, driver_feedback: { change: 'IMPROVED', rating: 4 } });
  expect(stored.preferences.output).toBe('keep');
  expect(stored.drafts.unrelated.text).toBe('keep');
});
