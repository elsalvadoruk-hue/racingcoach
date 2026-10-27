import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import CornerLab from './CornerLab';
import { activateSession, selectGame } from './sessionData';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./RuntimePreview', () => ({ useLabControls: () => ({ apiBase: 'http://localhost:24891' }), RuntimeControls: () => null }));
jest.mock('./Telemetry', () => ({ TelemetryChart: () => null, TrackMap: () => null, ControlLegend: () => null }));
jest.mock('../components/CornerMap', () => () => null);
jest.mock('./WorkspaceNotices', () => () => null);
jest.mock('../components/LapOverlay', () => ({ __esModule: true, default: () => null, PanBar: () => null }));
jest.mock('./useDriverJournal', () => () => ({data:{drives:[],candidates:[],challenge_history:[]},busy:false,error:''}));
jest.mock('./DriverResults', () => () => null);

const session = { kind: 'LIVE_LOCAL', filename: 'fixture.ibt', defaultTarget: 2, defaultReference: 1,
  session: { track_name: 'Fixture', car_name: 'GT3', track_length_m: 5000 }, laps: [], corners: [],
  initialComparison: { corners: [], trace: [], track_path: [], reference_track_path: [], controlEvents: {} } };
let originalFetch;
beforeEach(() => {
  localStorage.clear(); window.history.replaceState(null, '', '#driver');
  originalFetch = global.fetch;
  requestJson.mockImplementation(async path => path.endsWith('/session') ? session : { files: [] });
});
afterEach(() => {
  global.fetch = originalFetch; selectGame('iracing'); window.history.replaceState(null, '', '/'); jest.clearAllMocks();
});

test.each(['iracing', 'lmu'])('a completed %s analysis refreshes the mounted driver report through the existing workspace lifecycle', async game => {
  selectGame(game); activateSession({ ...session, provider: game === 'lmu' ? 'LMU' : 'IRACING' });
  let revision = 1;
  global.fetch = jest.fn(async url => ({ ok: true, json: async () => url.includes('/api/driver-history/library?')
    ? { game, status: 'COMPLETE', revision: 1, total: 1, processed: 1, updated: 1, skipped: 0, failed: 0 }
    : { schema_version: 1, game, period_days: 0, state: 'OBSERVED', overview: { title: `관측 ${revision}` } } }));
  const initial = { game, revision: 0, adoptDesktopAnalysis: jest.fn() };
  const view = render(<CornerLab game={game} analysisWorkspace={initial} />);
  await screen.findByRole('heading', { name: '관측 1' });
  view.rerender(<CornerLab game={game} analysisWorkspace={{ ...initial }} />);
  expect(fetch.mock.calls.filter(([url]) => url.includes('/api/driver-report?'))).toHaveLength(1);
  revision = 2;
  view.rerender(<CornerLab game={game} analysisWorkspace={game === 'lmu'
    ? { ...initial, revision: 1 }
    : { ...initial, analysis: { comparison: { target_lap: 2 } }, analysisSnapshot: { filename: session.filename, references: [] } }} />);
  await screen.findByRole('heading', { name: '관측 2' });
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.includes('/api/driver-report?'))).toHaveLength(2));
  expect(window.location.hash).toBe('#driver');
});
