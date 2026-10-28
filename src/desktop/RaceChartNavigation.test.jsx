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
jest.mock('./BattleWorkspace', () => ({ __esModule: true, default: ({ filename, game }) =>
  <section><h2>배틀 분석 화면</h2><p>{filename} · {game}</p></section> }));
jest.mock('./DrivingSummary', () => ({ __esModule: true, default: ({ battleFile }) => <section><h2>요약 화면</h2><p>배틀 파일 {String(battleFile)}</p></section> }));

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

function open(game, sessionType = 'Race') {
  const corner = { id: 'C1', entry_pct: .1, apex_pct: .2, exit_pct: .3, target: {}, reference: {} };
  selectGame(game);
  activateSession({ filename: 'fixture.ibt', provider: game === 'lmu' ? 'LMU' : 'IRACING',
    defaultTarget: 2, defaultReference: 1, session: { session_type: sessionType, track_name: 'Fixture', track_length_m: 5000 },
    laps: [], corners: [corner], initialComparison: { corners: [corner], trace: [], track_path: [], reference_track_path: [], controlEvents: {} } });
  render(<CornerLab game={game} analysisWorkspace={{ game, adoptDesktopAnalysis: jest.fn() }} />);
}

test.each(['iracing', 'lmu'])('a race file has a battle tab inside driving analysis and comes back to the same corner (%s)', async game => {
  open(game);
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
  expect(screen.getByRole('button', { name: '타임라인 C1 선택' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('button', { name: '순위 변화', exact: true })).not.toBeInTheDocument();
  const battle = screen.getByRole('tab', { name: '배틀 분석', exact: true });
  fireEvent.mouseDown(battle, { button: 0, ctrlKey: false });
  await screen.findByRole('heading', { name: '배틀 분석 화면' });
  expect(screen.getByText(`fixture.ibt · ${game}`)).toBeInTheDocument();
  expect(window.location.hash).toBe('#battle');
  const tab = screen.getByRole('tab', { name: '코너 비교', exact: true });
  fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
  await waitFor(() => expect(screen.queryByRole('heading', { name: '배틀 분석 화면' })).not.toBeInTheDocument());
  expect(tab).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: '타임라인 C1 선택' })).toHaveAttribute('aria-pressed', 'true');
  expect(window.location.hash).toBe('#corners');
});

test('a practice file has no battle tab and an old #battle link opens the summary', async () => {
  window.history.replaceState(null, '', '#battle');
  open('iracing', 'Practice');
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
  expect(screen.queryByRole('tab', { name: '배틀 분석', exact: true })).not.toBeInTheDocument();
  expect(screen.getByRole('tab', { name: '주행 요약', exact: true })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('배틀 파일 null')).toBeInTheDocument();
});

test.each([['iracing', 'fixture.ibt'], ['lmu', 'null']])('the summary asks for a battle banner only for iRacing races (%s)', async (game, expected) => {
  window.history.replaceState(null, '', '#summary');
  open(game);
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
  expect(screen.getByText(`배틀 파일 ${expected}`)).toBeInTheDocument();
});
