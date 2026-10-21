import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import CornerLab from './CornerLab';
import LapAnalysis from './LapAnalysis';
import { activateSession, selectGame } from './sessionData';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./RuntimePreview', () => ({ useLabControls: () => ({}), RuntimeControls: () => null }));
jest.mock('./Telemetry', () => ({ TelemetryChart: () => null, TrackMap: () => null, ControlLegend: () => null }));
jest.mock('../components/CornerMap', () => () => null);
jest.mock('./WorkspaceNotices', () => () => null);
jest.mock('./LmuWorkspacePanel', () => ({ workspace }) => <h2>LMU 목적지: {workspace}</h2>);
jest.mock('./LmuAnalysisPanels', () => ({ __esModule: true, default: ({ mode }) => <div>LMU 심화: {mode}</div>, LmuConsistencyPanel: () => <div>여러 파일의 일관성 선택</div> }));
jest.mock('../components/LapOverlay', () => ({ __esModule: true, default: () => <div>겹쳐 보기</div>, PanBar: () => null }));
jest.mock('../components/LapRangeFilter', () => ({ __esModule: true, default: () => <div data-testid="lap-range-filter">랩 범위</div>, lapsInRange: laps => new Set(laps.map(lap => lap.lap_number)) }));
jest.mock('../components/LapTimeUse', () => () => <div>시간 쓰임새</div>);
jest.mock('./LapRecords', () => () => <div>전체 파일 기록</div>);

const corner = { id: 'C1', label: '첫 구간', entry_pct: .1, apex_pct: .2, exit_pct: .3, delta_s: .2,
  target: { min_speed_kph: 90 }, reference: { min_speed_kph: 92 }, evidence: [] };
const session = { provider: 'LMU', kind: 'LIVE_LOCAL', filename: 'aaaaaaaaaaaaaaaaaaaaaaaa', defaultTarget: 2, defaultReference: 1,
  session: { track_name: 'Monza', car_name: 'GT3', track_length_m: 5793 },
  laps: [1, 2].map(n => ({ lap_number: n, lap_time_s: 117 + n, status: 'VALIDITY_UNKNOWN', measurement_eligible: true, complete: true })),
  corners: [corner], initialComparison: { provider: 'LMU', corners: [corner], trace: [], track_path: [], reference_track_path: [], delta: .2, controlEvents: {} }, external: [] };
beforeEach(() => {
  localStorage.clear(); window.history.replaceState(null, '', '#corners'); selectGame('lmu'); activateSession(session);
  Element.prototype.scrollIntoView = jest.fn();
  requestJson.mockReset(); requestJson.mockImplementation(async path => path.endsWith('/files') ? { files: [] } : { files: [] });
});
afterEach(() => { selectGame('iracing'); window.history.replaceState(null, '', '/'); });

test('LMU corner practice opens the actual history destination and explains the required measured laps', async () => {
  render(<CornerLab game="lmu" onGameChange={jest.fn()} analysisWorkspace={{ game: 'lmu', adoptDesktopAnalysis: jest.fn() }} />);
  fireEvent.click(screen.getByRole('button', { name: '이 코너를 연습 구간으로' }));
  expect(await screen.findByRole('heading', { name: 'LMU 목적지: history' })).toBeInTheDocument();
  expect(window.location.hash).toBe('#history');
  expect(screen.getByText(/C1 반복성을 확인하려면 기록 화면에서/)).toHaveTextContent('측정 랩 3~6개');
  expect(screen.queryByText('연습 구간으로 선택됨')).not.toBeInTheDocument();
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
  expect(requestJson.mock.calls.every(([path]) => !path.includes('/practice/tasks'))).toBe(true);
});

