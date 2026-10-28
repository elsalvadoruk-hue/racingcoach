import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import SourcePicker from './SourcePicker';
import { activateSession, currentSessionResult, labRequest, selectGame } from './sessionData';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));

jest.mock('./sessionData', () => {
  const actual = jest.requireActual('./sessionData');
  return Object.defineProperty({ ...actual, labRequest: jest.fn() }, 'data', { get: () => actual.data });
});
const lap = { lap_number: 2, lap_time_s: 90, status: 'VALID', complete: true };
const session = filename => ({ kind: 'LIVE_LOCAL', filename, defaultTarget: 2, defaultReference: 2,
  session: { track_length_m: 1000 }, laps: [lap], corners: [], initialComparison: {} });
const personalReference = filename => ({ filename, lap: 2, lap_time_s: 90 });
const reference = name => ({ name, driver_name: name, usable: true, compatible: true, lap_time_s: 89 });
let catalogs, delayed;
beforeEach(() => {
  requestJson.mockReset(); requestJson.mockResolvedValue({ health: { enabled: false }, job: null });
  localStorage.clear(); selectGame('iracing'); activateSession(currentSessionResult(session('A.ibt')));
  catalogs = {
    'A.ibt': { files: [reference('A.csv')], recommended_selection: ['A.csv'] },
    'B.ibt': { files: [reference('B.csv'), reference('B2.csv')], recommended_selection: ['B2.csv'] },
    'C.ibt': { files: [], recommended_selection: [] },
  };
  delayed = null;
  labRequest.mockReset(); labRequest.mockImplementation(async (route, body) => {
    if (route === 'files') return { files: ['A.ibt','B.ibt','C.ibt'].map(name => ({ name })) };
    if (route === 'session') return session(body.filename);
    if (route === 'references') {
      if (delayed && body.filename === 'B.ibt') return new Promise(resolve => { delayed.resolve = resolve; });
      return { ...catalogs[body.filename], personalReference: personalReference(body.filename) };
    }
    if (route === 'comparison') return { reference_lap: 2 };
    throw new Error(route);
  });
});
afterEach(() => selectGame('iracing'));
const ready = () => waitFor(() => expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
async function picker({ external = ['A.csv'], game = 'iracing', trial, snapshot = {}, referenceLap = '2' } = {}) {
  selectGame(game); activateSession(currentSessionResult({ ...session('A.ibt'), provider: game, ...snapshot }));
  render(<SourcePicker target="2" reference={referenceLap} external={external} onApply={jest.fn()}
    analysisWorkspace={{ game, activeTrial: trial }} />);
  await ready();
}
async function changeFile(file, game = 'iracing') {
  fireEvent.change(screen.getByLabelText(`주행 파일 · ${game === 'lmu' ? 'LMU DuckDB' : 'IBT'}`), { target: { value: file } });
  await ready();
}
function selected() { return [...screen.getByLabelText('Garage61 기준 · Ctrl 키로 여러 랩 선택').selectedOptions].map(option => option.value); }

test('changing the primary preserves support and sends the pinned lap first', async () => {
  catalogs['A.ibt'] = { files: [reference('A.csv'), reference('A2.csv'), reference('A3.csv')], recommended_selection: ['A.csv', 'A2.csv'] };
  await picker({ external: ['A.csv', 'A2.csv'] });
  fireEvent.change(screen.getByLabelText('Garage61 대표 기준'), { target: { value: 'A2.csv' } });
  const list = screen.getByLabelText('Garage61 기준 · Ctrl 키로 여러 랩 선택');
  for (const option of list.options) option.selected = true;
  fireEvent.change(list);
  expect(screen.getByLabelText('Garage61 대표 기준')).toHaveValue('A2.csv');
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(labRequest).toHaveBeenCalledWith('comparison', expect.objectContaining({ external_reference_files: ['A2.csv', 'A.csv', 'A3.csv'] })));
});

test('a missing primary falls back to personal instead of promoting supporting laps', async () => {
  catalogs['A.ibt'] = { files: [reference('support.csv')], recommended_selection: ['support.csv'] };
  await picker({ external: ['missing.csv', 'support.csv'] });
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  expect(screen.getByText(/이전에 선택한 Garage61 대표 기준을 사용할 수 없어/)).toBeInTheDocument();
});

