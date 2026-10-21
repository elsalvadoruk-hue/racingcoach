import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App';
import { LAST_SESSION_KEY, LMU_SESSION_KEY, selectGame } from './desktop/sessionData';

const FILE_ID = '0123456789abcdef01234567';
const FILE_NAME = 'Monza_P_2026-10-20T08_59_41Z.duckdb';
const usage = overlap => ({ status: 'PARTIAL', method: 'NATIVE_TIME_THRESHOLD_V1', threshold_ratio: .05,
  input_stage: 'UNFILTERED', lap_duration_s: 100.02, observed_time_s: 100,
  unobserved_time_s: .02, coverage_ratio: 100 / 100.02,
  categories: [
    { key: 'THROTTLE_ONLY', duration_s: 60, observed_percent: 60 },
    { key: 'BRAKE_ONLY', duration_s: 20, observed_percent: 20 },
    { key: 'OVERLAP', duration_s: overlap, observed_percent: overlap },
    { key: 'BOTH_LOW', duration_s: 20 - overlap, observed_percent: 20 - overlap },
  ] });
const laps = [1, 2].map(number => ({ lap_number: number, lap_time_s: 100 + number,
  status: 'VALIDITY_UNKNOWN', valid: null, measurement_eligible: true, complete: true,
  reference_status: 'VALIDITY_UNVERIFIED', pedal_usage: usage(number === 1 ? 8 : 12) }));
const comparison = { provider: 'LMU', status: 'MEASUREMENT_ONLY', target_lap: 2, reference_lap: 1,
  targetTime: 102, referenceTime: 101, delta: 1, reference_status: 'VALIDITY_UNVERIFIED',
  conditions: 'NOT_COMPARED', trace: [], corners: [], track_path: [], reference_track_path: [],
  controlEvents: {}, warnings: [], pedal_usage: { target: laps[1].pedal_usage, reference: laps[0].pedal_usage } };
const session = { provider: 'LMU', kind: 'LIVE_LOCAL', filename: FILE_ID, file_name: FILE_NAME,
  defaultTarget: 2, defaultReference: 1, laps, corners: [], initialComparison: comparison,
  session: { provider: 'LMU', track_name: 'Monza', track_config: 'Grand Prix',
    car_name: 'GT3', track_length_m: 5793, session_type: 'Practice', conditions: 'NOT_COMPARED' },
  consistency: {}, external: [], analysisResult: { provider: 'LMU', laps, comparison: { target_lap: 2, reference_lap: 1 } } };

