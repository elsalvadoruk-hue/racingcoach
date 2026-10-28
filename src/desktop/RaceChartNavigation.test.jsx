import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import CornerLab from './CornerLab';
import { activateSession, selectGame } from './sessionData';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./RuntimePreview', () => ({ useLabControls: () => ({}), RuntimeControls: () => null }));
jest.mock('../components/CornerMap', () => () => null);
jest.mock('./WorkspaceNotices', () => () => null);
jest.mock('../components/LapOverlay', () => ({ __esModule: true, default: () => null, PanBar: () => null }));
jest.mock('./RacePositionChart', () => ({ __esModule: true, default: ({ onClose }) =>
  <section><h2>경기 순위 변화</h2><button onClick={onClose}>분석으로 돌아가기</button></section> }));

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, '', '#corners');
  requestJson.mockResolvedValue({ files: [] });
  global.ResizeObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { this.callback([{ target, contentRect: { width: 1000, height: 100 } }]); }
    disconnect() {}
  };
});

afterEach(() => {
  selectGame('iracing');
  window.history.replaceState(null, '', '/');
  jest.clearAllMocks();
});

test.each(['iracing', 'lmu'])('clicking the selected analysis tab closes the race chart and preserves the corner (%s)', async game => {
  const corner = { id: 'C1', entry_pct: .1, apex_pct: .2, exit_pct: .3, target: {}, reference: {} };
  selectGame(game);
  activateSession({ filename: 'fixture.ibt', provider: game === 'lmu' ? 'LMU' : 'IRACING',
    defaultTarget: 2, defaultReference: 1, session: { session_type: 'Race', track_name: 'Fixture', track_length_m: 5000 },
    laps: [], corners: [corner], initialComparison: { corners: [corner], trace: [], track_path: [], reference_track_path: [], controlEvents: {} } });
  render(<CornerLab game={game} analysisWorkspace={{ game, adoptDesktopAnalysis: jest.fn() }} />);
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
  expect(screen.getByRole('button', { name: '타임라인 C1 선택' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '순위 변화', exact: true }));
  await screen.findByRole('heading', { name: '경기 순위 변화' });
  const tab = screen.getByRole('tab', { name: '코너 비교', exact: true });
  expect(tab).toHaveAttribute('aria-selected', 'true');
  fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
  fireEvent.click(tab);
  await waitFor(() => expect(screen.queryByRole('heading', { name: '경기 순위 변화' })).not.toBeInTheDocument());
  expect(tab).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: '타임라인 C1 선택' })).toHaveAttribute('aria-pressed', 'true');
  expect(window.location.hash).toBe('#corners');
});
