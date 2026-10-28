import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import SourcePicker from './SourcePicker';
import { activateSession, currentSessionResult, labRequest, selectGame } from './sessionData';
import { requestJson } from '../analysis/analysisClient';

jest.mock('./sessionData', () => {
  const actual = jest.requireActual('./sessionData');
  return Object.defineProperty({ ...actual, labRequest: jest.fn() }, 'data', { get: () => actual.data });
});
jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));

const lap = { lap_number: 2, lap_time_s: 90, status: 'VALID', complete: true };
const session = filename => ({ kind: 'LIVE_LOCAL', filename, defaultTarget: 2, defaultReference: 2,
  reference_selection: 'AUTO', session: {}, laps: [lap], corners: [], initialComparison: {} });
const health = (filename, needs = true) => ({ enabled: true, needs_collection: needs, context: { context_key: filename } });
const job = (filename, busy = true) => ({ job_id: filename, filename, context_key: filename,
  state: busy ? 'DOWNLOADING' : 'COMPLETE', busy, progress: busy ? .42 : 1, downloaded_count: busy ? 0 : 1 });
const refs = [{ name: 'new.csv', usable: true, compatible: true, driver_name: '검증 드라이버', lap_time_s: 89 }];
let onApply, downloaded;
beforeEach(() => {
  localStorage.clear(); selectGame('iracing'); activateSession(currentSessionResult(session('A.ibt')));
  onApply = jest.fn(); downloaded = false;
  labRequest.mockReset(); requestJson.mockReset();
  labRequest.mockImplementation(async (route, body) => {
    if (route === 'files') return { files: ['A.ibt','B.ibt','C.ibt'].map(name => ({ name })) };
    if (route === 'session') return session(body.filename);
    if (route === 'references') return { files: downloaded && body.filename === 'B.ibt' ? refs : [],
      recommended_selection: downloaded ? ['new.csv'] : [], personalReference: { filename: body.filename, lap: 2 } };
    if (route === 'comparison') return { reference_lap: 2 };
    throw new Error(route);
  });
  requestJson.mockImplementation(async (url, options) => {
    const filename = options?.body ? JSON.parse(options.body).filename : new URL(url, 'http://test.invalid').searchParams.get('filename');
    if (url.endsWith('/start')) return { health: health(filename), job: job(filename) };
    return { health: filename === 'A.ibt' ? { enabled: false } : health(filename), job: null };
  });
});
afterEach(() => { jest.useRealTimers(); selectGame('iracing'); });
const ready = () => waitFor(() => expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
const starts = () => requestJson.mock.calls.filter(([url]) => url.endsWith('/collector/start'));
async function open(game = 'iracing') {
  const view = render(<SourcePicker target="2" reference="2" external={[]} onApply={onApply} analysisWorkspace={{ game }} />);
  await ready();
  return view;
}
async function choose(filename) {
  fireEvent.change(screen.getByLabelText('주행 파일 · IBT'), { target: { value: filename } });
  await ready();
}

test('selecting a recording starts its download before applying a comparison', async () => {
  await open(); await choose('B.ibt');
  await waitFor(() => expect(starts()).toHaveLength(1));
  expect(JSON.parse(starts()[0][1].body)).toMatchObject({ filename: 'B.ibt', force: false, collector_mode: 'AUTO' });
  expect(onApply).not.toHaveBeenCalled();
  expect(screen.getByRole('progressbar', { name: 'Garage61 수집 예상 진행률' })).toHaveAttribute('aria-valuenow', '42');
  expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled();
});

test('completed collection refreshes the open picker and preserves the personal choice', async () => {
  jest.useFakeTimers();
  await open(); await choose('B.ibt');
  await waitFor(() => expect(starts()).toHaveLength(1));
  downloaded = true;
  requestJson.mockImplementation(async () => ({ health: health('B.ibt', false), job: job('B.ibt', false) }));
  await act(async () => { jest.advanceTimersByTime(5000); });
  await waitFor(() => expect(screen.getByRole('option', { name: 'Garage61 · 내려받은 기준' })).toBeEnabled());
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  fireEvent.change(screen.getByLabelText('기준 종류'), { target: { value: 'GARAGE61' } });
  expect(screen.getByRole('option', { name: /검증 드라이버/ })).toBeInTheDocument();
  expect(onApply).not.toHaveBeenCalled();
});

test('a late status for the previous file cannot start its download', async () => {
  let finish;
  const original = requestJson.getMockImplementation();
  requestJson.mockImplementation((url, options) => url.includes('B.ibt')
    ? new Promise(resolve => { finish = resolve; }) : original(url, options));
  await open(); await choose('B.ibt');
  await waitFor(() => expect(finish).toBeDefined());
  await choose('A.ibt');
  await act(async () => finish({ health: health('B.ibt'), job: null }));
  expect(starts()).toHaveLength(0);
  expect(screen.getByLabelText('주행 파일 · IBT')).toHaveValue('A.ibt');
});

test('a sufficient cache explains why another download is unnecessary', async () => {
  requestJson.mockResolvedValue({ health: health('A.ibt', false), job: null });
  await open();
  expect(await screen.findByText(/최신 Garage61 기준이 충분해/)).toBeInTheDocument();
  expect(starts()).toHaveLength(0);
});

test('a cooldown is visible instead of appearing to do nothing', async () => {
  jest.useFakeTimers();
  requestJson.mockImplementation(async url => url.endsWith('/start')
    ? { health: health('B.ibt'), job: { state: 'SKIPPED_COOLDOWN', busy: false, message: '동일 조건을 최근 15분 이내 수집하여 재수집을 생략했습니다.' } }
    : { health: url.includes('B.ibt') ? health('B.ibt') : { enabled: false }, job: null });
  await open(); await choose('B.ibt');
  expect(await screen.findByText(/최근 15분 이내/)).toBeInTheDocument();
  expect(screen.getByRole('status', { name: 'Garage61 수집 상태' })).toHaveTextContent('재수집 생략');
  expect(screen.getByText('SKIPPED_COOLDOWN', { exact: true })).toBeInTheDocument();
  expect(screen.queryByRole('progressbar', { name: 'Garage61 수집 예상 진행률' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled();
  expect(starts()).toHaveLength(1);
  const calls = requestJson.mock.calls.length;
  await act(async () => { jest.advanceTimersByTime(60000); });
  expect(requestJson).toHaveBeenCalledTimes(calls);
  fireEvent.click(screen.getByRole('button', { name: 'Garage61 다시 확인' }));
  await waitFor(() => expect(starts()).toHaveLength(2));
  expect(starts().every(([, options]) => JSON.parse(options.body).force === false)).toBe(true);
  expect(await screen.findByText('SKIPPED_COOLDOWN', { exact: true })).toBeInTheDocument();
});

test.each([
  ['COMPLETE', 'Garage61 수집 완료', 'Garage61 자료 수집을 마쳤습니다.'],
  ['PARTIAL', 'Garage61 일부 수집', 'Garage61 자료를 일부 받았습니다.'],
  ['CANCELLED', 'Garage61 수집 취소', 'Garage61 자료 수집이 취소됐습니다.'],
  ['SKIPPED_CACHE_SUFFICIENT', 'Garage61 저장된 자료 사용', '최신 Garage61 기준이 충분해'],
  ['FAILED', 'Garage61 수집 실패', '검증용 인증 오류'],
])('%s has a visible terminal result and leaves personal comparison available', async (phase, title, message) => {
  jest.useFakeTimers();
  requestJson.mockImplementation(async url => url.endsWith('/start')
    ? { health: health('B.ibt'), job: { state: phase, busy: false, message: '검증용 인증 오류' } }
    : { health: url.includes('B.ibt') ? health('B.ibt') : { enabled: false }, job: null });
  await open(); await choose('B.ibt');
  const result = await screen.findByRole(phase === 'FAILED' ? 'alert' : 'status', { name: 'Garage61 수집 상태' });
  expect(result).toHaveTextContent(title);
  expect(result).toHaveTextContent(message);
  expect(screen.queryByRole('progressbar', { name: 'Garage61 수집 예상 진행률' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled();
  const calls = requestJson.mock.calls.length;
  await act(async () => { jest.advanceTimersByTime(30000); });
  expect(requestJson).toHaveBeenCalledTimes(calls);
});

test('a cooldown without server wording still explains the skipped download', async () => {
  requestJson.mockImplementation(async url => url.endsWith('/start')
    ? { health: health('B.ibt'), job: { state: 'SKIPPED_COOLDOWN', busy: false } }
    : { health: url.includes('B.ibt') ? health('B.ibt') : { enabled: false }, job: null });
  await open(); await choose('B.ibt');
  expect(await screen.findByRole('status', { name: 'Garage61 수집 상태' })).toHaveTextContent('재수집 제한 시간 동안 다운로드를 생략');
});

test('LMU never checks or starts Garage61 collection', async () => {
  await open('lmu');
  expect(requestJson.mock.calls.filter(([url]) => url.includes('/garage61/collector'))).toHaveLength(0);
});

test('no token uses personal comparison even with cached recommendations and old external selection', async () => {
  const original = labRequest.getMockImplementation();
  labRequest.mockImplementation(async (route, body, signal) => route === 'references'
    ? { files: refs, recommended_selection: ['new.csv'], api_token_configured: false,
      personalReference: { filename: body.filename, lap: 2 } }
    : original(route, body, signal));
  requestJson.mockResolvedValue({ health: { ...health('A.ibt'), collection_policy: { api_token_configured: false } },
    job: { state: 'FAILED', busy: false, message: 'old authentication failure' } });
  render(<SourcePicker target="2" reference="2" external={['new.csv']} onApply={onApply} analysisWorkspace={{ game: 'iracing' }} />);
  await ready();
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  expect(screen.queryByRole('option', { name: 'Garage61 · 내려받은 기준' })).not.toBeInTheDocument();
  expect(screen.queryByRole('status', { name: 'Garage61 수집 상태' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Garage61 다시 확인' })).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.queryByText('Garage61 수집 실패')).not.toBeInTheDocument();
  expect(starts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(onApply).toHaveBeenCalled());
  const body = labRequest.mock.calls.find(([route]) => route === 'comparison')[1];
  expect(body).toMatchObject({ reference_mode: 'LOCAL', external_reference_files: [] });
});

test('registering a token can surface authentication errors and deleting it clears them on recheck', async () => {
  let configured = false;
  requestJson.mockImplementation(async url => ({
    health: { ...health('A.ibt'), collection_policy: { api_token_configured: configured } },
    job: url.endsWith('/start') ? { state: 'FAILED', busy: false, error_code: 'API_TOKEN_INVALID', message: '토큰 인증 실패 (HTTP 401)' } : null,
  }));
  const view = await open();
  expect(screen.queryByRole('button', { name: 'Garage61 다시 확인' })).not.toBeInTheDocument();
  expect(starts()).toHaveLength(0);
  configured = true;
  view.unmount();
  await open();
  expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 401');
  expect(starts()).toHaveLength(1);
  configured = false;
  fireEvent.click(screen.getByRole('button', { name: 'Garage61 다시 확인' }));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(screen.queryByRole('status', { name: 'Garage61 수집 상태' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Garage61 다시 확인' })).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(starts()).toHaveLength(1);
});

test('a busy collector waits for the other context before downloading the previewed recording', async () => {
  jest.useFakeTimers();
  let otherBusy = true;
  requestJson.mockImplementation(async (url, options) => {
    if (url.endsWith('/start')) return { health: health('B.ibt'), job: job('B.ibt') };
    return { health: url.includes('B.ibt') ? health('B.ibt') : { enabled: false }, job: job('A.ibt', otherBusy) };
  });
  await open(); await choose('B.ibt');
  expect(await screen.findByText(/다른 주행의 자료를 수집 중/)).toBeInTheDocument();
  expect(starts()).toHaveLength(0);
  expect(screen.getByRole('progressbar', { name: 'Garage61 수집 예상 진행률' })).not.toHaveAttribute('aria-valuenow');
  otherBusy = false;
  await act(async () => { jest.advanceTimersByTime(5000); });
  await waitFor(() => expect(starts()).toHaveLength(1));
  expect(JSON.parse(starts()[0][1].body).filename).toBe('B.ibt');
});

test('a request failure shows the reason and a status retry can recover', async () => {
  requestJson.mockRejectedValue(new Error('검증용 연결 시간 초과'));
  await open();
  expect(await screen.findByRole('alert')).toHaveTextContent('검증용 연결 시간 초과');
  requestJson.mockResolvedValue({ health: health('A.ibt', false), job: null });
  fireEvent.click(screen.getByRole('button', { name: 'Garage61 다시 확인' }));
  expect(await screen.findByText(/최신 Garage61 기준이 충분해/)).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(starts()).toHaveLength(0);
});

test('closing the picker aborts its pending status lookup and cannot start a late download', async () => {
  let finish, signal;
  const original = requestJson.getMockImplementation();
  requestJson.mockImplementation((url, options) => url.includes('B.ibt')
    ? new Promise(resolve => { finish = resolve; signal = options.signal; }) : original(url, options));
  const view = await open(); await choose('B.ibt');
  await waitFor(() => expect(finish).toBeDefined());
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish({ health: health('B.ibt'), job: null }));
  expect(starts()).toHaveLength(0);
});

test('collection reuses the parsed preview and does not wait for the personal reference search', async () => {
  let finishSession, finishReferences;
  const original = labRequest.getMockImplementation();
  labRequest.mockImplementation((route, body, signal) => body.filename === 'B.ibt' && route === 'session'
    ? new Promise(resolve => { finishSession = resolve; }) : body.filename === 'B.ibt' && route === 'references'
      ? new Promise(resolve => { finishReferences = resolve; }) : original(route, body, signal));
  await open();
  fireEvent.change(screen.getByLabelText('주행 파일 · IBT'), { target: { value: 'B.ibt' } });
  await waitFor(() => expect(finishSession).toBeDefined());
  expect(requestJson.mock.calls.some(([url]) => url.includes('B.ibt'))).toBe(false);
  await act(async () => finishSession(session('B.ibt')));
  await waitFor(() => expect(starts()).toHaveLength(1));
  expect(finishReferences).toBeDefined();
  expect(onApply).not.toHaveBeenCalled();
  await act(async () => finishReferences({ files: [], personalReference: { filename: 'B.ibt', lap: 2 } }));
  await ready();
});

test('collection completion during an older reference search cannot be overwritten by its empty catalog', async () => {
  jest.useFakeTimers();
  let finish;
  const original = labRequest.getMockImplementation();
  labRequest.mockImplementation((route, body, signal) => route === 'references' && body.filename === 'B.ibt' && !downloaded
    ? new Promise(resolve => { finish = resolve; }) : original(route, body, signal));
  await open();
  fireEvent.change(screen.getByLabelText('주행 파일 · IBT'), { target: { value: 'B.ibt' } });
  await waitFor(() => expect(starts()).toHaveLength(1));
  downloaded = true;
  requestJson.mockResolvedValue({ health: health('B.ibt', false), job: job('B.ibt', false) });
  await act(async () => { jest.advanceTimersByTime(5000); });
  await act(async () => finish({ files: [], personalReference: { filename: 'B.ibt', lap: 2 } }));
  await ready();
  await waitFor(() => expect(screen.getByRole('option', { name: 'Garage61 · 내려받은 기준' })).toBeEnabled());
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
});
