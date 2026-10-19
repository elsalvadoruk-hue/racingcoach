const routes = {
  'practice-coach': ['summary', 'today-task-title'], 'closed-loop-coach': ['summary', 'next-run-title'], 'run-summary': ['summary'],
  'voice-review': ['history', 'voice-review'], 'voice-review-panel': ['history', 'voice-review-panel'],
  personalization: ['history', 'personalization'], memory: ['history', 'memory-history'],
  'external-reference': ['settings', 'external-reference'], 'overlay-settings': ['settings', 'overlay-settings'],
  'live-coach': ['live'], 'user-guide': ['guide'], 'lap-overlay': ['expert'],
  'lap-time-use': ['laps'], 'lap-detail': ['laps'], 'corner-consistency': ['laps'],
  'corner-inputs': ['corners'], comparison: ['corners'], priorities: ['corners'], 'corner-table': ['corners'],
  'ab-manager': ['setup'], briefing: ['setup'], 'engineer-briefing': ['setup'], 'setup-decision': ['setup'],
  decision: ['setup'], 'tire-snapshot': ['setup'], 'ride-aero': ['setup'], 'damper-velocity': ['setup'], 'gg-diagram': ['setup'],
};
const workspaces = ['summary', 'corners', 'laps', 'expert', 'setup', 'live', 'history', 'settings', 'guide'];

export function desktopDestination(section) {
  const id = typeof section === 'object' ? section?.id : section;
  if (typeof id !== 'string') return { workspace: 'summary', anchor: null };
  if (id === 'corner-names') return { workspace: 'corners', anchor: null, names: true };
  if (id.startsWith('guide-')) return { workspace: 'guide', anchor: id };
  const route = routes[id] || [workspaces.includes(id) ? id : 'summary'];
  return { workspace: route[0], anchor: route[1] || null };
}

export function requestedCornerLocation(corners, request) {
  if (!request) return null;
  const pct = typeof request.distance_pct === 'number' && Number.isFinite(request.distance_pct)
    ? Math.max(0, Math.min(1, request.distance_pct)) : null;
  const byId = corners.find(corner => corner.id === (request.id || request.corner_id));
  const contains = pct === null ? null : corners.find(corner => corner.entry_pct <= corner.exit_pct
    ? pct >= corner.entry_pct && pct <= corner.exit_pct : pct >= corner.entry_pct || pct <= corner.exit_pct);
  const distance = corner => { const delta = Math.abs(corner.apex_pct - pct); return Math.min(delta, 1 - delta); };
  const corner = contains || byId || (pct !== null ? [...corners].sort((a, b) => distance(a) - distance(b))[0] : null);
  return corner ? { id: corner.id, pct: pct ?? corner.apex_pct, corner } : null;
}

export async function openVoiceCorner(workspace, note) {
  if (workspace.activeTrial || workspace.loading) {
    workspace.setError?.('진행 중 실험의 랩과 기준을 유지합니다. 실험 종료 후 메모 랩을 비교하세요.');
    return false;
  }
  const lap = note?.lap_number == null ? NaN : Number(note.lap_number);
  if (!Number.isInteger(lap) || lap < 0) { workspace.setError?.('메모를 남긴 랩을 먼저 확인해 주세요.'); return false; }
  if (Number(workspace.comparison?.target_lap) !== lap) {
    const sameAsReference = Number(workspace.referenceLap) === lap;
    const result = await workspace.runAnalysis({ includeAI: false, persistMemory: false, targetLapOverride: lap,
      ...(sameAsReference ? { referenceLapOverride: null } : {}) });
    if (!result || Number(result.comparison?.target_lap) !== lap) {
      workspace.setError?.('이 랩은 비교 대상으로 사용할 수 없습니다. 대화 카드의 해당 랩 관측을 확인하세요.');
      return false;
    }
    workspace.setTargetLapManual?.(true);
    if (sameAsReference) workspace.setReferenceLapManual?.(false);
  }
  workspace.navigateToSection('corner-inputs', { id: note.corner_id || note.id, distance_pct: note.distance_pct, lap_number: lap });
  return true;
}