const paths = () => global.fetch.mock.calls.map(([url]) => new URL(url, 'http://localhost:24158').pathname);
// LMU analysis never reads iRacing recordings; live coaching is the shared service.
const iracingAnalysis = () => paths().filter(path => path.startsWith('/api/workspace/') || path === '/api/files' || path === '/api/analyze');
let previousMediaDevices;
let mediaDevices;
let eventSource;
let socket;
let previousResizeObserver;

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('coach.selected-game.v1', 'lmu');
  window.history.replaceState(null, '', '/');
  selectGame('lmu');
  previousResizeObserver = global.ResizeObserver;
  global.ResizeObserver = class { observe() {} disconnect() {} };
  Element.prototype.scrollIntoView = jest.fn();
  previousMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
  mediaDevices = { enumerateDevices: jest.fn(async () => []), getUserMedia: jest.fn(),
    addEventListener: jest.fn(), removeEventListener: jest.fn() };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: mediaDevices });
  eventSource = jest.fn(() => ({ close: jest.fn(), addEventListener: jest.fn() }));
  global.EventSource = eventSource;
  socket = jest.spyOn(window, 'WebSocket').mockImplementation(() => ({ close: jest.fn(), send: jest.fn() }));
  jest.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  jest.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  global.fetch = jest.fn(async (url, options = {}) => {
    const path = new URL(url, 'http://localhost:24158').pathname;
    let payload;
    if (path.startsWith('/api/lmu/workspace/')) {
      expect(options.method).toBe('POST');
      const route = path.split('/').pop();
      payload = route === 'files' ? { provider: 'LMU', files: [{ name: FILE_ID, file_id: FILE_ID,
        file_name: FILE_NAME, display_name: FILE_NAME, provider: 'LMU', analyzable: true,
        track_name: 'Monza', car_name: 'GT3', session_type: 'Practice', detected_laps: 2,
        measurement_laps: 2, complete_laps: 2, reference_status: 'VALIDITY_UNVERIFIED' }] }
        : route === 'session' ? session
        : route === 'references' ? { provider: 'LMU', files: [], status: 'UNSUPPORTED' }
        : route === 'comparison' ? { ...comparison, ...JSON.parse(options.body) }
        : { provider: 'LMU', laps: [] };
    } else if (path === '/api/lmu/live/bridge') {
      payload = { state: 'READY', message: 'LMU 실시간 연결 준비됨' };
    } else if (path === '/api/lmu/status') {
      payload = { dependency: 'READY', file_count: 1, telemetry_dirs: [], live: { state: 'STOPPED', collecting: false } };
    } else if (path === '/api/lmu/voice/reports') {
      payload = { available: false, reports: [], lease_s: 0 };
    } else if (path === '/api/lmu/settings') {
      payload = { settings: { telemetry_dirs: [] }, telemetry_folders: [], source: 'DEFAULT' };
    } else if (path === '/api/lmu/overlay') {
      payload = { source_game: 'lmu', supported: true, settings: {}, auto_show: false };
    } else if (path === '/api/lmu/history') {
      payload = { records: [], total: 0, tracks: [], cars: [], session_types: [] };
    } else if (path === '/api/lmu/practice/tasks') payload = { tasks: [] };
    else if (path === '/api/files') payload = { files: [] };
    else if (path === '/api/settings') payload = { version: '1.4.3', settings: { telemetry_dirs: [] }, google: {}, garage61: {}, audio: {}, telemetry_folders: [] };
    else if (path === '/api/status') payload = { telemetry: null, ai_enabled: false };
    else if (path.includes('/api/overlay')) payload = { enabled: false, available: false, settings: {}, rows: [], auto_show: false, voices: {} };
    else if (path.includes('/api/memory/sessions')) payload = { sessions: [] };
    else payload = { enabled: false, connected: false, counts: { sessions: 0 } };
    return { ok: true, json: async () => payload };
  });
});

afterEach(() => {
  cleanup();
  jest.restoreAllMocks();
  if (previousMediaDevices) Object.defineProperty(navigator, 'mediaDevices', previousMediaDevices);
  else delete navigator.mediaDevices;
  delete global.fetch;
  delete global.EventSource;
  if (previousResizeObserver) global.ResizeObserver = previousResizeObserver;
  else delete global.ResizeObserver;
  selectGame('iracing');
});

test('LMU analyses its own recordings and shares the iRacing live coach without mic access or unsolicited audio', async () => {
  render(<App />);
  expect(await screen.findByRole('heading', { name: 'LMU 주행 코치' })).toBeVisible();
  expect(document.documentElement.dataset.game).toBe('lmu');
  await waitFor(() => expect(paths()).toContain('/api/lmu/workspace/files'));
  expect(iracingAnalysis()).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: '설정', exact: true }));
  expect(await screen.findByText('LMU 실시간 연결')).toBeVisible();
  await screen.findByText('LMU 실시간 연결 준비됨');
  expect(screen.getByRole('switch', { name: '마우스 오버 설명' })).toBeVisible();
  await waitFor(() => expect(paths()).toContain('/api/update'));
  fireEvent.click(screen.getByRole('button', { name: '음성 코치', exact: true }));
  expect(await screen.findByRole('heading', { name: '음성 코치' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '기록', exact: true }));
  await screen.findByText(/저장된 LMU 연습 기록이 없습니다\./);
  expect(iracingAnalysis()).toEqual([]);
  expect(paths()).not.toContain('/api/lmu/live/journal/start');
  expect(mediaDevices.getUserMedia).not.toHaveBeenCalled();
  expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
});