test('LMU consistency and records hide filters that do not apply, while time distribution keeps them', () => {
  const props = { target: '2', names: {}, chosen: ['1', '2'], range: {}, onRange: jest.fn(), onChosen: jest.fn(), onTab: jest.fn(), onCorner: jest.fn() };
  const view = render(<LapAnalysis {...props} tab="consistency" />);
  expect(screen.queryByLabelText('분석할 랩')).not.toBeInTheDocument();
  expect(screen.queryByTestId('lap-range-filter')).not.toBeInTheDocument();
  expect(screen.getByText(/여러 파일의 측정 랩 3~6개를 따로 선택/)).toBeInTheDocument();
  expect(screen.getByText('여러 파일의 일관성 선택')).toBeInTheDocument();
  view.rerender(<LapAnalysis {...props} tab="time" />);
  expect(screen.getByLabelText('분석할 랩')).toBeInTheDocument();
  expect(screen.getByTestId('lap-range-filter')).toBeInTheDocument();
  view.rerender(<LapAnalysis {...props} tab="records" />);
  expect(screen.queryByLabelText('분석할 랩')).not.toBeInTheDocument();
  expect(screen.queryByTestId('lap-range-filter')).not.toBeInTheDocument();
  expect(screen.getByText(/현재 파일 전체를 기준/)).toBeInTheDocument();
});

test('iRacing consistency retains its existing shared lap filters', () => {
  activateSession({ ...session, provider: 'IRACING' });
  render(<LapAnalysis target="2" names={{}} chosen={['1', '2']} range={{}} onRange={jest.fn()} onChosen={jest.fn()} onTab={jest.fn()} onCorner={jest.fn()} tab="consistency" />);
  expect(screen.getByLabelText('분석할 랩')).toBeInTheDocument();
  expect(screen.getByTestId('lap-range-filter')).toBeInTheDocument();
  expect(screen.getByText(/선택 2랩/)).toBeInTheDocument();
});

test.each(['lmu', 'iracing'])('only the LMU setup rail shows the coming soon badge (%s)', async game => {
  selectGame(game);
  activateSession({ ...session, provider: game === 'lmu' ? 'LMU' : 'IRACING' });
  render(<CornerLab game={game} onGameChange={jest.fn()} analysisWorkspace={{ game, adoptDesktopAnalysis: jest.fn() }} />);
  const rail = screen.getByRole('complementary', { name: '작업 보기' });
  const button = within(rail).getByRole('button', { name: /셋업 실험/ });
  if (game === 'lmu') {
    expect(within(button).getByText('준비 중')).toBeInTheDocument();
    fireEvent.click(button);
    expect(screen.getByRole('heading', { name: 'LMU 목적지: setup' })).toBeInTheDocument();
    expect(window.location.hash).toBe('#setup');
  } else {
    expect(within(rail).queryByText('준비 중')).not.toBeInTheDocument();
    expect(button).toHaveAccessibleName('셋업 실험');
  }
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
});

test('the LMU summary route mounts shared DrivingSummary and its history action instead of a separate LMU summary', async () => {
  render(<CornerLab game="lmu" onGameChange={jest.fn()} analysisWorkspace={{ game: 'lmu', adoptDesktopAnalysis: jest.fn() }} />);
  fireEvent.mouseDown(screen.getByRole('tab', { name: '주행 요약' }), { button: 0, ctrlKey: false });
  expect(screen.getByText('오늘의 연습 과제')).toBeInTheDocument();
  expect(screen.getByText('다음 주행에서 할 일')).toBeInTheDocument();
  expect(screen.getByText('연습 위치')).toBeInTheDocument();
  expect(screen.getByText('측정 근거')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'LMU 목적지: summary' })).not.toBeInTheDocument();
  expect(screen.queryByText('LMU 심화: summary')).not.toBeInTheDocument();
  expect(screen.queryByText('시간 쓰임새')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '연습 기록·반복 연습' }));
  expect(await screen.findByRole('heading', { name: 'LMU 목적지: history' })).toBeInTheDocument();
});

test('the existing expert vehicle tab provides braking and channel diagnostics moved out of the summary', async () => {
  render(<CornerLab game="lmu" onGameChange={jest.fn()} analysisWorkspace={{ game: 'lmu', adoptDesktopAnalysis: jest.fn() }} />);
  fireEvent.mouseDown(screen.getByRole('tab', { name: '전문가 보기' }), { button: 0, ctrlKey: false });
  fireEvent.mouseDown(screen.getByRole('tab', { name: '차량 상태·제동·일관성' }), { button: 0, ctrlKey: false });
  expect(await screen.findByText('LMU 심화: summary')).toBeInTheDocument();
  expect(screen.getByText('LMU 심화: expert')).toBeInTheDocument();
});
