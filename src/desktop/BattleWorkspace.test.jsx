import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import BattleWorkspace from './BattleWorkspace';
import { forgetBattle, loadBattle } from './battleReport';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./RacePositionChart', () => ({ __esModule: true, default: ({ filename, onClose }) =>
  <section><h2>경기 순위 변화</h2><p>{filename}{onClose ? ' · 닫기' : ''}</p></section> }));

const ring = Array.from({ length: 500 }, (_, i) => { const a = i / 500 * 2 * Math.PI; return [300 * Math.cos(a), 300 * Math.sin(a)]; });
const normal = ring.map(([x, y]) => [x / 300, y / 300]);
const frame = (t, gap = 8, beside = null) => ({ t, me: [300 * Math.cos(t / 10), 300 * Math.sin(t / 10)], speed_kph: 108, brake: 0, throttle: .6,
  gear: 4, steer_deg: 5, pct: t / (20 * Math.PI), gap_m: gap, gap_clamped: false, beside, rival_pct: (30 * t + gap) / (600 * Math.PI), rival_lat: [-1, 1] });
const trajectory = (factor = 0) => Array.from({ length: 21 }, (_, i) => {
  const t = 1 + i / 10, distance = 30 * (t - 1) - factor * (t - 1) ** 2;
  const f = frame((30 + distance) / 30, 8 + factor * (t - 1) ** 2);
  return { ...f, t, speed_kph: 108 - factor * (t - 1) * 7.2, distance_m: distance,
    rival: frame(t + 8 / 30).me };
});
const alternative = (id, label, factor) => ({ id, label, status: 'MODEL_UNCERTAIN', rankable: false,
  start_t: 1, end_t: 3, horizon_s: 2, trajectory: trajectory(factor), progress_range_m: [-5, -1], position_error_m: 3,
  responses: ['track_pace', 'brakes', 'holds_speed'].map((id, i) => ({ id, trajectory: trajectory(factor + i * .2), baseline: trajectory(i * .2) })) });
const alternatives = [alternative('follow', '앞차에 맞춰 진입', .5), alternative('earlier_brake', '제동을 먼저 시작', 1)];
const corner = { corner: 'C1', label: 'T1', lap: 6, t_entry: 2, gap_entry_m: 6, alternatives,
  simulation: { status: 'READY', start_t: 1, end_t: 3,
    baselines: ['track_pace', 'brakes', 'holds_speed'].map((id, i) => ({ id, trajectory: trajectory(i * .2) })), baseline_error_m: 3,
    validation: { distance_p90_m: 2 }, omitted: [{ id: 'lift' }] } };
const report = {
  status: 'READY', laps_used: 8, enough_laps: true,
  calibration: { edges: 12, median_m: 4.9, p10_p90_m: [4.6, 5.2], bias_m: .26 },
  assumptions: { car_length_m: 4.64, car_width_m: 2.05, side_max_m: 4.6, lateral_rate_mps: 2.5 },
  geometry: { ref: ring, normal, band_left: ring, band_right: ring, grid: 500 },
  scenes: [
    { id: 'B1', rival: 7, lap: 6, t0: 0, t1: 8, outcome: 'passed', beside_s: 1.5,
      frames: Array.from({ length: 81 }, (_, i) => frame(i / 10, 8 - i / 8, i > 20 && i < 40 ? 'L' : null)),
      corners: [corner, { corner: 'C2', label: 'T2', lap: 6, t_entry: 5, alternatives: [], why_not: 'INSUFFICIENT_DYNAMICS' }] },
    { id: 'B2', rival: 9, lap: 2, t0: 20, t1: 30, outcome: 'held', beside_s: 0,
      frames: [frame(20, -5), frame(25, -6), frame(30, -7)], corners: [] },
  ],
  summary: { battles: 2, side_by_side: 1, passed: 1, lost: 0, top: null },
};

beforeEach(() => {
  forgetBattle();
  global.ResizeObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { this.callback([{ target, contentRect: { width: 800, height: 400 } }]); }
    disconnect() {}
  };
});
afterEach(() => { jest.clearAllMocks(); jest.useRealTimers(); });

