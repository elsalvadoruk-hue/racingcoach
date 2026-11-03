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
