import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import BattleWorkspace from './BattleWorkspace';
import { forgetBattle, loadBattle } from './battleReport';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./RacePositionChart', () => ({ __esModule: true, default: ({ filename, onClose }) =>
  <section><h2>경기 순위 변화</h2><p>{filename}{onClose ? ' · 닫기' : ''}</p></section> }));

const ring = Array.from({ length: 500 }, (_, i) => { const a = i / 500 * 2 * Math.PI; return [300 * Math.cos(a), 300 * Math.sin(a)]; });
const normal = ring.map(([x, y]) => [x / 300, y / 300]);
const frame = (t, gap, beside = null) => ({ t, me: [300 * Math.cos(t / 10), 300 * Math.sin(t / 10)], speed_kph: 150, brake: 0, throttle: 1,
  gear: 4, steer_deg: 5, pct: t / 62.8, gap_m: gap, gap_clamped: false, beside, rival_pct: t / 62.8 + gap / 1885, rival_lat: beside ? [2.25, 4.6] : [-1, 1] });
const pass = { brake_m: -40, min_kph: 92, exit_kph: 140, full_throttle_m: 20, end_kph: 210 };
const corner = (id, extra) => ({ corner: id, label: `T${id.slice(1)}`, lap: 6, t_entry: 2, t_exit: 4, t_end: 6, turns: 'L', we_were: 'inside',
  gap_entry_m: 6, gap_next_m: 3, actual: pass, usual: null, best: null, noise_s: .05, ...extra });
const report = {
  status: 'READY', laps_used: 8, enough_laps: true,
  calibration: { edges: 12, median_m: 4.9, p10_p90_m: [4.6, 5.2], bias_m: .26 },
  assumptions: { car_length_m: 4.64, car_width_m: 2.05, side_max_m: 4.6, lateral_rate_mps: 2.5, entry_match_kph: 5 },
  geometry: { ref: ring, normal, band_left: ring, band_right: ring, grid: 500 },
  scenes: [
    { id: 'B1', rival: 7, lap: 6, t0: 0, t1: 8, outcome: 'passed', beside_s: 1.5,
      frames: [frame(0, 8), frame(2, 4, 'L'), frame(4, 0, 'L'), frame(6, -3), frame(8, -6)],
      corners: [corner('C1', { usual: { gain_s: .32, gap_next_pred_m: -6, verdict: 'ahead', laps: 5, numbers: { ...pass, brake_m: -46, min_kph: 99 } },
        best: { gain_s: .45, gap_next_pred_m: -9, verdict: 'ahead', lap: 3, solo: true, candidates: 6, chance_s: .07, numbers: { ...pass, min_kph: 101 } } }),
      corner('C2', { why_not: 'RIVAL_AHEAD' })] },
    { id: 'B2', rival: 9, lap: 2, t0: 20, t1: 30, outcome: 'held', beside_s: 0, frames: [frame(20, -5), frame(25, -6), frame(30, -7)], corners: [] },
  ],
  summary: { battles: 2, side_by_side: 1, passed: 1, lost: 0, top: { scene: 'B1', label: 'T1', lap: 6, gain_s: .45 } },
};

beforeEach(() => {
  forgetBattle();
  global.ResizeObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { this.callback([{ target, contentRect: { width: 800, height: 400 } }]); }
    disconnect() {}
  };
});
afterEach(() => jest.clearAllMocks());

