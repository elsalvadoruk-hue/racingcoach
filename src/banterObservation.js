// Read the current audio state only. No playback, priority, storage or synthesis calls.
export function banterObservation(candidate, frame, output, pending) {
  let audioBusy = null;
  try { audioBusy = typeof output.audioBusy === 'function' ? output.audioBusy() : null; } catch { /* Unknown is deferred. */ }
  const bool = value => typeof value === 'boolean' ? value : null;
  return { ...candidate, session_time: frame.radio.session_time,
    audio_busy: bool(audioBusy), engineer_busy: bool(output.busy),
    pending_audio: Boolean(pending || frame.events?.length || frame.radio?.events?.length),
    muted: typeof output.muted === 'boolean' ? output.muted || output.volume === 0 : null };
}
