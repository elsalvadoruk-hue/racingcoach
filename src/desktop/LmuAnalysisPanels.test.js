import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import LmuAnalysisPanels, { LmuConsistencyPanel, LmuLapSelection } from './LmuAnalysisPanels';
import LmuSetupPanel from './LmuSetupPanel';
import LmuPracticePanel from './LmuPracticePanel';
import { requestJson } from '../analysis/analysisClient';
import { activateSession, selectGame } from './sessionData';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
const FILE = '0123456789abcdef01234567', OTHER = 'abcdef0123456789abcdef01', THIRD = '111111111111111111111111';
const setupItem = (key, stringValue) => ({ key, label: key, status: 'AVAILABLE', stringValue });
const files = [{ name: FILE, display_name: '새 주행.duckdb', analyzable: true }, { name: OTHER, display_name: '이전 주행.duckdb', analyzable: true }, { name: THIRD, display_name: '추가 주행.duckdb', analyzable: true }];
const baseline = { lap_count: 3, sources: [{ file_id: FILE, lap_number: 1 }, { file_id: FILE, lap_number: 2 }, { file_id: OTHER, lap_number: 3 }], corners: [], focus: [{ id: 'C1', label: '첫 구간', observation: '제동 위치 범위 15m', next_check: '다음 주행 확인' }] };
const task = { task_id: 'task-1', created_at: '2026-10-20T10:00:00Z', baseline };
let override;

beforeEach(() => {
  jest.clearAllMocks(); override = () => undefined;
  selectGame('lmu');
  activateSession({ provider: 'LMU', filename: FILE, file_name: '새 주행.duckdb', defaultTarget: 2, defaultReference: 1,
    session: { track_name: 'Monza', car_name: 'GT3', track_length_m: 5800 }, laps: [], initialComparison: {} });
  requestJson.mockImplementation((path, options = {}) => {
    const custom = override(path, options);
    if (custom !== undefined) return custom;
    if (path === '/api/lmu/workspace/files') return Promise.resolve({ files });
    if (path === '/api/lmu/workspace/file-laps') {
      const filename = JSON.parse(options.body).filename;
      return Promise.resolve({ laps: (filename === FILE ? [1, 2] : filename === THIRD ? [6] : [3, 4, 5]).map(n => ({ lap_number: n, lap_time_s: 117 + n, measurement_eligible: true, status: 'VALIDITY_UNKNOWN' })) });
    }
    if (path.startsWith('/api/lmu/telemetry/lap-analysis')) return Promise.resolve({ analysis: {
      lap_number: 2, braking: { zones: [{ status: 'COMPLETE', start_distance_m: 120, end_distance_m: 250, duration_s: 2.4, shared_measurement: { metrics: { brake_entry_speed_kph: 240, brake_end_speed_kph: 90, avg_decel_g: 1.2 } } }] }, full_throttle: { zones: [] }, channels: { speed_mps: { status: 'AVAILABLE', source_name: 'GPS Speed' } },
    } });
    if (path.startsWith('/api/lmu/telemetry/vehicle-channels')) return Promise.resolve({ vehicle: { status: 'PARTIAL', channel: { unit: 'kPa', components: [{ id: 'value1', label: 'value1', mean: 180, minimum: 175, maximum: 185, valid_samples: 99, sample_count: 100 }] }, catalog: [{ name: 'TyresPressure', label: '타이어 압력', status: 'AVAILABLE' }, { name: 'Fuel Level', label: '연료량', status: 'AVAILABLE' }] } });
    if (path.startsWith('/api/lmu/telemetry/vehicle-comparison')) return Promise.resolve({ comparison: { channel: { components: [] } } });
    if (path.startsWith('/api/lmu/telemetry/setup?')) return Promise.resolve({ setup: { counts: { AVAILABLE: 1 }, items: [setupItem('브레이크 밸런스', '54%')] } });
    if (path === '/api/lmu/practice/tasks') return Promise.resolve(options.method === 'POST' ? { task } : { tasks: [] });
    if (path === '/api/lmu/practice/tasks/task-1') return Promise.resolve({ task, comparisons: [] });
    return Promise.reject(new Error(`Unexpected mock endpoint ${path}`));
  });
});

test('summary shows real braking measurements and distinguishes detection thresholds from the five percent distribution', async () => {
  render(<LmuAnalysisPanels />);
  expect(await screen.findByText('240.0')).toBeInTheDocument();
  expect(screen.getByText('1.20')).toBeInTheDocument();
  expect(screen.getByText(/양쪽 5% 기준과 별개/)).toBeInTheDocument();
  expect(screen.getByText('GPS Speed')).toBeInTheDocument();
  expect(requestJson.mock.calls.every(([path]) => path.startsWith('/api/lmu/'))).toBe(true);
});

