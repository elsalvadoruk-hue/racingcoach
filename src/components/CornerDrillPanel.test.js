import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import CornerDrillPanel, { DrillGuide } from './CornerDrillPanel';
import { playBeep } from '../drillBeeps';

jest.mock('../drillBeeps', () => ({ playBeep: jest.fn() }));

const task = { id: 't1', corners: [{ label: '5번 코너' }] };
const plan = { task_id: 't1', label: '5번 코너', track_length_m: 4000, start_pct: 0.2, end_pct: 0.3, brake_pct: 0.25, section_m: 400,
  reference_source: 'GARAGE61', reference_label: 'Garage61 조건 가중 종합 기준 · 5랩' };
const running = (extra = {}) => ({ active: true, done: false, saved: true, plan, attempts: [], baseline_s: 10.0, recent_ok: 2, recent_count: 3,
  needed: { recent: 5, ok: 4 }, target: { label: '풀스로틀 지점', unit: 'm', sign: -1, goal: 3 }, ...extra });

afterEach(() => { delete global.fetch; });

test('starts the drill for the task corner and shows the plan', async () => {
  let state = { active: false };                                  // the server remembers the drill once started
  global.fetch = jest.fn((url, options) => {
    if (options?.method === 'POST') state = running({ saved: false, recent_count: 0 });
    return Promise.resolve({ ok: true, json: async () => state });
  });
  render(<CornerDrillPanel apiBase="http://api" filename="a.ibt" task={task} />);
  fireEvent.click(screen.getByText('구간 연습 시작 · 5번 코너'));
  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('http://api/api/practice/t1/drill', expect.objectContaining({ method: 'POST' })));
  expect(JSON.parse(global.fetch.mock.calls.find((call) => call[1]?.method === 'POST')[1].body)).toEqual({ filename: 'a.ibt', corner_label: '5번 코너' });
  expect(await screen.findByText(/아직 시작점이 저장되지 않았습니다/)).toBeInTheDocument();
  expect(screen.getByText(/랩 800 m 지점 \(제동 지점 약 200 m 전\)/)).toBeInTheDocument();
  expect(screen.getByTestId('drill-reference')).toHaveTextContent('Garage61 조건 가중 종합 기준 · 5랩의 제동·풀스로틀 지점 (빠른 드라이버 기준)');
});

test('shows attempts against the first three and the progress', async () => {
  const attempts = [
    { n: 1, time_s: 10.4, origin_m: null, brake_start_m: 190, min_speed_kph: 90, full_throttle_m: 330, input_change: -2, input_ok: false },
    { n: 2, time_s: 9.9, origin_m: 800, brake_start_m: 200, min_speed_kph: 92, full_throttle_m: 325, input_change: -6, input_ok: true },
  ];
  global.fetch = jest.fn(() => Promise.resolve({ ok: true, json: async () => running({ attempts }) }));
  render(<CornerDrillPanel apiBase="" filename="a.ibt" task={task} />);
  expect(await screen.findByTestId('drill-progress')).toHaveTextContent('같은 저장 지점 최근 3번 중 목표 달성 2번 (5번 중 4번이면 완료)');
  expect(screen.getByText('-0.100')).toBeInTheDocument();
  expect(screen.getByText('-6.0 m ✓')).toBeInTheDocument();
  expect(screen.getByText('1 (진입)')).toBeInTheDocument();
});

test('a finished drill says so and can be ended', async () => {
  global.fetch = jest.fn((url, options) => Promise.resolve({ ok: true, json: async () => (options?.method === 'DELETE' ? { active: false } : running({ done: true })) }));
  render(<CornerDrillPanel apiBase="" filename="a.ibt" task={task} />);
  expect(await screen.findByText('연습 완료')).toBeInTheDocument();
  fireEvent.click(screen.getByText('구간 연습 끝내기'));
  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('/api/drill', expect.objectContaining({ method: 'DELETE' })));
});

test('the guide explains the keys and lets each sound be heard', () => {
  render(<DrillGuide />);
  expect(screen.getByTestId('drill-guide')).toHaveTextContent('Advanced Analysis');
  expect(screen.getByTestId('drill-guide')).toHaveTextContent('혼자 하는 오프라인 연습');
  fireEvent.click(screen.getByText('“삐, 삐, 삑”'));
  expect(playBeep).toHaveBeenCalledWith('brake_point');
});

test('warm-up laps first, with the fuel to put in, and they can be skipped', async () => {
  let state = running({ phase: 'WARMUP', warm_laps: 1, warmup_laps: 2, saved: false,
    plan: { ...plan, fuel: { lap: 7, lap_start_l: 41.3, per_lap_l: 2.5, suggest_l: 47 } } });
  global.fetch = jest.fn((url, options) => {
    if (options?.method === 'POST' && url.endsWith('/skip-warmup')) state = { ...state, phase: 'READY' };
    return Promise.resolve({ ok: true, json: async () => state });
  });
  render(<CornerDrillPanel apiBase="" filename="a.ibt" task={task} />);
  expect(await screen.findByTestId('drill-warmup')).toHaveTextContent('타이어 워밍업 1/2바퀴');
  expect(screen.getByTestId('drill-fuel')).toHaveTextContent('47 L');
  expect(screen.getByTestId('drill-fuel')).toHaveTextContent('과제 랩(7랩) 시작 41.3 L + 워밍업 2바퀴 × 2.5 L');
  expect(screen.getByTestId('drill-progress')).toHaveTextContent('워밍업 중에는 저장하지 않습니다.');
  fireEvent.click(screen.getByText('워밍업 건너뛰기 (이미 따뜻함)'));
  await waitFor(() => expect(screen.queryByTestId('drill-warmup')).toBeNull());
});
