import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import DrivingSummary from './DrivingSummary';
import PracticeCoach from '../components/PracticeCoach';
import { TrackMap } from './Telemetry';
import { requestJson } from '../analysis/analysisClient';
import { activateSession, selectGame } from './sessionData';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn(), API_BASE: 'http://test.invalid' }));
jest.mock('../components/PracticeCoach', () => jest.fn(() => <div>iRacing 과제 대화상자</div>));
jest.mock('./Telemetry', () => ({ TrackMap: jest.fn(({ schematic, corners }) => <div data-testid="summary-map" data-schematic={String(schematic)}>{corners.map(corner => corner.id).join(',')}</div>) }));

const corners = [1, 2].map(n => ({ id: `geometry-${n}`, label: `구간 ${n}`, entry_pct: .1 * n, apex_pct: .1 * n + .02, exit_pct: .1 * n + .05,
  delta_s: n / 4, target: { min_speed_kph: 90, coast_time_s: .5 }, reference: { min_speed_kph: 92, coast_time_s: .25 } }));
const plan = { mode: 'DRIVING_FOCUS', mode_label: '측정값 확인', required_measured_laps: 3, recommended_measured_laps: 6,
  required_valid_laps: 99, recommended_valid_laps: 99, required_clean_corners: 0, lock: ['차량과 트랙 유지'], success_criteria: ['구간 시간과 입력 편차를 따로 확인'], after_run: '새 측정 랩으로 재확인' };
const comparison = { corners, rows: [], paths: { target: [], reference: [] }, loss: null, reference_label: '기준 1랩', practiceSummary: {
  options: corners.map(corner => ({ focus: { root_cause: `LMU_OBSERVED:${corner.id}`, root_cause_label: `${corner.label} · 관측 시간 차이`,
    corner_id: corner.id, total_observed_loss_s: corner.delta_s, action: '속도·페달 위치를 함께 확인하고 다음 주행에서 반복성을 비교하세요.', success_criteria: ['반복성 확인'] }, corner_ids: [corner.id], next_run: plan })), next_run: plan,
} };
const props = () => ({ comparison, names: {}, onCorner: jest.fn(), onCompare: jest.fn(), onPractice: jest.fn(), analysisWorkspace: { game: 'lmu' } });

beforeEach(() => {
  jest.clearAllMocks(); selectGame('lmu');
  TrackMap.mockImplementation(({ schematic, corners }) => <div data-testid="summary-map" data-schematic={String(schematic)}>{corners.map(corner => corner.id).join(',')}</div>);
  PracticeCoach.mockImplementation(() => <div>iRacing 과제 대화상자</div>);
  activateSession({ provider: 'LMU', filename: 'aaaaaaaaaaaaaaaaaaaaaaaa', defaultTarget: 2, defaultReference: 1,
    session: { track_length_m: 5793 }, laps: [], corners, initialComparison: {}, external: [], practice: { active: { title: '교차 오염 방지용 값' } } });
  requestJson.mockImplementation(async path => path.endsWith('/comparison') ? comparison : { practice: { active: { title: '저장된 과제' } } });
});
afterEach(() => selectGame('iracing'));

test('LMU uses the existing four-card DrivingSummary layout with observation labels and a schematic map', () => {
  render(<DrivingSummary {...props()} />);
  expect(screen.getByRole('region', { name: '주행 요약' })).toHaveClass('driving-summary');
  expect(screen.getByText('오늘의 연습 과제')).toBeInTheDocument();
  expect(screen.getByText('다음 주행에서 할 일')).toBeInTheDocument();
  expect(screen.getByText('연습 위치')).toBeInTheDocument();
  expect(screen.getByText('구간 시간 차이')).toBeInTheDocument();
  expect(screen.getByText('관측 시간 차이')).toBeInTheDocument();
  expect(screen.queryByText('관측 손실')).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: /측정 가능.*3.*권장.*6/ })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /99/ })).not.toBeInTheDocument();
  expect(screen.getByTestId('summary-map')).toHaveAttribute('data-schematic', 'true');
  expect(screen.getByText(/기록 좌표 개략도/)).toBeInTheDocument();
  expect(screen.queryByText(/GPS 주행선/)).not.toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalled();
});

test('both LMU practice buttons navigate without mounting PracticeCoach or calling any iRacing practice API', () => {
  const handlers = props(); render(<DrivingSummary {...handlers} />);
  fireEvent.click(screen.getByRole('button', { name: '기록에서 연습 기준 준비' }));
  fireEvent.click(screen.getByRole('button', { name: '연습 기록·반복 연습' }));
  expect(handlers.onPractice).toHaveBeenCalledTimes(2);
  expect(requestJson).not.toHaveBeenCalled(); expect(PracticeCoach).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('연습 진행 중')).not.toBeInTheDocument();
});

test('selecting another observed difference updates the existing evidence and map cards', () => {
  const handlers = props(); render(<DrivingSummary {...handlers} />);
  fireEvent.click(screen.getByRole('radio', { name: '이번 연습: 구간 2 · 관측 시간 차이' }));
  expect(screen.getByTestId('summary-map')).toHaveTextContent('geometry-2');
  expect(TrackMap.mock.calls.at(-1)[0].selected.id).toBe('geometry-2');
  fireEvent.click(screen.getByRole('button', { name: '코너 비교에서 보기' }));
  expect(handlers.onCorner).toHaveBeenCalledWith('geometry-2');
});

test('insufficient LMU evidence keeps the four cards and the existing compare action without inventing a task', () => {
  const handlers = props();
  render(<DrivingSummary {...handlers} comparison={{ ...comparison, practiceSummary: { options: [], next_run: { ...plan, mode: 'REFERENCE_REQUIRED', title: '비교 기준과 반복 주행을 확인하세요', primary_instruction: '다른 랩·주행 파일을 기준으로 선택하세요' } } }} />);
  expect(screen.getByText('추천할 연습 과제가 없습니다')).toBeInTheDocument();
  expect(screen.getByText('비교 기준과 반복 주행을 확인하세요')).toBeInTheDocument();
  expect(screen.getByText('측정 근거')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '비교 변경' }));
  expect(handlers.onCompare).toHaveBeenCalledTimes(1);
  expect(requestJson).not.toHaveBeenCalled();
});

test('iRacing retains its original valid-lap labels and practice start API/dialog behavior', async () => {
  selectGame('iracing');
  activateSession({ provider: 'IRACING', filename: 'run.ibt', defaultTarget: 2, defaultReference: 1,
    session: { track_length_m: 5793 }, laps: [], corners, initialComparison: {}, external: [] });
  render(<DrivingSummary {...props()} analysisWorkspace={{ game: 'iracing' }} />);
  expect(screen.getByRole('heading', { name: /유효.*99.*권장.*99/ })).toBeInTheDocument();
  expect(screen.getByText('관측 손실')).toBeInTheDocument();
  expect(screen.getByTestId('summary-map')).toHaveAttribute('data-schematic', 'false');
  fireEvent.click(screen.getByRole('button', { name: '이 과제로 연습 시작' }));
  await waitFor(() => expect(requestJson.mock.calls.some(([path]) => path === '/api/practice/start')).toBe(true));
  await screen.findByRole('button', { name: '진행 과제 확인' });
  fireEvent.click(screen.getByRole('button', { name: '연습 기록·반복 연습' }));
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  expect(PracticeCoach).toHaveBeenCalled();
});
