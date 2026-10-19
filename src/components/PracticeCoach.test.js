import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PracticeCoach from './PracticeCoach';

const coach = {
  ranked_focuses: [
    { root_cause: 'DRIVER_VARIANCE', root_cause_label: '랩마다 다른 입력', affected_corners: ['3번 코너'], total_observed_loss_s: 0.4, action: '같은 지점에서 제동하세요.' },
    { root_cause: 'THROTTLE_RETURN_LATE', root_cause_label: '풀스로틀 복귀 지연', affected_corners: ['5번 코너', '9번 코너'], total_observed_loss_s: 0.31, estimated_recovery_s: [0.1, 0.2], action: '탈출에서 더 일찍 풀스로틀로 가세요.', success_criteria: ['풀스로틀 지점 3m 앞당기기'] },
  ],
};
const active = {
  id: 'task1', title: '풀스로틀 복귀 지연', action: '탈출에서 더 일찍 풀스로틀로 가세요.', source_file: 'a.ibt',
  corners: [{ label: '5번 코너' }],
};

afterEach(() => { delete global.fetch; });

test('suggests a concrete focus first and starts it', async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ practice: { active, check: { status: 'WAITING', summary: '이 주행에서 정한 과제입니다.', corners: [] }, history: { by_cause: [] } } }) });
  render(<PracticeCoach apiBase="http://api" filename="a.ibt" practice={{ active: null, check: null, history: { by_cause: [] } }} coach={coach} />);
  expect(screen.getByText(/오늘의 한 가지: 풀스로틀 복귀 지연/)).toBeInTheDocument();
  expect(screen.getByText(/① 5번 코너, ② 9번 코너에서 합쳐 0.31초/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('이 과제로 연습 시작'));
  await waitFor(() => expect(screen.getByText(/진행 중인 과제/)).toBeInTheDocument());
  const [url, options] = global.fetch.mock.calls[0];
  expect(url).toBe('http://api/api/practice/start');
  expect(JSON.parse(options.body)).toEqual({ filename: 'a.ibt', focus_index: 1 });
  expect(screen.getByText('다음 주행 대기')).toBeInTheDocument();
});

test('shows the check of the next run with corner rows', () => {
  const check = {
    status: 'ACHIEVED', summary: '입력을 바꿨고 그 코너들이 빨라졌습니다. 과제를 해냈습니다.',
    target: { label: '풀스로틀 지점', unit: 'm', goal: 3, sign: -1 },
    corners: [{ label: '5번 코너', matched: true, input_change: -6, input_done: true, time_before_s: 10.1, time_after_s: 9.9, gain_s: 0.2 }],
  };
  render(<PracticeCoach apiBase="" filename="b.ibt" practice={{ active, check, history: { by_cause: [] } }} coach={coach} />);
  expect(screen.getByText('해냈습니다')).toBeInTheDocument();
  expect(screen.getByTestId('practice-summary')).toHaveTextContent('과제를 해냈습니다');
  expect(screen.getByText(/풀스로틀 지점 -6.0 m/)).toBeInTheDocument();
  expect(screen.getByText(/0.200초 빨라짐/)).toBeInTheDocument();
});

test('closing a task returns to the suggestion and keeps the history', async () => {
  const history = { by_cause: [{ root_cause: 'THROTTLE_RETURN_LATE', title: '풀스로틀 복귀 지연', tried: 1, done: 1, gain_s: 0.2 }] };
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ task: {}, history }) });
  render(<PracticeCoach apiBase="" filename="b.ibt" practice={{ active, check: null, history: { by_cause: [] } }} coach={coach} />);
  fireEvent.click(screen.getByText('완료로 기록'));
  await waitFor(() => expect(screen.getByText(/오늘의 한 가지/)).toBeInTheDocument());
  expect(global.fetch.mock.calls[0][0]).toBe('/api/practice/task1/close');
  expect(screen.getByTestId('practice-history-THROTTLE_RETURN_LATE')).toHaveTextContent('찾은 시간 0.20초');
});