test('shows the battles, the advice for the slow corner and the corner it cannot improve', async () => {
  requestJson.mockResolvedValue(report);
  const onOpenCorner = jest.fn();
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={onOpenCorner} />);
  expect(await screen.findByText('배틀 2번 · 나란히 1 · 추월 1 · 추월당함 0')).toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledWith('/api/workspace/battle', expect.objectContaining({ body: JSON.stringify({ filename: 'race.ibt' }) }));
  expect(screen.getByRole('button', { name: /6랩 · 상대 #7/ })).toHaveAttribute('aria-pressed', 'true');
  const card = screen.getByRole('article', { name: 'T1 6랩 제안' });
  expect(card).toHaveTextContent('다음 코너 입구에서 내가 앞');
  expect(card).toHaveTextContent('입구 46m 전');
  expect(card).toHaveTextContent('6m 일찍');
  expect(card).toHaveTextContent('0.32초 빨리');
  expect(card).toHaveTextContent('평소 혼자 달린 5랩의 중앙값');
  fireEvent.click(screen.getByRole('radio', { name: '이 레이스 최고' }));
  expect(card).toHaveTextContent('0.45초 빨리');
  expect(card).toHaveTextContent('3랩 혼자 주행 · 비슷한 진입 6번 중 가장 빠름');
  fireEvent.click(screen.getByRole('button', { name: /코너 비교에서 보기/ }));
  expect(onOpenCorner).toHaveBeenCalledWith(expect.objectContaining({ corner: 'C1', lap: 6 }), 3);
  fireEvent.click(screen.getByText('제안이 없는 코너 1곳'));
  expect(screen.getByText(/앞차보다 빠른 속도가 필요해 제외/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /T1 · 6랩/ }));
  expect(screen.getByText(/점선: T1만 이렇게 탔을 때/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /2랩 · 상대 #9/ }));
  expect(screen.getByText(/평소 혼자 달릴 때만큼 달렸습니다/)).toBeInTheDocument();
});

test('compares next corner entry speed for the usual and best passes', async () => {
  const speedCorner = { ...report.scenes[0].corners[0],
    usual: { ...report.scenes[0].corners[0].usual, numbers: { ...pass, end_kph: 214 } },
    best: { ...report.scenes[0].corners[0].best, numbers: { ...pass, end_kph: 208 } } };
  requestJson.mockResolvedValue({ ...report, scenes: [{ ...report.scenes[0], corners: [speedCorner] }] });
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  const card = await screen.findByRole('article', { name: 'T1 6랩 제안' });
  const speedCells = () => within(within(card).getByRole('row', { name: /다음 코너 입구 속도/ })).getAllByRole('cell');
  expect(speedCells()[0]).toHaveTextContent('210 km/h');
  expect(speedCells()[1]).toHaveTextContent('214 km/h · +4');
  fireEvent.click(within(card).getByRole('radio', { name: '이 레이스 최고' }));
  expect(speedCells()[0]).toHaveTextContent('210 km/h');
  expect(speedCells()[1]).toHaveTextContent('208 km/h · -2');
});

test('does not invent a next corner entry speed or difference when either is missing', async () => {
  const speedCorner = { ...report.scenes[0].corners[0], actual: { ...pass, end_kph: null },
    usual: { ...report.scenes[0].corners[0].usual, numbers: { ...pass, end_kph: null } } };
  requestJson.mockResolvedValue({ ...report, scenes: [{ ...report.scenes[0], corners: [speedCorner] }] });
  render(<BattleWorkspace filename="race.ibt" game="iracing" onOpenCorner={jest.fn()} />);
  const card = await screen.findByRole('article', { name: 'T1 6랩 제안' });
  const speedCells = () => within(within(card).getByRole('row', { name: /다음 코너 입구 속도/ })).getAllByRole('cell');
  expect(speedCells().map(cell => cell.textContent)).toEqual(['—', '—']);
  fireEvent.click(within(card).getByRole('radio', { name: '이 레이스 최고' }));
  expect(speedCells().map(cell => cell.textContent)).toEqual(['—', '210 km/h']);
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
  fireEvent.change(time, { target: { value: '4' } });
  expect(time).toHaveValue('4');
  expect(screen.getByRole('button', { name: '재생', exact: true })).toHaveAttribute('aria-pressed', 'false');
  const graph = screen.getByRole('img', { name: '앞뒤 간격과 페달' });
  jest.spyOn(graph, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 800 });
  fireEvent.click(graph, { clientX: 416 });
  expect(time).toHaveValue('2');
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
  expect(await screen.findByText(/깨끗한 랩이 3개뿐/)).toBeInTheDocument();
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