test('LMU automatically opens the latest measurable drive and the common picker accepts its unknown laps', async () => {
  const previousSelection = JSON.stringify({ filename: 'preserved-iracing.ibt', target_lap: 9, reference_lap: 4 });
  localStorage.setItem(LAST_SESSION_KEY, previousSelection);
  render(<App />);
  await screen.findByText('오늘의 연습 과제');
  expect(screen.queryByTestId('lap-time-use')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: '분석할 주행을 선택하세요.' })).not.toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem(LMU_SESSION_KEY))).toMatchObject({ filename: FILE_ID, target_lap: 2, reference_lap: 1 });
  fireEvent.click(screen.getByRole('button', { name: '비교할 내 랩 변경', exact: true }));
  const dialog = await screen.findByRole('dialog', { name: '비교할 주행 선택' });
  const own = within(dialog).getByLabelText('내 주행');
  const reference = within(dialog).getByLabelText('비교 기준');
  await waitFor(() => expect(within(dialog).getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
  expect(within(dialog).getByLabelText('주행 파일 · LMU DuckDB')).toHaveValue(FILE_ID);
  expect(within(own).getAllByRole('option')).toHaveLength(2);
  for (const option of within(own).getAllByRole('option')) {
    expect(option).toBeEnabled();
    expect(option).toHaveTextContent('확인 필요');
    expect(option).not.toHaveTextContent('★ 베스트');
  }
  expect(within(dialog).getByRole('option', { name: 'Garage61 · 내려받은 기준' })).toBeDisabled();
  fireEvent.change(own, { target: { value: '2' } });
  fireEvent.change(reference, { target: { value: '1' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  const call = global.fetch.mock.calls.find(([url]) => url.endsWith('/api/lmu/workspace/comparison'));
  expect(JSON.parse(call[1].body)).toMatchObject({ filename: FILE_ID, target_lap: 2, reference_lap: 1,
    reference_mode: 'LOCAL', external_reference_files: [] });
  fireEvent.mouseDown(screen.getByRole('tab', { name: '랩 분석', exact: true }), { button: 0, ctrlKey: false });
  const table = await screen.findByTestId('lap-time-use');
  expect(within(table).getByText(/두 페달 모두 5% 이상/)).toBeVisible();
  expect(within(table).getByLabelText('동시입력 12.0%')).toBeVisible();
  expect(within(table).getByLabelText('동시입력 8.0%')).toBeVisible();
  expect(within(table).getByTestId('time-use-lap-1')).toBeVisible();
  expect(within(table).getByTestId('time-use-lap-2')).toBeVisible();
  expect(within(table).getAllByTitle('관측 100.000 s · 미관측 0.020 s')).toHaveLength(2);
  expect(iracingAnalysis()).toEqual([]);
  expect(JSON.parse(localStorage.getItem(LMU_SESSION_KEY))).toMatchObject({ filename: FILE_ID, target_lap: 2, reference_lap: 1 });
  expect(localStorage.getItem(LAST_SESSION_KEY)).toBe(previousSelection);
});

test('switching between LMU and iRacing preserves the existing iRacing session selection', async () => {
  const saved = JSON.stringify({ filename: 'existing-iracing.ibt', target_lap: 7, reference_lap: 3,
    external_reference_files: ['driver-reference.csv'], reference_mode: 'SINGLE' });
  localStorage.setItem(LAST_SESSION_KEY, saved);
  render(<App />);
  await screen.findByRole('heading', { name: 'LMU 주행 코치' });
  fireEvent.click(screen.getByRole('radio', { name: 'iRacing', exact: true }));
  await screen.findByRole('heading', { name: 'iRacing 주행 코치' });
  expect(document.documentElement.dataset.game).toBe('iracing');
  await waitFor(() => expect(paths()).toContain('/api/files'));
  expect(localStorage.getItem(LAST_SESSION_KEY)).toBe(saved);
  fireEvent.click(screen.getByRole('radio', { name: 'LMU', exact: true }));
  await screen.findByRole('heading', { name: 'LMU 주행 코치' });
  expect(localStorage.getItem(LAST_SESSION_KEY)).toBe(saved);
  expect(localStorage.getItem('coach.selected-game.v1')).toBe('lmu');
  expect(document.documentElement.dataset.game).toBe('lmu');
});

test('late iRacing startup responses cannot start memory or Garage61 loading after switching to LMU', async () => {
  localStorage.setItem('coach.selected-game.v1', 'iracing');
  const fetcher = global.fetch;
  let release;
  const delay = new Promise(resolve => { release = resolve; });
  global.fetch = jest.fn(async (...args) => {
    const path = new URL(args[0]).pathname;
    if (path === '/api/files' || path === '/api/status') await delay;
    return fetcher(...args);
  });
  render(<App />);
  fireEvent.click(screen.getByRole('radio', { name: 'LMU', exact: true }));
  await screen.findByRole('heading', { name: 'LMU 주행 코치' });
  await act(async () => { release(); });
  expect(paths().filter(path => path.startsWith('/api/memory/') || path.startsWith('/api/garage61/'))).toEqual([]);
});
