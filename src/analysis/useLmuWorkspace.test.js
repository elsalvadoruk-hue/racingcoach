import { act, renderHook, waitFor } from '@testing-library/react';
import { LMU_RECORDING_POLL_MS, useLmuWorkspace } from './useLmuWorkspace';
import { labRequest, LAST_SESSION_KEY, LMU_SESSION_KEY, selectGame } from '../desktop/sessionData';

jest.mock('../desktop/sessionData', () => ({
  ...jest.requireActual('../desktop/sessionData'), labRequest: jest.fn(),
}));

const session = (filename, target = 3, reference = 2) => ({
  filename, provider: 'LMU', defaultTarget: target, defaultReference: reference,
  session: { track_length_m: 5793 }, laps: [], corners: [], initialComparison: {},
});
const file = name => ({ name, analyzable: true, valid_laps: 1 });
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

test('startup failure can be retried immediately without waiting for the catalog timer', async () => {
  labRequest.mockRejectedValueOnce(unavailable(503));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(result.current.error).toContain('503');
  labRequest.mockResolvedValueOnce({ files: [file('recovered')] }).mockResolvedValueOnce(session('recovered'));
  act(() => result.current.retryInitialization());
  await waitFor(() => expect(result.current.selectedFile).toBe('recovered'));
  expect(result.current.error).toBe('');
  expect(result.current.initialLoading).toBe(false);
  expect(labRequest.mock.calls[2][1].progress_id).toEqual(expect.any(String));
});

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
    ['files', {}], ['session', { filename: 'new', reference_selection: 'AUTO', progress_id: expect.any(String) }],
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
  expect(labRequest).toHaveBeenLastCalledWith('session', { ...saved, reference_selection: 'AUTO', progress_id: expect.any(String) }, expect.any(AbortSignal));
  expect(workspaceData().defaultTarget).toBe(2);
});

