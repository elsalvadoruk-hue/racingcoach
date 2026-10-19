import { desktopDestination, requestedCornerLocation, openVoiceCorner } from './desktopNavigation';

test('legacy manual links keep their destination and the settings/history anchor', () => {
  expect(desktopDestination('ab-manager')).toMatchObject({ workspace: 'setup' });
  expect(desktopDestination('external-reference')).toEqual({ workspace: 'settings', anchor: 'external-reference' });
  expect(desktopDestination('personalization')).toEqual({ workspace: 'history', anchor: 'personalization' });
  expect(desktopDestination('practice-coach')).toEqual({ workspace: 'summary', anchor: 'today-task-title' });
  expect(desktopDestination('closed-loop-coach')).toEqual({ workspace: 'summary', anchor: 'next-run-title' });
  expect(desktopDestination('lap-overlay')).toEqual({ workspace: 'expert', anchor: null });
  expect(desktopDestination('guide-voice')).toEqual({ workspace: 'guide', anchor: 'guide-voice' });
});

test('a voice request resolves observed distance including zero and wraparound without inventing a location', () => {
  const corners = [{ id: 'C1', entry_pct: 0, exit_pct: .1, apex_pct: .05 }, { id: 'C2', entry_pct: .4, exit_pct: .6, apex_pct: .5 }, { id: 'C3', entry_pct: .9, exit_pct: .01, apex_pct: .97 }];
  expect(requestedCornerLocation(corners, { distance_pct: 0 })).toMatchObject({ id: 'C1', pct: 0 });
  expect(requestedCornerLocation(corners, { distance_pct: .95 })).toMatchObject({ id: 'C3', pct: .95 });
  expect(requestedCornerLocation(corners, { corner_id: 'C2' })).toMatchObject({ id: 'C2', pct: .5 });
  expect(requestedCornerLocation(corners, {})).toBeNull();
});

function workspace() {
  return { loading: false, activeTrial: null, comparison: { target_lap: 2 }, referenceLap: '3', setError: jest.fn(),
    runAnalysis: jest.fn(async () => ({ comparison: { target_lap: 3 } })), setTargetLapManual: jest.fn(), setReferenceLapManual: jest.fn(), navigateToSection: jest.fn() };
}

test('a voice note on another lap requests that lap and preserves its exact destination across the session reload', async () => {
  const w = workspace();
  expect(await openVoiceCorner(w, { lap_number: 3, corner_id: 'C7', distance_pct: .42 })).toBe(true);
  expect(w.runAnalysis).toHaveBeenCalledWith({ includeAI: false, persistMemory: false, targetLapOverride: 3, referenceLapOverride: null });
  expect(w.setReferenceLapManual).toHaveBeenCalledWith(false);
  expect(w.navigateToSection).toHaveBeenCalledWith('corner-inputs', { id: 'C7', distance_pct: .42, lap_number: 3 });
});

test('trial, analysis in flight, missing lap and unusable note lap cannot change the displayed comparison', async () => {
  for (const patch of [{ activeTrial: {} }, { loading: true }]) {
    const w = Object.assign(workspace(), patch);
    expect(await openVoiceCorner(w, { lap_number: 3 })).toBe(false);
    expect(w.runAnalysis).not.toHaveBeenCalled(); expect(w.navigateToSection).not.toHaveBeenCalled();
  }
  const w = workspace();
  expect(await openVoiceCorner(w, {})).toBe(false);
  w.runAnalysis.mockResolvedValue({ comparison: { target_lap: 2 } });
  expect(await openVoiceCorner(w, { lap_number: 3 })).toBe(false);
  expect(w.navigateToSection).not.toHaveBeenCalled();
});