test('a missing primary restores an automatic personal lap even after a previous manual selection', async () => {
  await picker({ external: ['missing.csv'], snapshot: { reference_selection: 'MANUAL', defaultReference: null }, referenceLap: '' });
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  expect(screen.getByLabelText('기준 선택 방식')).toHaveValue('AUTO');
  expect(screen.getByLabelText('비교 기준')).toHaveValue('2');
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(labRequest).toHaveBeenCalledWith('comparison', expect.objectContaining({
    reference_selection: 'AUTO', reference_lap: 2, external_reference_files: [],
  })));
});

test('switching files selects compatible server recommendations and applying uses them', async () => {
  await picker(); await changeFile('B.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('GARAGE61');
  expect(selected()).toEqual(['B2.csv']);
  expect(screen.queryByText(/이전에 선택한 Garage61 대표 기준을 사용할 수 없어/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(labRequest).toHaveBeenCalledWith('comparison', expect.objectContaining({
    filename: 'B.ibt', reference_mode: 'SINGLE', external_reference_files: ['B2.csv'],
  })));
  await changeFile('A.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('GARAGE61');
  expect(selected()).toEqual(['A.csv']);
});

test('a new file defaults to Garage61 even when the current file uses a local reference', async () => {
  await picker({ external: [] });
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  await changeFile('B.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('GARAGE61');
  expect(selected()).toEqual(['B2.csv']);
  fireEvent.change(screen.getByLabelText('기준 종류'), { target: { value: 'LOCAL' } });
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
});

test('no compatible Garage61 falls back locally and the next available file uses Garage61 again', async () => {
  catalogs['C.ibt'] = { files: [{ ...reference('broken.csv'), usable: false }, { ...reference('other.csv'), compatible: false }],
    recommended_selection: ['broken.csv','other.csv'] };
  await picker(); await changeFile('C.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  expect(screen.getByText(/이 차량·트랙과 맞는 내려받은 Garage61 기준이 없습니다/)).toBeInTheDocument();
  await changeFile('B.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('GARAGE61');
  expect(selected()).toEqual(['B2.csv']);
});

test('compatible existing selections take precedence over replacing the chosen Garage61 lap', async () => {
  catalogs['B.ibt'].files.push(reference('A.csv'));
  await picker(); await changeFile('B.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('GARAGE61');
  expect(selected()).toEqual(['A.csv']);
});

test('a local comparison also explains why the Garage61 option is unavailable', async () => {
  catalogs['A.ibt'] = { files: [], recommended_selection: [] };
  await picker({ external: [] });
  expect(screen.getByRole('option', { name: 'Garage61 · 내려받은 기준' })).toBeDisabled();
  expect(screen.getByText(/이 차량·트랙과 맞는 내려받은 Garage61 기준이 없습니다/)).toBeInTheDocument();
});

test('an older server without recommendations can still default to its first compatible reference', async () => {
  delete catalogs['B.ibt'].recommended_selection;
  await picker(); await changeFile('B.ibt');
  expect(selected()).toEqual(['B.csv']);
});

test('a late catalog from the previously selected file cannot replace the current selection', async () => {
  await picker(); delayed = {};
  fireEvent.change(screen.getByLabelText('주행 파일 · IBT'), { target: { value: 'B.ibt' } });
  await waitFor(() => expect(delayed.resolve).toBeDefined());
  await changeFile('C.ibt');
  await act(async () => delayed.resolve({ ...catalogs['B.ibt'], personalReference: personalReference('B.ibt') }));
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  expect(screen.getByLabelText('주행 파일 · IBT')).toHaveValue('C.ibt');
});

test('a trial with a frozen local baseline does not switch to Garage61', async () => {
  await picker({ external: [], trial: { reference_mode: 'LOCAL', reference_files: [] } });
  await changeFile('B.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  expect(screen.getByLabelText('기준 종류')).toBeDisabled();
});

test('LMU stays local even if a catalog happens to contain Garage61 rows', async () => {
  await picker({ external: [], game: 'lmu' }); await changeFile('B.ibt', 'lmu');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
});

test('opening the current recording shows loading instead of claiming that Garage61 is empty', async () => {
  const original = labRequest.getMockImplementation();
  let finish;
  labRequest.mockImplementation((route, body, signal) => route === 'references'
    ? new Promise(resolve => { finish = resolve; }) : original(route, body, signal));
  const view = render(<SourcePicker target="2" reference="2" external={['A.csv']} onApply={jest.fn()}
    analysisWorkspace={{ game: 'iracing' }} />);
  await waitFor(() => expect(finish).toBeDefined());
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  expect(screen.getByText('비교 기준 불러오는 중')).toBeInTheDocument();
  expect(screen.queryByText('이 차량·트랙과 맞는 내려받은 Garage61 기준이 없습니다.')).not.toBeInTheDocument();
  const request = labRequest.mock.calls.find(([route]) => route === 'references');
  expect(request[2].aborted).toBe(false);
  view.unmount();
  expect(request[2].aborted).toBe(true);
  await act(async () => finish({ ...catalogs['A.ibt'], personalReference: personalReference('A.ibt') }));
});

test('file reading and reference lookup remain separate loading phases', async () => {
  await picker();
  const original = labRequest.getMockImplementation();
  let finishSession, finishReferences;
  labRequest.mockImplementation((route, body, signal) => {
    if (route === 'session') return new Promise(resolve => { finishSession = resolve; });
    if (route === 'references') return new Promise(resolve => { finishReferences = resolve; });
    return original(route, body, signal);
  });
  fireEvent.change(screen.getByLabelText('주행 파일 · IBT'), { target: { value: 'B.ibt' } });
  await waitFor(() => expect(finishSession).toBeDefined());
  expect(screen.getByText('주행 파일 읽는 중')).toBeInTheDocument();
  await act(async () => finishSession(session('B.ibt')));
  expect(screen.getByText('비교 기준 불러오는 중')).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  expect(screen.getByRole('button', { name: '비교 기준 불러오는 중…' })).toBeDisabled();
  await act(async () => finishReferences({ ...catalogs['B.ibt'], personalReference: personalReference('B.ibt') }));
  await ready();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(selected()).toEqual(['B2.csv']);
});

test('a catalog error is explained and reopening can recover', async () => {
  const original = labRequest.getMockImplementation();
  labRequest.mockImplementation((route, body, signal) => route === 'references'
    ? Promise.reject(new Error('기준 목록 연결 시간 초과')) : original(route, body, signal));
  const view = render(<SourcePicker target="2" reference="2" external={['A.csv']} onApply={jest.fn()}
    analysisWorkspace={{ game: 'iracing' }} />);
  await ready();
  expect(screen.getByText('기준 목록 연결 시간 초과')).toBeInTheDocument();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  view.unmount();
  labRequest.mockImplementation(original);
  await picker();
  expect(screen.queryByText('기준 목록 연결 시간 초과')).not.toBeInTheDocument();
  expect(selected()).toEqual(['A.csv']);
});

test('background collection shows its reported progress without blocking cached references', async () => {
  requestJson.mockResolvedValue({ health: { enabled: true, context: { context_key: 'A-context' } }, job: {
    job_id: 'A-job', context_key: 'A-context', busy: true, progress: 0.42, state: 'DOWNLOADING',
    message: '기준 CSV를 내려받고 있습니다.',
  } });
  render(<SourcePicker target="2" reference="2" external={['A.csv']} onApply={jest.fn()}
    analysisWorkspace={{ game: 'iracing', garage61Collector: { job: {
      busy: true, progress: 0.42, state: 'DOWNLOADING', message: '기준 CSV를 내려받고 있습니다.',
    } } }} />);
  await ready();
  expect(screen.getByRole('progressbar', { name: 'Garage61 수집 예상 진행률' })).toHaveAttribute('aria-valuenow', '42');
  expect(screen.getByText('기준 CSV를 내려받고 있습니다.')).toBeInTheDocument();
  expect(selected()).toEqual(['A.csv']);
});
