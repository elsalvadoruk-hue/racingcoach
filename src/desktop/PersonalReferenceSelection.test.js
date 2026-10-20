import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SourcePicker from './SourcePicker';
import { activateSession, currentSessionResult, data, LAST_SESSION_KEY, labRequest, rememberSession, selectGame } from './sessionData';
import { readDesktopLaps } from '../analysis/desktopSelection';

jest.mock('./sessionData', () => {
  const actual = jest.requireActual('./sessionData');
  return Object.defineProperty({ ...actual, labRequest: jest.fn() }, 'data', { get: () => actual.data });
});
const validLap = (lap_number, lap_time_s) => ({ lap_number, lap_time_s, status: 'VALID', complete: true });
const personal = { status: 'READY', filename: 'best-file', lap: 7, lap_time_s: 87 };
const session = { kind: 'LIVE_LOCAL', filename: 'current-file', defaultTarget: 2, defaultReference: 7,
  defaultReferenceFile: 'best-file', reference_selection: 'AUTO', personalReference: personal,
  session: { track_length_m: 1000 }, laps: [validLap(1, 91), validLap(2, 90)], corners: [], initialComparison: {} };

beforeEach(() => { localStorage.clear(); labRequest.mockReset(); });
afterEach(() => selectGame('iracing'));

test.each(['iracing', 'lmu'])('%s falls back from unavailable Garage61 to a compatible best lap in another file', async game => {
  selectGame(game); activateSession(currentSessionResult({ ...session, provider: game }));
  labRequest.mockImplementation(async route => {
    if (route === 'files') return { files: [{ name: 'current-file' }, { name: 'best-file', file_name: 'best run' }] };
    if (route === 'references') return { files: [], personalReference: personal };
    if (route === 'file-laps') return { laps: [validLap(7, 87), validLap(8, 89)] };
    if (route === 'comparison') return { reference_filename: 'best-file', reference_lap: 7 };
    throw new Error(route);
  });
  const onApply = jest.fn();
  render(<SourcePicker target="2" reference="7" external={['missing.csv']} analysisWorkspace={{ game }} onApply={onApply} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
  expect(screen.getByLabelText('기준 종류')).toHaveValue('LOCAL');
  expect(screen.getByLabelText('기준 선택 방식')).toHaveValue('AUTO');
  expect(screen.getByLabelText(`기준 주행 파일 · ${game === 'lmu' ? 'LMU' : 'IBT'}`)).toHaveValue('best-file');
  expect(screen.getByText(/Garage61 기준을 사용할 수 없어/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
  expect(labRequest).toHaveBeenCalledWith('comparison', expect.objectContaining({ reference_selection: 'AUTO',
    reference_filename: 'best-file', reference_lap: 7, reference_mode: 'LOCAL', external_reference_files: [] }));
});

test('a Garage61 lookup error retains the local session and its automatic reference', async () => {
  selectGame('iracing'); activateSession(currentSessionResult(session));
  labRequest.mockImplementation(async route => {
    if (route === 'files') return { files: [{ name: 'current-file' }, { name: 'best-file' }] };
    if (route === 'references') throw new Error('연결되지 않았습니다.');
    if (route === 'file-laps') return { laps: [validLap(7, 87)] };
    if (route === 'comparison') return { reference_filename: 'best-file', reference_lap: 7 };
    throw new Error(route);
  });
  const onApply = jest.fn();
  render(<SourcePicker target="2" reference="7" external={[]} analysisWorkspace={{}} onApply={onApply} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(onApply).toHaveBeenCalled());
  expect(onApply.mock.calls[0][0].filename).toBe('current-file');
});

test('iRacing preserves another-file reference identity and automatic selection across reloads', () => {
  activateSession(currentSessionResult({ ...session, provider: 'IRACING' }));
  expect(data.reference_filename).toBe('best-file');
  rememberSession(data, '2', '7', []);
  expect(JSON.parse(localStorage.getItem(LAST_SESSION_KEY))).toMatchObject({ reference_filename: 'best-file', reference_selection: 'AUTO' });
  expect(readDesktopLaps('current-file', [])).toEqual({ targetLapOverride: 2, referenceLapOverride: null,
    referenceFilenameOverride: 'best-file', referenceSelectionOverride: 'AUTO' });
});