test('says so when there is nothing to work on', () => {
  render(<PracticeCoach apiBase="" filename="b.ibt" practice={null} coach={{ ranked_focuses: [] }} />);
  expect(screen.getByText(/고칠 만한 반복 손실을 찾지 못했습니다/)).toBeInTheDocument();
});

test('shows the server reason when a start is refused', async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: '분석 중인 파일과 다릅니다.' }) });
  render(<PracticeCoach apiBase="" filename="b.ibt" practice={null} coach={coach} />);
  fireEvent.click(screen.getByText('이 과제로 연습 시작'));
  expect(await screen.findByText('분석 중인 파일과 다릅니다.')).toBeInTheDocument();
});

test('shows where the focus corners are when the lap has GPS', () => {
  const path = Array.from({ length: 200 }, (_, i) => {
    const angle = (i / 200) * Math.PI * 2;
    return { lat: 40 + Math.sin(angle) * 0.004, lon: -80 + Math.cos(angle) * 0.005, distance_pct: i / 200, brake: i % 50 < 5 ? 0.6 : 0, throttle: i % 50 < 10 ? 0.4 : 1 };
  });
  const visualizations = { track_path: path, markers: [
    { label: '5번 코너', entry_pct: 0.2, apex_pct: 0.22, exit_pct: 0.25 },
    { label: '9번 코너', entry_pct: 0.6, apex_pct: 0.62, exit_pct: 0.65 },
  ] };
  render(<PracticeCoach apiBase="" filename="a.ibt" practice={null} coach={coach} visualizations={visualizations} trackLength={4000} />);
  expect(screen.getByTestId('focus-mini-map')).toBeInTheDocument();
  expect(screen.getByLabelText('5번 코너 확대')).toBeInTheDocument();
  fireEvent.click(screen.getByText('2'));
  expect(screen.getByLabelText('9번 코너 확대')).toBeInTheDocument();
});

test('draws no map without GPS', () => {
  const visualizations = { track_path: [{ lat: null, lon: null, distance_pct: 0.1 }], markers: [{ label: '5번 코너', entry_pct: 0.2, apex_pct: 0.22, exit_pct: 0.25 }] };
  render(<PracticeCoach apiBase="" filename="a.ibt" practice={null} coach={coach} visualizations={visualizations} trackLength={4000} />);
  expect(screen.queryByTestId('focus-mini-map')).toBeNull();
});

test('draws the picked focus corner as a graph from this run', async () => {
  const measured = [{ id: 'C5', label: '5번 코너', entry_pct: 0.2, apex_pct: 0.21, exit_pct: 0.22,
    target: { full_throttle_pct: 0.216 }, reference: { full_throttle_pct: 0.213 } }];
  const load = jest.fn((a, b) => Promise.resolve([{ distance_pct: a, target_speed_kph: 100, reference_speed_kph: 101 }, { distance_pct: b, target_speed_kph: 140, reference_speed_kph: 141 }]));
  render(<PracticeCoach apiBase="" filename="a.ibt" practice={null} coach={coach} corners={measured} loadWindow={load} trackLength={4000} />);
  expect(await screen.findByTestId('practice-graph')).toHaveTextContent('① 5번 코너: 이번 랩과 기준 랩 비교');
  expect(await screen.findByText(/늦게 열었습니다. ← 오늘의 과제/)).toBeInTheDocument();
});

