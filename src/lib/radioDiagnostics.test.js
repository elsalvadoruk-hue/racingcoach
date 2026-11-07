import { createRadioWait, observeRadioWait, snapshotRadioWait } from './radioDiagnostics';

test('queue observations are sampled counts with bounded values and detached snapshots', () => {
  const summary = createRadioWait('lap_time', 100);
  observeRadioWait(summary, 'overlap', 100);
  observeRadioWait(summary, 'audio_busy', 200);
  const snapshot = snapshotRadioWait(summary);
  expect(snapshot).toEqual({ scope: 'QUEUE_OBSERVATIONS', observations: 2, elapsed_ms: 100,
    reasons: { overlap: 1, audio_busy: 1 }, last_reason: 'audio_busy' });
  observeRadioWait(summary, 'overlap', 300);
  expect(snapshot.observations).toBe(2);
  expect(snapshot.reasons.overlap).toBe(1);
  for (let i = 0; i < 70000; i += 1) observeRadioWait(summary, 'overlap', 999999);
  expect(snapshotRadioWait(summary).observations).toBe(65535);
  expect(snapshotRadioWait(summary).reasons.overlap).toBeLessThanOrEqual(65535);
  expect(snapshotRadioWait(summary).elapsed_ms).toBe(120000);
  observeRadioWait(summary, 'device_id', 1000000);
  expect(snapshotRadioWait(summary).last_reason).toBe('overlap');
});

test('only lap and personal best calls carry optional waiting diagnostics', () => {
  expect(createRadioWait('personal_best', 0)).toBeDefined();
  expect(createRadioWait('black', 0)).toBeUndefined();
  expect(snapshotRadioWait(undefined)).toBeUndefined();
  expect(snapshotRadioWait(createRadioWait('lap_time', 0))).toBeUndefined();
});