test('lap selector combines files and removes a selected lap without duplicating it', async () => {
  function Harness() { const [selected, setSelected] = React.useState([]); return <LmuLapSelection selections={selected} onChange={setSelected} />; }
  render(<Harness />);
  fireEvent.click(await screen.findByRole('button', { name: '랩 1 · 1:58.000' }));
  fireEvent.change(screen.getByLabelText('측정 랩을 추가할 주행'), { target: { value: OTHER } });
  fireEvent.click(await screen.findByRole('button', { name: '랩 3 · 2:00.000' }));
  expect(screen.getByText(/선택 2개/)).toBeInTheDocument();
  const table = screen.getByRole('table');
  expect(within(table).getByText('새 주행.duckdb')).toBeInTheDocument();
  expect(within(table).getByText('이전 주행.duckdb')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('랩 1 선택 해제'));
  expect(screen.getByText(/선택 1개/)).toBeInTheDocument();
});

test('LMU lap and setup selectors exclude confirmed empty files while keeping measured unknown-validity records', async () => {
  const empty = { name: 'empty', display_name: '빈 기록.duckdb', analyzable: true, measurement_laps: 0, valid_laps: null };
  override = path => path === '/api/lmu/workspace/files' ? Promise.resolve({ files: [empty,
    { ...files[0], measurement_laps: 2, valid_laps: null },
    { ...files[1], measurement_laps: 3, valid_laps: null },
  ] }) : undefined;
  const view = render(<LmuLapSelection selections={[]} onChange={() => {}} />);
  const laps = screen.getByLabelText('측정 랩을 추가할 주행');
  await waitFor(() => expect(within(laps).getByRole('option', { name: '이전 주행.duckdb' })).toBeInTheDocument());
  expect(within(laps).queryByRole('option', { name: '빈 기록.duckdb' })).not.toBeInTheDocument();
  expect(await screen.findByRole('button', { name: '랩 1 · 1:58.000' })).toBeEnabled();
  view.unmount();
  render(<LmuSetupPanel />);
  const setup = screen.getByLabelText('기준 A 주행');
  await waitFor(() => expect(within(setup).getByRole('option', { name: '이전 주행.duckdb' })).toBeInTheDocument());
  expect(within(setup).queryByRole('option', { name: '빈 기록.duckdb' })).not.toBeInTheDocument();
});

test('a currently selected LMU recording that resolves to zero measurements cannot add new observations', async () => {
  override = path => path === '/api/lmu/workspace/files' ? Promise.resolve({ files: [
    { ...files[0], measurement_laps: 0, valid_laps: null }, files[1],
  ] }) : undefined;
  const onChange = jest.fn();
  render(<LmuLapSelection selections={[{ file_id: FILE, lap_number: 1 }]} onChange={onChange} />);
  const select = screen.getByLabelText('측정 랩을 추가할 주행');
  await waitFor(() => expect(within(select).getByRole('option', { name: /새 주행/ })).toBeDisabled());
  expect(select).toHaveValue(FILE);
  expect(screen.queryByRole('button', { name: '랩 2 · 1:59.000' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '랩 1 선택 해제' })).toBeEnabled();
  expect(onChange).not.toHaveBeenCalled();
});

test('consistency requires three explicit observations and posts source identities from both files', async () => {
  override = path => path === '/api/lmu/telemetry/corner-consistency' ? Promise.resolve({ consistency: { corners: [{ id: 'C1', label: '첫 구간', sample_count: 3, spread_s: .25, input_consistency: { brake_start_m: { spread_m: 12 } } }] } }) : undefined;
  render(<LmuConsistencyPanel />);
  expect(screen.getByRole('button', { name: '일관성 분석' })).toBeDisabled();
  fireEvent.click(await screen.findByRole('button', { name: '랩 1 · 1:58.000' }));
  fireEvent.click(screen.getByRole('button', { name: '랩 2 · 1:59.000' }));
  fireEvent.change(screen.getByLabelText('측정 랩을 추가할 주행'), { target: { value: OTHER } });
  fireEvent.click(await screen.findByRole('button', { name: '랩 3 · 2:00.000' }));
  fireEvent.click(screen.getByRole('button', { name: '일관성 분석' }));
  expect(await screen.findByText('0.250')).toBeInTheDocument();
  const body = JSON.parse(requestJson.mock.calls.find(([path]) => path === '/api/lmu/telemetry/corner-consistency')[1].body);
  expect(body.selections).toEqual([{ file_id: FILE, lap_number: 1 }, { file_id: FILE, lap_number: 2 }, { file_id: OTHER, lap_number: 3 }]);
});

