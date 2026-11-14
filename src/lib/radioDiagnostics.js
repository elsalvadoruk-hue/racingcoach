// Ephemeral page-local identity; never derived from a device, account or recording.
const pageOwner = `radio-${Math.random().toString(36).slice(2, 12)}`;
let serial = 0;

export function createRadioAttempt(game, generation = 0) {
  serial += 1;
  return { attempt_id: `${pageOwner}:${serial}`, owner_generation: `${pageOwner}:${generation}`,
    game, created_at: Date.now() / 1000 };
}

export function radioDiagnostic(attempt, stage, started = false, details = {}) {
  return { ...attempt, stage, started, elapsed_ms: Math.max(0, Date.now() - attempt.created_at * 1000), ...details };
}

// Counts describe observed queue gates, not time spent or original SDK decisions.
const WAIT_REASONS = new Set(['radio_disconnected', 'overlap', 'stale_frame', 'processing',
  'playback_pending', 'preparing_audio', 'retry_backoff', 'alerts_disabled', 'close_battle',
  'level_filtered', 'specialists_disabled', 'coach_busy', 'audio_busy', 'workload',
  'call_spacing', 'render_busy', 'queue_priority', 'eligible', 'render_deferred',
  'audio_unavailable', 'playback_declined']);

export function createRadioWait(key, now) {
  return ['lap_time', 'personal_best'].includes(key)
    ? { first: now, observations: 0, elapsed_ms: 0, reasons: {}, last_reason: null } : undefined;
}

export function observeRadioWait(wait, reason, now = Date.now()) {
  if (!wait || !WAIT_REASONS.has(reason)) return;
  wait.observations = Math.min(65535, wait.observations + 1);
  wait.reasons[reason] = Math.min(65535, (wait.reasons[reason] || 0) + 1);
  wait.elapsed_ms = Math.min(120000, Math.max(0, now - wait.first));
  wait.last_reason = reason;
}

export function snapshotRadioWait(wait) {
  return wait?.observations ? { scope: 'QUEUE_OBSERVATIONS', observations: wait.observations,
    elapsed_ms: wait.elapsed_ms, reasons: { ...wait.reasons }, last_reason: wait.last_reason,
    ...(wait.preparation ? { preparation: { ...wait.preparation } } : {}) } : undefined;
}

export function observeRadioPreparation(wait, status, now = Date.now()) {
  if (!wait) return;
  const elapsed = Math.min(120000, Math.max(0, now - wait.first));
  wait.preparation = status === 'requested'
    ? { requests: Math.min(255, (wait.preparation?.requests || 0) + 1), requested_ms: elapsed, status }
    : { ...wait.preparation, response_ms: elapsed, status };
}