test('retries default laps when saved lap numbers are no longer usable', async () => {
  localStorage.setItem(LMU_SESSION_KEY, JSON.stringify({ filename: 'latest', target_lap: 99 }));
  labRequest.mockResolvedValueOnce({ files: [file('latest'), file('old')] })
    .mockRejectedValueOnce(unavailable(422)).mockResolvedValueOnce(session('latest'));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(labRequest).toHaveBeenLastCalledWith('session', { filename: 'latest', reference_selection: 'AUTO', progress_id: expect.any(String) }, expect.any(AbortSignal));
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

describe('recordings arriving while the LMU screen stays open', () => {
  const recording = (name, version = 1, extra = {}) => ({ ...file(name), file_signature: [100, version], auto_load: true, ...extra });
  const tick = async () => { await act(async () => { jest.advanceTimersByTime(LMU_RECORDING_POLL_MS); }); };
  const opened = () => labRequest.mock.calls.filter(([route]) => route === 'session').map(([, body]) => body);
  let catalog, nextSession;
  beforeEach(() => {
    jest.useFakeTimers();
    catalog = [recording('old')];
    nextSession = async body => session(body.filename);
    labRequest.mockImplementation(async (route, body) => route === 'files' ? { files: catalog } : nextSession(body));
  });
  afterEach(() => { jest.useRealTimers(); });
  const mount = async () => {
    let hook;
    await act(async () => { hook = renderHook(useLmuWorkspace); });
    return hook;
  };

  test('loads a new recording after two stable observations without reopening unchanged files', async () => {
    const { result } = await mount();
    await tick();
    expect(opened()).toHaveLength(1);
    catalog = [recording('hypercar'), ...catalog];
    await tick();
    expect(result.current.selectedFile).toBe('old');
    await tick();
    expect(result.current.selectedFile).toBe('hypercar');
    expect(workspaceData().filename).toBe('hypercar');
    expect(result.current.revision).toBe(2);
    expect(JSON.parse(localStorage.getItem(LMU_SESSION_KEY)).filename).toBe('hypercar');
    await tick();
    expect(opened()).toHaveLength(2);
  });

  test('keeps the previous analysis while writing, changing or temporarily locked, then retries', async () => {
    const { result } = await mount();
    catalog = [recording('hypercar', 1, { analyzable: false, status: 'RECORDING' }), ...catalog];
    await tick(); await tick();
    expect(opened()).toHaveLength(1);
    catalog[0] = recording('hypercar', 2);
    await tick();
    catalog[0] = recording('hypercar', 3);
    await tick();
    nextSession = async body => { if (body.filename === 'hypercar') throw unavailable(409); return session(body.filename); };
    await tick();
    expect(result.current.selectedFile).toBe('old');
    expect(workspaceData().filename).toBe('old');
    nextSession = async body => session(body.filename);
    await tick();
    expect(result.current.selectedFile).toBe('hypercar');
    expect(result.current.error).toBe('');
  });

  test('refreshes a completed file that changes again using fresh automatic lap selection', async () => {
    const { result } = await mount();
    catalog = [recording('old', 2)];
    await tick(); await tick();
    expect(result.current.revision).toBe(2);
    expect(opened()[1]).toEqual({ filename: 'old', reference_selection: 'AUTO', progress_id: expect.any(String) });
  });

  test('starts empty and automatically opens the first completed recording', async () => {
    catalog = [];
    const { result } = await mount();
    expect(result.current.initialLoading).toBe(false);
    catalog = [recording('first')];
    await tick(); await tick();
    expect(result.current.selectedFile).toBe('first');
  });

  test('does not switch the driving file to an imported reference, including on startup', async () => {
    const imported = recording('downloaded', 1, { auto_load: false });
    catalog = [imported, ...catalog];
    const { result } = await mount();
    expect(result.current.selectedFile).toBe('old');
    catalog = [recording('another-reference', 1, { auto_load: false }), ...catalog];
    await tick(); await tick();
    expect(opened()).toHaveLength(1);
    expect(result.current.selectedFile).toBe('old');
  });

  test('keeps a saved imported driving file available', async () => {
    localStorage.setItem(LMU_SESSION_KEY, JSON.stringify({ filename: 'downloaded', target_lap: 2 }));
    catalog = [recording('downloaded', 1, { auto_load: false }), ...catalog];
    const { result } = await mount();
    expect(result.current.selectedFile).toBe('downloaded');
  });

  test('pauses automatic application while choosing files and resumes after closing', async () => {
    const { result } = await mount();
    let resume;
    act(() => { resume = result.current.pauseAutomaticSelection(); });
    catalog = [recording('hypercar'), ...catalog];
    await tick(); await tick();
    expect(result.current.selectedFile).toBe('old');
    act(() => { resume(); });
    await tick();
    expect(result.current.selectedFile).toBe('hypercar');
  });

  test('a manual selection cancels a late automatic load but future recordings still open', async () => {
    const { result } = await mount();
    catalog = [recording('hypercar'), ...catalog];
    await tick();
    nextSession = body => body.filename === 'hypercar' ? new Promise(resolve => { resolvePending = resolve; }) : session(body.filename);
    await tick();
    act(() => { result.current.adoptDesktopAnalysis(session('manual')); });
    const signal = labRequest.mock.calls.filter(([route]) => route === 'session').at(-1)[2];
    expect(signal.aborted).toBe(true);
    await act(async () => { resolvePending(session('hypercar')); });
    await tick();
    expect(result.current.selectedFile).toBe('manual');
    nextSession = async body => session(body.filename);
    catalog = [recording('next-drive'), ...catalog];
    await tick(); await tick();
    expect(result.current.selectedFile).toBe('next-drive');
  });

  test('a manual selection before the first catalog response is not replaced on retry', async () => {
    labRequest.mockImplementationOnce(() => new Promise(resolve => { resolvePending = resolve; }));
    const { result } = await mount();
    act(() => { result.current.adoptDesktopAnalysis(session('manual')); });
    await act(async () => { resolvePending({ files: catalog }); });
    await tick(); await tick();
    expect(result.current.selectedFile).toBe('manual');
    expect(opened()).toHaveLength(0);
  });

  test('retries after a catalog network failure and stops polling on unmount', async () => {
    const { result, unmount } = await mount();
    labRequest.mockRejectedValueOnce(new Error('offline'));
    await tick();
    expect(result.current.selectedFile).toBe('old');
    expect(result.current.error).toBe('offline');
    catalog = [recording('hypercar'), ...catalog];
    await tick(); await tick();
    expect(result.current.selectedFile).toBe('hypercar');
    expect(result.current.error).toBe('');
    unmount();
    const calls = labRequest.mock.calls.length;
    await tick(); await tick();
    expect(labRequest).toHaveBeenCalledTimes(calls);
  });
});

test('the first screen skips a newer recording without a valid lap and opens the newest one with laps', async () => {
  labRequest.mockResolvedValueOnce({ files: [
    { name: 'gravel', analyzable: true, status: 'COACHING_ONLY', valid_laps: 0, complete_laps: 3 },
    { name: 'pending', analyzable: true, valid_laps: null }, file('good'),
  ] }).mockResolvedValueOnce(session('good'));
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.selectedFile).toBe('good'));
  expect(labRequest.mock.calls.map(([route, body]) => [route, body.filename])).toEqual([['files', undefined], ['session', 'good']]);
});