test('LMU consistency reuses iRacing box distributions and keeps same lap numbers from different files distinct', async () => {
  const onCorner = jest.fn();
  override = (path, options) => {
    if (path === '/api/lmu/workspace/file-laps' && JSON.parse(options.body).filename === OTHER) return Promise.resolve({ laps: [{ lap_number: 2, lap_time_s: 119, measurement_eligible: true }] });
    if (path === '/api/lmu/telemetry/corner-consistency') return Promise.resolve({ consistency: { corners: [
      { id: 'C1', label: '좁은 편차', sample_count: 3, passes: [10, 10.1, 10.2].map((duration_s, selection_index) => ({ duration_s, selection_index, lap_number: selection_index ? 2 : 1 })) },
      { id: 'C2', label: '넓은 편차', sample_count: 3, passes: [11, 12, 13].map((duration_s, selection_index) => ({ duration_s, selection_index, lap_number: selection_index ? 2 : 1 })) },
    ] } });
    return undefined;
  };
  render(<LmuConsistencyPanel onCorner={onCorner} />);
  fireEvent.click(await screen.findByRole('button', { name: '랩 1 · 1:58.000' }));
  fireEvent.click(screen.getByRole('button', { name: '랩 2 · 1:59.000' }));
  fireEvent.change(screen.getByLabelText('측정 랩을 추가할 주행'), { target: { value: OTHER } });
  fireEvent.click(await screen.findByRole('button', { name: '랩 2 · 1:59.000' }));
  fireEvent.click(screen.getByRole('button', { name: '일관성 분석' }));
  await screen.findByTestId('corner-consistency');
  expect(screen.getByTestId('corner-consistency')).toHaveTextContent('2 (현재 내 파일)');
  const labels = () => screen.getAllByTestId(/consistency-C/).map(node => node.textContent);
  expect(labels()).toEqual(['넓은 편차', '좁은 편차']);
  fireEvent.click(screen.getByRole('button', { name: '트랙 순서' }));
  expect(labels()).toEqual(['좁은 편차', '넓은 편차']);
  // The target is the current file's 2nd selection (12 s), not the other file's
  // equal-numbered 3rd selection (13 s). The median of best three is 12 s.
  expect(screen.getByTestId('corner-consistency')).toHaveTextContent('0.000 s');
  fireEvent.click(screen.getByRole('button', { name: '좁은 편차' }));
  expect(onCorner).toHaveBeenCalledWith('C1');
});

test('consistency never highlights a different observation when the current target is absent', async () => {
  const onCorner = jest.fn();
  override = path => path === '/api/lmu/telemetry/corner-consistency' ? Promise.resolve({ consistency: { corners: [
    { id: 'C1', label: '다른 기준의 구간', sample_count: 3, passes: [10, 11, 12].map((duration_s, selection_index) => ({ duration_s, selection_index })) },
  ] } }) : undefined;
  render(<LmuConsistencyPanel onCorner={onCorner} />);
  await screen.findByRole('button', { name: '랩 1 · 1:58.000' });
  fireEvent.change(screen.getByLabelText('측정 랩을 추가할 주행'), { target: { value: OTHER } });
  fireEvent.click(await screen.findByRole('button', { name: '랩 3 · 2:00.000' }));
  fireEvent.click(screen.getByRole('button', { name: '랩 4 · 2:01.000' }));
  fireEvent.change(screen.getByLabelText('측정 랩을 추가할 주행'), { target: { value: FILE } });
  fireEvent.click(await screen.findByRole('button', { name: '랩 1 · 1:58.000' }));
  fireEvent.click(screen.getByRole('button', { name: '일관성 분석' }));
  const distribution = await screen.findByTestId('corner-consistency');
  expect(distribution).toHaveTextContent('대상 랩 미포함');
  expect(within(distribution).getByText('--')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '다른 기준의 구간' })).not.toBeInTheDocument();
  expect(onCorner).not.toHaveBeenCalled();
});

test('vehicle comparison uses the reference file and never labels unverified wheel components', async () => {
  const { data } = require('./sessionData'); data.reference_filename = OTHER; data.defaultReference = 2;
  render(<LmuAnalysisPanels mode="expert" />);
  expect(await screen.findByText('180.00')).toBeInTheDocument();
  expect(screen.getByText('value1')).toBeInTheDocument();
  const request = requestJson.mock.calls.find(([path]) => path.includes('vehicle-comparison'))[0];
  expect(request).toContain(`reference_file_id=${OTHER}`);
  expect(screen.queryByText('앞 왼쪽')).not.toBeInTheDocument();
});

