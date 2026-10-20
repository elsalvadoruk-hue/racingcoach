import { act, renderHook, waitFor } from '@testing-library/react';
import { useLmuWorkspace } from './useLmuWorkspace';
import { labRequest, LAST_SESSION_KEY, LMU_SESSION_KEY, selectGame } from '../desktop/sessionData';

jest.mock('../desktop/sessionData', () => ({
  ...jest.requireActual('../desktop/sessionData'), labRequest: jest.fn(),
}));

const session = (filename, target = 3, reference = 2) => ({
  filename, provider: 'LMU', defaultTarget: target, defaultReference: reference,
  session: { track_length_m: 5793 }, laps: [], corners: [], initialComparison: {},
});
const file = name => ({ name, analyzable: true });
const unavailable = status => Object.assign(new Error(`unavailable ${status}`), { status });
const workspaceData = () => jest.requireActual('../desktop/sessionData').data;
let resolvePending;

beforeEach(() => {
  localStorage.clear();
  selectGame('lmu');
  labRequest.mockReset();
  resolvePending = undefined;
});

afterEach(() => { selectGame('iracing'); });

test('opens the newest measurable recording ahead of an older saved file and skips unavailable entries', async () => {
  localStorage.setItem(LMU_SESSION_KEY, JSON.stringify({ filename: 'old', target_lap: 9 }));
  localStorage.setItem(LAST_SESSION_KEY, 'preserved iRacing selection');
  labRequest.mockResolvedValueOnce({ files: [
    { name: 'recording', analyzable: false, status: 'WAL_PRESENT' },
    { name: 'unknown' }, file('new'), file('old'),
  ] }).mockResolvedValueOnce(session('new'));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(labRequest.mock.calls.map(([route, body]) => [route, body])).toEqual([
    ['files', {}], ['session', { filename: 'new', reference_selection: 'AUTO' }],
  ]);
  expect(result.current.selectedFile).toBe('new');
  expect(workspaceData().filename).toBe('new');
  expect(JSON.parse(localStorage.getItem(LMU_SESSION_KEY))).toMatchObject({ filename: 'new', target_lap: 3, reference_lap: 2 });
  expect(localStorage.getItem(LAST_SESSION_KEY)).toBe('preserved iRacing selection');
});

test('restores chosen laps for the latest file', async () => {
  const saved = { filename: 'latest', target_lap: 2, reference_lap: 3, reference_mode: 'LOCAL', external_reference_files: [] };
  localStorage.setItem(LMU_SESSION_KEY, JSON.stringify(saved));
  labRequest.mockResolvedValueOnce({ files: [file('latest')] }).mockResolvedValueOnce(session('latest', 2, 3));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.selectedFile).toBe('latest'));
  expect(labRequest).toHaveBeenLastCalledWith('session', { ...saved, reference_selection: 'AUTO' }, expect.any(AbortSignal));
  expect(workspaceData().defaultTarget).toBe(2);
});

test('retries default laps when saved lap numbers are no longer usable', async () => {
  localStorage.setItem(LMU_SESSION_KEY, JSON.stringify({ filename: 'latest', target_lap: 99 }));
  labRequest.mockResolvedValueOnce({ files: [file('latest'), file('old')] })
    .mockRejectedValueOnce(unavailable(422)).mockResolvedValueOnce(session('latest'));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(labRequest).toHaveBeenLastCalledWith('session', { filename: 'latest', reference_selection: 'AUTO' }, expect.any(AbortSignal));
  expect(result.current.selectedFile).toBe('latest');
  expect(result.current.error).toBe('');
});

test('falls back to the previous recording if a catalog entry becomes unavailable', async () => {
  localStorage.setItem(LMU_SESSION_KEY, 'invalid JSON');
  labRequest.mockResolvedValueOnce({ files: [file('latest'), file('previous')] })
    .mockRejectedValueOnce(unavailable(409)).mockResolvedValueOnce(session('previous'));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(result.current.selectedFile).toBe('previous');
  expect(result.current.error).toBe('');
});

test.each([new Error('Network unavailable'), unavailable(503)])('server or connection failures stop the scan: %s', async error => {
  labRequest.mockResolvedValueOnce({ files: [file('latest'), file('old')] }).mockRejectedValueOnce(error);
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(result.current.error).toBe(error.message);
  expect(labRequest).toHaveBeenCalledTimes(2);
  expect(workspaceData().filename).toBe('');
});

test('a manual selection takes precedence over a late automatic response', async () => {
  labRequest.mockResolvedValueOnce({ files: [file('latest')] }).mockImplementationOnce(() => new Promise(resolve => { resolvePending = resolve; }));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(resolvePending).toBeDefined());
  act(() => { result.current.adoptDesktopAnalysis(session('manual')); });
  expect(labRequest.mock.calls[1][2].aborted).toBe(true);
  await act(async () => { resolvePending(session('latest')); });
  expect(result.current.selectedFile).toBe('manual');
  expect(result.current.initialLoading).toBe(false);
  expect(result.current.revision).toBe(0);
  expect(localStorage.getItem(LMU_SESSION_KEY)).toBeNull();
  expect(workspaceData().filename).toBe('');
});

test('unmounting prevents a late response from replacing the next game session', async () => {
  labRequest.mockResolvedValueOnce({ files: [file('latest')] }).mockImplementationOnce(() => new Promise(resolve => { resolvePending = resolve; }));
  const { unmount } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(resolvePending).toBeDefined());
  unmount();
  selectGame('iracing');
  await act(async () => { resolvePending(session('latest')); });
  expect(workspaceData().provider).toBe('iracing');
  expect(workspaceData().filename).toBe('');
  expect(localStorage.getItem(LMU_SESSION_KEY)).toBeNull();
});

test('shows the last availability error if no candidate can be opened', async () => {
  labRequest.mockResolvedValueOnce({ files: [file('latest'), file('old')] })
    .mockRejectedValueOnce(unavailable(404)).mockRejectedValueOnce(unavailable(400));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(result.current.error).toBe('unavailable 400');
  expect(result.current.selectedFile).toBe('');
});

test('leaves the shared picker available when no measurable recordings exist', async () => {
  labRequest.mockResolvedValueOnce({ files: [{ name: 'untimed', analyzable: false }] });
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(result.current.selectedFile).toBe('');
  expect(result.current.error).toBe('');
  expect(labRequest).toHaveBeenCalledTimes(1);
});
