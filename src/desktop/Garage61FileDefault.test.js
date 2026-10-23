import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import SourcePicker from './SourcePicker';
import { activateSession, currentSessionResult, labRequest, selectGame } from './sessionData';

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
async function picker({ external = ['A.csv'], game = 'iracing', trial } = {}) {
  selectGame(game); activateSession(currentSessionResult({ ...session('A.ibt'), provider: game }));
  render(<SourcePicker target="2" reference="2" external={external} onApply={jest.fn()}
    analysisWorkspace={{ game, activeTrial: trial }} />);
  await ready();
}
async function changeFile(file, game = 'iracing') {
  fireEvent.change(screen.getByLabelText(`주행 파일 · ${game === 'lmu' ? 'LMU DuckDB' : 'IBT'}`), { target: { value: file } });
  await ready();
}
function selected() { return [...screen.getByLabelText('Garage61 기준 · Ctrl 키로 여러 랩 선택').selectedOptions].map(option => option.value); }

test('switching files selects compatible server recommendations and applying uses them', async () => {
  await picker(); await changeFile('B.ibt');
  expect(screen.getByLabelText('기준 종류')).toHaveValue('GARAGE61');
  expect(selected()).toEqual(['B2.csv']);
  expect(screen.queryByText(/Garage61 기준을 사용할 수 없어/)).not.toBeInTheDocument();
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
  expect(screen.getByText(/Garage61 기준을 사용할 수 없어/)).toBeInTheDocument();
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