test('setup compares recorded changes and preserves unknown values', async () => {
  override = path => path.startsWith('/api/lmu/telemetry/setup-comparison') ? Promise.resolve({ comparison: { counts: { CHANGED: 1, UNCHANGED: 0, UNKNOWN: 1 }, rows: [
    { key: 'balance', label: '브레이크 밸런스 비교', state: 'CHANGED', reference: setupItem('balance', '53%'), target: setupItem('balance', '54%') },
    { key: 'fuel', label: '연료', state: 'UNKNOWN', reference: null, target: null },
  ] } }) : undefined;
  render(<LmuSetupPanel />);
  await screen.findByText('54%');
  fireEvent.change(screen.getByLabelText('기준 A 주행'), { target: { value: OTHER } });
  expect(await screen.findByText('53%')).toBeInTheDocument();
  expect(screen.getByText(/셋업 효과는 미평가/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('표시할 항목'), { target: { value: 'CHANGED' } });
  expect(screen.queryByText('연료')).not.toBeInTheDocument();
  expect(screen.getByText('브레이크 밸런스 비교')).toBeInTheDocument();
});

test('practice saves a three-lap baseline, clears selection, then compares three new laps and displays history', async () => {
  override = path => path === '/api/lmu/practice/tasks/task-1/compare' ? Promise.resolve({ comparison: { comparison_id: 'comparison-1', lap_count: 3, corners: [{ id: 'C1', baseline_median_duration_s: 12, current_median_duration_s: 11.8, spread_delta_m: { brake_start_m: -2, throttle_first_m: -3, full_throttle_m: -4 } }] } }) : undefined;
  render(<LmuPracticePanel />);
  fireEvent.click(await screen.findByRole('button', { name: '랩 1 · 1:58.000' }));
  fireEvent.click(screen.getByRole('button', { name: '랩 2 · 1:59.000' }));
  fireEvent.change(screen.getByLabelText('측정 랩을 추가할 주행'), { target: { value: OTHER } });
  fireEvent.click(await screen.findByRole('button', { name: '랩 3 · 2:00.000' }));
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩으로 연습 기준 저장' }));
  expect(await screen.findByText('현재 과제의 기준 관측')).toBeInTheDocument();
  expect(screen.getByText(/선택 0개/)).toBeInTheDocument();
  for (const name of ['랩 3 · 2:00.000', '랩 4 · 2:01.000', '랩 5 · 2:02.000']) fireEvent.click(screen.getByRole('button', { name }));
  expect(screen.getByRole('button', { name: '선택한 새 랩으로 과제 비교' })).toBeDisabled();
  expect(screen.getByText(/기준에 사용한 랩이 포함/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '랩 3 · 2:00.000' }));
  fireEvent.change(screen.getByLabelText('측정 랩을 추가할 주행'), { target: { value: THIRD } });
  fireEvent.click(await screen.findByRole('button', { name: '랩 6 · 2:03.000' }));
  fireEvent.click(screen.getByRole('button', { name: '선택한 새 랩으로 과제 비교' }));
  expect(await screen.findByText('주행 후 확인 1')).toBeInTheDocument();
  expect(screen.getByText('-2.0')).toBeInTheDocument();
  expect(screen.getByText('-3.0')).toBeInTheDocument();
  expect(screen.getByText('-4.0')).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: '스로틀 20% 관측 편차 변화 (m)' })).toBeInTheDocument();
  expect(screen.queryByRole('columnheader', { name: /브레이크 해제/ })).not.toBeInTheDocument();
  expect(screen.getByText('11.800')).toBeInTheDocument();
});

test('unmount cancels the pending vehicle requests and ignores their late results', async () => {
  const pending = []; override = (path, options) => path.includes('/vehicle-') ? new Promise(resolve => pending.push({ resolve, signal: options.signal })) : undefined;
  const view = render(<LmuAnalysisPanels mode="expert" />);
  await waitFor(() => expect(pending).toHaveLength(2));
  view.unmount();
  expect(pending.every(item => item.signal.aborted)).toBe(true);
  await act(async () => { pending.forEach(item => item.resolve({ vehicle: { reason: 'late' } })); });
});

test('measurement API failure remains an explicit error without invented values', async () => {
  override = path => path.includes('lap-analysis') ? Promise.reject(new Error('기록 파일이 변경됐습니다.')) : undefined;
  render(<LmuAnalysisPanels />);
  expect(await screen.findByText('기록 파일이 변경됐습니다.')).toBeInTheDocument();
  expect(screen.queryByText('240.0')).not.toBeInTheDocument();
});