test('a focus measured against Garage61 is drawn and labelled against the fast laps', async () => {
  const g61Coach = { ranked_focuses: [{ ...coach.ranked_focuses[1], source: 'GARAGE61_REFERENCE_CONSENSUS', affected_corners: ['5번 코너'] }] };
  const own = [{ id: 'C5', label: '5번 코너', entry_pct: 0.2, apex_pct: 0.21, exit_pct: 0.22, target: { full_throttle_pct: 0.216 }, reference: { full_throttle_pct: 0.215 } }];
  const fast = [{ ...own[0], reference: { full_throttle_pct: 0.212 } }];
  const local = jest.fn(() => Promise.resolve([]));
  const external = jest.fn((a, b) => Promise.resolve([{ distance_pct: a, target_speed_kph: 100, reference_speed_kph: 104 }, { distance_pct: b, target_speed_kph: 140, reference_speed_kph: 143 }]));
  render(<PracticeCoach apiBase="" filename="a.ibt" practice={null} coach={g61Coach} corners={own} loadWindow={local} trackLength={4000}
    referenceLabel="내 기준 랩 7" externalCorners={fast} externalLoadWindow={external} externalLabel="Garage61 조건 가중 종합 기준 · 5랩" />);
  expect(screen.getByTestId('practice-reference')).toHaveTextContent('손실 기준: Garage61 조건 가중 종합 기준 · 5랩');
  await waitFor(() => expect(external).toHaveBeenCalled());
  expect(local).not.toHaveBeenCalled();
  expect(await screen.findByText(/풀스로틀: 기준보다 16m 늦게 열었습니다/)).toBeInTheDocument();   // .216 against the fast laps' .212
});

const mine = { ...coach.ranked_focuses[1], source: 'PERSONAL_LAP_COMPARISON' };
const fastFocus = { ...coach.ranked_focuses[1], root_cause: 'BRAKE_RELEASE_LATE', root_cause_label: '브레이크 릴리즈 지연', source: 'GARAGE61_REFERENCE_CONSENSUS' };

test('the driver picks whether the focus is measured on own laps or the Garage61 fast laps', async () => {
  window.localStorage.clear();
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ practice: { active, check: null, history: { by_cause: [] } } }) });
  render(<PracticeCoach apiBase="" filename="a.ibt" practice={null} coach={coach} referenceLabel="내 기준 랩 7" externalLabel="Garage61 종합 기준"
    focusSets={{ PERSONAL: [mine], GARAGE61: [fastFocus] }} />);
  // Garage61 first when this analysis has it
  expect(screen.getByText(/오늘의 한 가지: 브레이크 릴리즈 지연/)).toBeInTheDocument();
  expect(screen.getByTestId('practice-reference')).toHaveTextContent('손실 기준: Garage61 종합 기준');
  fireEvent.click(screen.getByRole('button', { name: '내 주행' }));
  expect(screen.getByText(/오늘의 한 가지: 풀스로틀 복귀 지연/)).toBeInTheDocument();
  expect(screen.getByTestId('practice-reference')).toHaveTextContent('손실 기준: 내 기준 랩 7');
  expect(window.localStorage.getItem('iracing.practice.source')).toBe('PERSONAL');
  fireEvent.click(screen.getByText('이 과제로 연습 시작'));
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ filename: 'a.ibt', focus_index: 0, source: 'PERSONAL' });
});

test('picking Garage61 without it in the analysis offers to analyse again, or says what is missing', () => {
  window.localStorage.setItem('iracing.practice.source', 'GARAGE61');
  const again = jest.fn();
  const { rerender } = render(<PracticeCoach apiBase="" filename="a.ibt" practice={null} coach={coach}
    focusSets={{ PERSONAL: [mine], GARAGE61: [] }} garage61Available onUseGarage61={again} />);
  expect(screen.queryByText(/오늘의 한 가지/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('Garage61 기준으로 다시 분석'));
  expect(again).toHaveBeenCalled();
  rerender(<PracticeCoach apiBase="" filename="a.ibt" practice={null} coach={coach} focusSets={{ PERSONAL: [mine], GARAGE61: [] }} />);
  expect(screen.getByTestId('practice-garage61-missing')).toHaveTextContent('Garage61 기준 랩이 없습니다');
  window.localStorage.clear();
});