test('shows conditional actions, errors and the actual corner without substituting another lap', async () => {
  requestJson.mockResolvedValue(report);
  const onOpenCorner = jest.fn();
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={onOpenCorner} />);
  expect(await screen.findByText('배틀 2번 · 나란히 1 · 추월 1 · 추월당함 0')).toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledWith('/api/workspace/battle', expect.objectContaining({ body: JSON.stringify({ filename: 'race.ibt' }) }));
  const card = screen.getByRole('article', { name: 'T1 6랩 대응 비교' });
  expect(card).toHaveTextContent('오차가 커 우열 보류');
  expect(card).toHaveTextContent('기본 대응 모형');
  expect(card).toHaveTextContent('위치 차이 3.0m');
  fireEvent.click(within(card).getByRole('button', { name: /실제 코너 보기/ }));
  expect(onOpenCorner).toHaveBeenCalledWith(expect.objectContaining({ corner: 'C1', lap: 6 }), 6);
  fireEvent.click(screen.getByText('대안을 계산하지 못한 코너 1곳'));
  expect(screen.getByText(/페달과 속도 변화 자료가 부족/)).toBeInTheDocument();
  expect(screen.queryByText(/초 빨리/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /2랩 · 상대 #9/ }));
  expect(screen.getByText(/구별되는 대안을 계산하지 못했습니다/)).toBeInTheDocument();
});

test('draws same-time ghost cars and paths; action and rival response change the visual result', async () => {
  requestJson.mockResolvedValue(report);
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  const map = await screen.findByRole('img', { name: '내 차 중심의 배틀 장면' });
  const slider = screen.getByRole('slider', { name: '장면 시간' });
  expect(slider).toHaveValue('10');
  expect(within(map).getByLabelText('같은 시각의 대안 차량')).toHaveAttribute('data-time', '1');
  fireEvent.change(slider, { target: { value: '20' } });
  const ghost = () => within(map).getByLabelText('대안 내 차').getAttribute('points');
  const first = ghost(), actual = within(map).getByLabelText('실제 내 차').getAttribute('points');
  const path = within(map).getByLabelText('선택한 대응 경로').getAttribute('points');
  fireEvent.click(screen.getByRole('radio', { name: '제동을 먼저 시작' }));
  fireEvent.change(slider, { target: { value: '20' } });
  expect(ghost()).not.toEqual(first);
  expect(within(map).getByLabelText('실제 내 차')).toHaveAttribute('points', actual);
  expect(within(map).getByLabelText('선택한 대응 경로').getAttribute('points')).not.toEqual(path);
  const second = ghost();
  fireEvent.change(screen.getByRole('combobox', { name: '상대 반응 가정' }), { target: { value: 'brakes' } });
  expect(ghost()).not.toEqual(second);
  expect(within(map).getByLabelText('같은 시각의 대안 차량')).toHaveAttribute('data-time', '2');
  fireEvent.change(slider, { target: { value: '40' } });
  expect(within(map).queryByLabelText('대안 내 차')).toBeNull();
  expect(screen.getByText(/대안 계산 구간 밖/)).toBeInTheDocument();
});

test('comparison playback stops at the supported horizon', async () => {
  jest.useFakeTimers();
  requestJson.mockResolvedValue(report);
  const { unmount } = render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  await screen.findByRole('button', { name: '비교 재생' });
  fireEvent.click(screen.getByRole('button', { name: '비교 재생' }));
  act(() => { jest.advanceTimersByTime(5000); });
  expect(screen.getByRole('slider', { name: '장면 시간' })).toHaveValue('30');
  expect(screen.getByRole('button', { name: '재생', exact: true })).toHaveAttribute('aria-pressed', 'false');
  unmount();
  jest.useRealTimers();
});

test('says why there is nothing to show and can look again', async () => {
  requestJson.mockResolvedValueOnce({ status: 'UNAVAILABLE', reason: 'NO_MATCHING_TRAFFIC_LOG' }).mockResolvedValueOnce(report);
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  expect(await screen.findByText(/시간이 맞는 주변 차량 기록을 찾지 못했습니다/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '다시 확인' }));
  expect(await screen.findByText(/배틀 2번/)).toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledTimes(2);
});

test('compact playback controls retain speed selection, scrubbing and graph seeking', async () => {
  requestJson.mockResolvedValue(report);
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  const speed = await screen.findByRole('combobox', { name: '재생 속도' });
  expect(speed).toHaveValue('0.5');
  expect(within(speed).getAllByRole('option').map(option => option.value)).toEqual(['0.25', '0.5', '1']);
  fireEvent.change(speed, { target: { value: '1' } });
  expect(speed).toHaveValue('1');
  const time = screen.getByRole('slider', { name: '장면 시간' });
  fireEvent.change(time, { target: { value: '80' } });
  expect(time).toHaveValue('80');
  expect(screen.getByRole('button', { name: '재생', exact: true })).toHaveAttribute('aria-pressed', 'false');
  const graph = screen.getByRole('img', { name: '앞뒤 간격과 페달' });
  jest.spyOn(graph, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 800 });
  fireEvent.click(graph, { clientX: 416 });
  expect(time).toHaveValue('40');
  expect(screen.getByRole('heading', { name: '배틀 분석', exact: true })).toBeInTheDocument();
});

test('a failed request offers a retry', async () => {
  requestJson.mockRejectedValueOnce(new Error('서버 연결 실패')).mockResolvedValueOnce(report);
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  expect(await screen.findByText('서버 연결 실패')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));
  expect(await screen.findByText(/배틀 2번/)).toBeInTheDocument();
});

test('few clean laps are pointed out', async () => {
  requestJson.mockResolvedValue({ ...report, laps_used: 3, enough_laps: false });
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  expect(await screen.findByText(/완주·비피트 랩이 3개뿐/)).toBeInTheDocument();
});

test('LMU shows the position chart and does not ask for battle scenes', async () => {
  requestJson.mockResolvedValue({});
  render(<BattleWorkspace filename="race.duckdb" game="lmu" onOpenCorner={jest.fn()} />);
  expect(await screen.findByRole('heading', { name: '경기 순위 변화' })).toBeInTheDocument();
  expect(screen.getByText('race.duckdb')).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByRole('tab', { name: '배틀 장면' }), { button: 0, ctrlKey: false });
  expect(await screen.findByText('LMU 배틀 장면은 준비 중입니다.')).toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalledWith('/api/lmu/workspace/battle', expect.anything());
});

test('one report is shared, a missing one is asked for again', async () => {
  const load = jest.fn().mockResolvedValueOnce({ status: 'UNAVAILABLE' }).mockResolvedValue(report);
  await loadBattle('a.ibt', 'iracing', undefined, load);
  await loadBattle('a.ibt', 'iracing', undefined, load);
  await loadBattle('a.ibt', 'iracing', undefined, load);
  expect(load).toHaveBeenCalledTimes(2);
  const controller = new AbortController();
  const pending = loadBattle('b.ibt', 'iracing', controller.signal, () => new Promise(() => {}));
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});
