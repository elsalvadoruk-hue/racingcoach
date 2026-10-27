import { act, renderHook, waitFor } from '@testing-library/react';
import { useAnalysisWorkspace } from './analysis/useAnalysisWorkspace';

const files = ['A.ibt', 'B.ibt', 'C.ibt'].map((name) => ({
  name, analyzable: true, valid_laps: 3, lap_count: 4, car_name: 'GT3', track_name: 'Spa',
}));
const analysisFor = (name) => ({
  session: { car_name: 'GT3', track_name: 'Spa' },
  comparison: { target_lap: 1, reference_lap: 2, target_time: `Target ${name}`, reference_time: '2:20.0', total_delta_s: 1 },
  laps: [], vehicle: {}, performance: { corners: [] }, visualizations: {},
  closed_loop_coach: { workflow: [], next_run: { title: `Next ${name}`, lock: [], success_criteria: [] }, top_setup_candidate: { setting: 'brake_bias', label: '브레이크 배분', original_value: 55, value: 54, unit: '%' } },
  setup_recommendation: { decision: 'TEST', confirmed_adjustments: [{ setting: 'brake_bias', label: '브레이크 배분', original_value: 55, value: 54, unit: '%' }] },
});
const response = (payload) => ({ ok: true, json: async () => payload });

beforeEach(() => {
  localStorage.clear();
  global.EventSource = class { close() {} };
  global.fetch = jest.fn(async (url, options) => {
    if (url.includes('/api/files')) return response({ files });
    if (url.endsWith('/api/status')) return response({ telemetry: null, ai_enabled: false });
    if (url.includes('/api/memory/sessions')) return response({ sessions: [] });
    if (url.includes('/api/external-references')) return response({ files: [], recommended_selection: [] });
    if (url.includes('/api/garage61/collector')) return response({ health: { enabled: false }, job: null });
    if (url.endsWith('/api/experiments/compare')) return response({ comparison: { verdict: 'INCONCLUSIVE', reason: '테스트 표본 부족', performance_axes: {} } });
    if (url.endsWith('/api/analyze')) return response(analysisFor(JSON.parse(options.body).filename));
    return response({ enabled: true, counts: { sessions: 0 } });
  });
  Element.prototype.scrollIntoView = jest.fn();
});

afterEach(() => {
  delete global.fetch;
  delete global.EventSource;
});

const ready = async () => {
  const view = renderHook(() => useAnalysisWorkspace());
  await waitFor(() => expect(view.result.current.initialLoading).toBe(false));
  await waitFor(() => expect(view.result.current.analysis).toBeTruthy());
  return view;
};
const requests = () => global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/analyze')).map(([, options]) => JSON.parse(options.body));

test('startup restores Monza once and ignores the existing Hockenheim announcement on subscription or reconnect', async () => {
  const monza = { ...files[1], name: 'monza.ibt', track_name: 'Monza' };
  const hockenheim = { ...files[0], name: 'hockenheim.ibt', track_name: 'Hockenheim' };
  localStorage.setItem('iracing-tuner.workflow.v1', JSON.stringify({ version: 1, preferences: { selectedFile: monza.name } }));
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  const telemetry = { file_name: hockenheim.name, file_signature: [hockenheim.name, 100, 1], analyzable: true };
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: [hockenheim, monza] })
    : url.endsWith('/api/status') ? response({ telemetry }) : original(url, options));
  const { result } = await ready();
  expect(result.current.selectedFile).toBe(monza.name);
  for (let announcement = 0; announcement < 2; announcement += 1) {
    await act(async () => source.onmessage({ data: JSON.stringify({ telemetry }) }));
  }
  expect(result.current.selectedFile).toBe(monza.name);
  expect(result.current.analysisSnapshot.filename).toBe(monza.name);
  expect(requests().map(request => request.filename)).toEqual([monza.name]);

  // A real update to that latest recording must still be adopted exactly once.
  const updated = { ...telemetry, file_signature: [hockenheim.name, 200, 2] };
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: updated }) }));
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: updated }) }));
  expect(result.current.analysisSnapshot.filename).toBe(hockenheim.name);
  expect(requests().map(request => request.filename)).toEqual([monza.name, hockenheim.name]);
});

test('a new recording arriving during startup is analyzed after restoration finishes', async () => {
  let source, finish;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => {
    if (url.endsWith('/api/status')) return response({ telemetry: { file_name: 'A.ibt', file_signature: [1] } });
    if (url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'A.ibt') {
      return new Promise(resolve => { finish = resolve; });
    }
    return original(url, options);
  });
  const { result } = renderHook(() => useAnalysisWorkspace());
  await waitFor(() => expect(finish).toBeDefined());
  const telemetry = { file_name: 'B.ibt', file_signature: [2] };
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry }) }));
  expect(requests().map(request => request.filename)).toEqual(['A.ibt']);
  await act(async () => finish(response(analysisFor('A.ibt'))));
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry }) }));
  expect(result.current.analysisSnapshot.filename).toBe('B.ibt');
  expect(requests().map(request => request.filename)).toEqual(['A.ibt', 'B.ibt']);
});

test('the first SSE snapshot does not retry a latest recording already rejected during startup', async () => {
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  const telemetry = { file_name: 'A.ibt', file_signature: [1] };
  global.fetch.mockImplementation((url, options) => {
    if (url.endsWith('/api/status')) return response({ telemetry });
    if (url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'A.ibt') {
      return response({ ...analysisFor('A.ibt'), comparison: null,
        data_quality: { analysis_scope: 'UNAVAILABLE', valid_laps: 0, coaching_ready: false } });
    }
    return original(url, options);
  });
  const { result } = await ready();
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry }) }));
  expect(result.current.analysisSnapshot.filename).toBe('B.ibt');
  expect(result.current.error).toBe('');
  expect(requests().map(request => request.filename)).toEqual(['A.ibt', 'B.ibt']);
});

test('a locked latest recording at startup is adopted when it unlocks without a signature change', async () => {
  let source, locked = true;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('/api/files')) return response({ files: [{ ...files[0], analyzable: !locked, retryable: locked }, files[1]] });
    if (url.endsWith('/api/status')) return response({ telemetry: {
      file_name: 'A.ibt', file_signature: [1], analyzable: false, retryable: true,
    } });
    return original(url, options);
  });
  const { result } = await ready();
  expect(result.current.selectedFile).toBe('B.ibt');
  locked = false;
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'A.ibt', file_signature: [1], retryable: false } }) }));
  expect(result.current.analysisSnapshot.filename).toBe('A.ibt');
  expect(requests().map(request => request.filename)).toEqual(['B.ibt', 'A.ibt']);
});

test('startup skips an empty newest recording and keeps the previous usable run after its SSE announcement', async () => {
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const newest = { ...files[0], analyzable: false, reason: 'No observed laps', valid_laps: 0 };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: [newest, files[1]] })
    : url.endsWith('/api/status') ? response({ telemetry: { ...newest, file_name: newest.name, file_signature: [1] } })
    : original(url, options));
  const { result } = await ready();
  expect(result.current.selectedFile).toBe('B.ibt');
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { ...newest, file_name: newest.name, file_signature: [1] } }) }));
  expect(result.current.selectedFile).toBe('B.ibt');
  expect(result.current.analysis.comparison.target_time).toBe('Target B.ibt');
  expect(requests().map(request => request.filename)).toEqual(['B.ibt']);
});

test.each([
  { detected_laps: 0 }, { lap_count: 0 }, { complete_laps: 0 }, { valid_laps: 0 }, { selectable: false },
])('startup excludes confirmed unusable recordings from restored and status candidates: %p', async unavailable => {
  const newest = { ...files[0], ...unavailable };
  localStorage.setItem('iracing-tuner.workflow.v1', JSON.stringify({ version: 1, preferences: { selectedFile: newest.name } }));
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: [newest, files[1]] })
    : url.endsWith('/api/status') ? response({ telemetry: { ...newest, file_name: newest.name, file_signature: [1] } })
    : original(url, options));
  const { result } = await ready();
  expect(result.current.selectedFile).toBe('B.ibt');
  expect(result.current.files).toEqual([newest, files[1]]);
  expect(requests().map(request => request.filename)).toEqual(['B.ibt']);
});

test('pending unknown lap counts remain candidates until the file is read', async () => {
  const pending = { ...files[0], analyzable: null, metadata_pending: true,
    detected_laps: null, lap_count: null, complete_laps: null, valid_laps: null };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: [pending, files[1]] }) : original(url, options));
  const { result } = await ready();
  expect(result.current.selectedFile).toBe(pending.name);
  expect(requests().map(request => request.filename)).toEqual([pending.name]);
});

test.each([false, true])('a zero-valid-lap SSE recording preserves analysis and trial ownership (trial=%s)', async withTrial => {
  let source, catalog = files;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: catalog }) : original(url, options));
  const { result } = await ready();
  if (withTrial) act(() => result.current.beginTrial({ setting: 'brake_bias_pct', original_value: 55, value: 54 }));
  const previousAnalysis = result.current.analysis;
  const unusable = { ...files[1], analyzable: true, detected_laps: 3, valid_laps: 0, status: 'COACHING_ONLY' };
  catalog = [unusable, files[0]];
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { ...unusable, file_name: unusable.name, file_signature: [2] } }) }));
  expect(result.current.selectedFile).toBe('A.ibt');
  expect(result.current.analysis).toBe(previousAnalysis);
  expect(result.current.newFileNotice).toBe('');
  expect(requests().map(request => request.filename)).toEqual(['A.ibt']);
  expect(result.current.activeTrial?.baseline_file || null).toBe(withTrial ? 'A.ibt' : null);
});

test('zero-valid-lap files stay in the raw catalog but cannot be chosen or added to expert overlays', async () => {
  const unusable = { ...files[1], valid_laps: 0, status: 'COACHING_ONLY' };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: [files[0], unusable, files[2]] }) : original(url, options));
  const { result } = await ready();
  const previousAnalysis = result.current.analysis;
  await act(async () => result.current.handleFileChange(unusable.name));
  expect(result.current.selectedFile).toBe('A.ibt');
  expect(result.current.analysis).toBe(previousAnalysis);
  expect(result.current.files).toContainEqual(unusable);
  expect(result.current.overlayFiles.map(file => file.name)).toEqual(['C.ibt']);
  expect(requests().map(request => request.filename)).toEqual(['A.ibt']);
});

test('an unavailable saved recording never selects another automatic baseline during a frozen trial', async () => {
  const trial = { id: 'frozen-trial', baseline_file: 'A.ibt', reference_mode: 'LOCAL', reference_files: [],
    adjustment: { setting: 'brake_bias_pct', original_value: 55, value: 54 } };
  localStorage.setItem('iracing-tuner.workflow.v1', JSON.stringify({ version: 1, activeTrial: trial,
    preferences: { selectedFile: 'A.ibt' } }));
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files')
    ? response({ files: [{ ...files[0], valid_laps: 0 }, files[1]] }) : original(url, options));
  const { result } = renderHook(useAnalysisWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(result.current.activeTrial).toEqual(trial);
  expect(result.current.analysis).toBeNull();
  expect(requests()).toEqual([]);
});

test('a selected trial candidate that becomes unusable keeps its analysis without allowing attachment', async () => {
  let source, catalog = files;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: catalog }) : original(url, options));
  const { result } = await ready();
  act(() => result.current.beginTrial({ setting: 'brake_bias_pct', original_value: 55, value: 54 }));
  await act(async () => result.current.handleFileChange('B.ibt'));
  expect(result.current.canAttachTrial).toBe(true);
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [1] } }) }));
  const previousAnalysis = result.current.analysis;
  const unavailable = { ...files[1], valid_laps: 0 };
  catalog = [unavailable, files[0]];
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { ...unavailable, file_name: 'B.ibt', file_signature: [2] } }) }));
  expect(result.current.selectedFile).toBe('B.ibt');
  expect(result.current.analysis).toBe(previousAnalysis);
  expect(result.current.canAttachTrial).toBe(false);
  expect(result.current.activeTrial.baseline_file).toBe('A.ibt');
});

test('startup tries the previous recording when pending metadata proves the latest has no usable laps', async () => {
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('/api/files')) return response({ files: [{ ...files[0], analyzable: null, metadata_pending: true }, files[1]] });
    if (url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'A.ibt') {
      return Promise.resolve({ ok: false, status: 422, json: async () => ({ detail: 'No clean laps or segments' }) });
    }
    return original(url, options);
  });
  const { result } = await ready();
  expect(requests().map(request => request.filename)).toEqual(['A.ibt', 'B.ibt']);
  expect(result.current.selectedFile).toBe('B.ibt');
  expect(result.current.error).toBe('');
});

test('a server failure stops startup without scanning older recordings', async () => {
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze')
    ? Promise.resolve({ ok: false, status: 500, json: async () => ({ detail: 'Analysis server failure' }) }) : original(url, options));
  const { result } = renderHook(() => useAnalysisWorkspace());
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(requests().map(request => request.filename)).toEqual(['A.ibt']);
  expect(result.current.error).toBe('Analysis server failure');
});

test('a successful response with unavailable measurements falls back without adopting the empty analysis', async () => {
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'A.ibt'
    ? response({ ...analysisFor('A.ibt'), comparison: null, data_quality: { analysis_scope: 'UNAVAILABLE', valid_laps: 0, coaching_ready: false } })
    : original(url, options));
  const { result } = await ready();
  expect(requests().map(request => request.filename)).toEqual(['A.ibt', 'B.ibt']);
  expect(result.current.analysisSnapshot.filename).toBe('B.ibt');
});

test('an unusable newly parsed SSE recording preserves the last analysis and file ownership', async () => {
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const { result } = await ready();
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'B.ibt'
    ? Promise.resolve({ ok: false, status: 422, json: async () => ({ detail: 'No clean laps or segments' }) }) : original(url, options));
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [2] } }) }));
  await waitFor(() => expect(result.current.error).toBe('No clean laps or segments'));
  expect(result.current.selectedFile).toBe('A.ibt');
  expect(result.current.analysisSnapshot.filename).toBe('A.ibt');
  expect(result.current.analysis.comparison.target_time).toBe('Target A.ibt');
});

test('a late startup lookup cannot replace a manual file choice', async () => {
  const original = global.fetch.getMockImplementation();
  let finish;
  global.fetch.mockImplementation((url, options) => url.includes('/api/external-references?filename=A.ibt')
    ? new Promise(resolve => { finish = resolve; }) : original(url, options));
  const { result } = renderHook(() => useAnalysisWorkspace());
  await waitFor(() => expect(finish).toBeDefined());
  await act(async () => result.current.handleFileChange('C.ibt'));
  await act(async () => finish(response({ files: [], recommended_selection: [] })));
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(requests().map(request => request.filename)).toEqual(['C.ibt']);
  expect(result.current.analysisSnapshot.filename).toBe('C.ibt');
});

test('automatic and requested post-drive analysis never request AI', async () => {
  const { result } = await ready();
  await act(async () => result.current.runAnalysis());
  expect(requests().length).toBeGreaterThan(1);
  requests().forEach(body => expect(body).not.toHaveProperty('include_ai'));
  expect(global.fetch.mock.calls.some(([url]) => url.includes('/api/ai/'))).toBe(false);
});

test('analysis replaces pending metadata with measured lap availability', async () => {
  const original = global.fetch.getMockImplementation();
  let metadataReady = false;
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('/api/files')) return response({ files: files.map(f => ({ ...f, metadata_pending: !metadataReady, valid_laps: metadataReady ? 1 : null })) });
    if (url.endsWith('/api/analyze')) { metadataReady = true; return response({ ...analysisFor('A.ibt'),
      data_quality: { state: 'PARTIAL', valid_laps: 1, complete_laps: 1, coaching_ready: true },
      laps: [{ lap_number: 1, status: 'VALID' }] }); }
    return original(url, options);
  });
  const { result } = await ready();
  expect(result.current.selectedFileEntry.valid_laps).toBe(1);
  expect(result.current.selectedFileEntry.metadata_pending).toBe(false);
});

test('feedback and analysis mode survive screen navigation', async () => {
  const { result } = await ready();
  act(() => { result.current.setFeedbackPhase('ENTRY'); result.current.setFeedbackSpeed('HIGH');
    result.current.setFeedbackSymptom('UNDERSTEER'); result.current.setUserFeedback('Turn one');
    result.current.setSetupMode('EVIDENCE'); result.current.selectTab('summary'); });
  act(() => result.current.navigateToSection('briefing'));
  expect(result.current.userFeedback).toBe('Turn one');
  expect(result.current.activeTab).toBe('setup');
  await act(async () => result.current.runAnalysis());
  expect(requests().at(-1)).toMatchObject({ feedback_phase: 'ENTRY', feedback_speed: 'HIGH', feedback_symptom: 'UNDERSTEER', setup_mode: 'EVIDENCE' });
});

test('a late response cannot replace a newer selected IBT', async () => {
  const { result } = await ready();
  const original = global.fetch.getMockImplementation();
  let finish;
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'B.ibt'
    ? new Promise(resolve => { finish = resolve; }) : original(url, options));
  let pending;
  act(() => { pending = result.current.handleFileChange('B.ibt'); });
  await waitFor(() => expect(finish).toBeDefined());
  await act(async () => result.current.handleFileChange('C.ibt'));
  await act(async () => { finish(response(analysisFor('B.ibt'))); await pending; });
  expect(result.current.selectedFile).toBe('C.ibt');
  expect(result.current.analysis.comparison.target_time).toBe('Target C.ibt');
});

test('trial A, explicit B and driver feedback survive file changes and reload', async () => {
  const view = await ready();
  act(() => view.result.current.beginTrial({ setting: 'brake_bias_pct', original_value: 55, value: 54 }));
  expect(view.result.current.activeTrial.baseline_file).toBe('A.ibt');
  await act(async () => view.result.current.handleFileChange('B.ibt'));
  expect(view.result.current.activeTrial.candidate_file).toBeNull();
  expect(view.result.current.canAttachTrial).toBe(true);
  act(() => view.result.current.patchTrial({ candidate_file: 'B.ibt', driver_feedback: { change: 'IMPROVED', rating: 4, side_effects: 'Exit' } }));
  view.unmount();
  const { result } = await ready();
  expect(result.current.activeTrial).toMatchObject({ baseline_file: 'A.ibt', candidate_file: 'B.ibt', driver_feedback: { rating: 4, side_effects: 'Exit' } });
});

test('a changed symptom blocks a stale setup candidate until recalculated', async () => {
  const { result } = await ready();
  expect(result.current.canStartTrial).toBe(true);
  act(() => result.current.setFeedbackSymptom('UNDERSTEER'));
  expect(result.current.canStartTrial).toBe(false);
  await act(async () => result.current.runAnalysis());
  expect(result.current.canStartTrial).toBe(true);
});

test('new telemetry offers B without replacing a trial baseline', async () => {
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const { result } = await ready();
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'A.ibt', file_signature: [1] } }) }));
  act(() => result.current.beginTrial({ setting: 'brake_bias_pct', original_value: 55, value: 54 }));
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [2] } }) }));
  await waitFor(() => expect(result.current.newFileNotice).toBe('B.ibt'));
  expect(result.current.selectedFile).toBe('A.ibt');
  expect(result.current.activeTrial.baseline_file).toBe('A.ibt');
});

test('a locked new recording preserves the previous analysis until unlocked', async () => {
  let source, locked = true;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/files') ? response({ files: files.map(f => f.name === 'B.ibt' && locked
    ? { ...f, analyzable: false, retryable: true } : f) }) : original(url, options));
  const { result } = await ready();
  const before = requests().length;
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [2], retryable: true } }) }));
  expect(result.current.selectedFile).toBe('A.ibt'); expect(requests()).toHaveLength(before);
  locked = false;
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [2], retryable: false } }) }));
  await waitFor(() => expect(result.current.analysis?.comparison.target_time).toBe('Target B.ibt'));
});

test('startup with a locked file waits then analyzes the same signature on unlock', async () => {
  let source, locked = true;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => {
    const file = { ...files[0], analyzable: !locked, retryable: locked };
    if (url.includes('/api/files')) return response({ files: [file] });
    if (url.endsWith('/api/status')) return response({ telemetry: { ...file, file_name: file.name, file_signature: [1] } });
    return original(url, options);
  });
  const { result } = renderHook(() => useAnalysisWorkspace());
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(requests()).toHaveLength(0);
  locked = false;
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'A.ibt', file_signature: [1], retryable: false } }) }));
  await waitFor(() => expect(result.current.analysis).toBeTruthy());
});

test('late reference discovery respects recommendations despite an empty autosaved draft', async () => {
  const original = global.fetch.getMockImplementation(); let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  global.fetch.mockImplementation((url, options) => url.includes('/api/external-references') ? pending : original(url, options));
  const { result } = renderHook(() => useAnalysisWorkspace());
  await waitFor(() => expect(result.current.selectedFile).toBe('A.ibt'));
  await act(async () => finish(response({ files: [{ name: 'one.csv', usable: true }], recommended_selection: ['one.csv'] })));
  await waitFor(() => expect(requests().length).toBeGreaterThan(0));
  expect(requests()[0].external_reference_files).toEqual(['one.csv']);
});

test('local, aggregate and single modes send exact reference selections', async () => {
  const { result } = await ready();
  act(() => { result.current.setReferenceMode('AGGREGATE'); result.current.setSelectedExternalReferences(['one.csv', 'two.csv']); });
  await act(async () => result.current.runAnalysis());
  expect(requests().at(-1).external_reference_files).toEqual(['one.csv', 'two.csv']);
  act(() => result.current.setReferenceMode('SINGLE'));
  await act(async () => result.current.runAnalysis());
  expect(requests().at(-1).external_reference_files).toEqual(['one.csv']);
  act(() => result.current.setReferenceMode('LOCAL'));
  await act(async () => result.current.runAnalysis());
  expect(requests().at(-1).external_reference_files).toEqual([]);
});

test('clean corner observations never invent a valid lap comparison', async () => {
  const { result } = await ready();
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze') ? response({ ...analysisFor('A.ibt'), comparison: null,
    data_quality: { valid_laps: 0, coaching_ready: true, analysis_scope: 'CLEAN_SEGMENTS_ONLY', clean_segment_count: 1 },
    coaching: { clean_segment_corners: [{ id: 'C1', label: 'T1', lap_number: 2, target: { min_speed_kph: 75 } }] } }) : original(url, options));
  await act(async () => result.current.runAnalysis());
  expect(result.current.cleanSectionOnly).toBe(true);
  expect(result.current.displayComparison).toBeNull();
  expect(result.current.comparisonCorners).toHaveLength(1);
});

test('startup skips a pending file when analysis confirms zero valid laps and keeps the next valid run', async () => {
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'A.ibt'
    ? response({ ...analysisFor('A.ibt'), comparison: null,
      data_quality: { valid_laps: 0, complete_laps: 2, coaching_ready: true, analysis_scope: 'CLEAN_SEGMENTS_ONLY' } })
    : original(url, options));
  const { result } = await ready();
  expect(result.current.analysisSnapshot.filename).toBe('B.ibt');
  expect(requests().map(request => request.filename)).toEqual(['A.ibt', 'B.ibt']);
});

test('desktop selection updates existing origin preferences and telemetry ownership', async () => {
  const { result } = await ready();
  const payload = analysisFor('C.ibt');
  act(() => result.current.adoptDesktopAnalysis({ filename: 'C.ibt', defaultTarget: 3, defaultReference: 2,
    external: ['one.csv'], session: {}, analysisResult: payload }, {}));
  expect(result.current.selectedFileRef.current).toBe('C.ibt');
  expect(result.current.analysisSnapshot).toMatchObject({ filename: 'C.ibt', referenceMode: 'SINGLE', references: ['one.csv'], targetLap: 3 });
  await waitFor(() => expect(JSON.parse(localStorage.getItem('iracing-tuner.workflow.v1')).preferences.selectedFile).toBe('C.ibt'));
});

test('desktop adoption collects missing Garage61 data once without replacing the selected comparison', async () => {
  const original = global.fetch.getMockImplementation();
  const health = { enabled: true, needs_collection: true, context: { context_key: 'C-context' } };
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('/api/external-references') && url.includes('C.ibt')) {
      return response({ files: [], recommended_selection: [], collector_health: health });
    }
    if (url.endsWith('/api/garage61/collector/start')) {
      return response({ health, job: { id: 'C-job', busy: true, state: 'STARTING', context_key: 'C-context' } });
    }
    return original(url, options);
  });
  const { result } = await ready();
  const chosen = { filename: 'C.ibt', defaultTarget: 3, defaultReference: 2, external: [],
    reference_selection: 'AUTO', session: {}, analysisResult: analysisFor('C.ibt') };
  act(() => result.current.adoptDesktopAnalysis(chosen, {}));
  await waitFor(() => expect(result.current.garage61Collector.job?.id).toBe('C-job'));
  // Reapplying a lap on the same recording must retain the normal context deduplication.
  await act(async () => result.current.adoptDesktopAnalysis(chosen, {}));
  const starts = global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/garage61/collector/start'));
  expect(starts).toHaveLength(1);
  expect(JSON.parse(starts[0][1].body)).toMatchObject({ filename: 'C.ibt', force: false, collector_mode: 'AUTO' });
  expect(result.current.analysis).toBe(chosen.analysisResult);
  expect(result.current.analysisSnapshot).toMatchObject({ filename: 'C.ibt', referenceMode: 'LOCAL', references: [], targetLap: 3 });
  expect(result.current.loading).toBe(false);
  expect(requests().map(request => request.filename)).toEqual(['A.ibt']);
});

test.each(['references', 'health'])('a late desktop %s response cannot collect for an abandoned recording', async (phase) => {
  const original = global.fetch.getMockImplementation();
  const health = { enabled: true, needs_collection: true, context: { context_key: 'C-context' } };
  let finish;
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('C.ibt') && (phase === 'references' ? url.includes('/api/external-references') : url.includes('/collector/status'))) {
      return new Promise(resolve => { finish = () => resolve(response(phase === 'references'
        ? { files: [], collector_health: health } : { health, job: null })); });
    }
    return original(url, options);
  });
  const { result } = await ready();
  act(() => result.current.adoptDesktopAnalysis({ filename: 'C.ibt', defaultTarget: 3, defaultReference: 2,
    external: [], session: {}, analysisResult: analysisFor('C.ibt') }, {}));
  await waitFor(() => expect(finish).toBeDefined());
  act(() => result.current.adoptDesktopAnalysis({ filename: 'B.ibt', defaultTarget: 1, defaultReference: 2,
    external: [], session: {}, analysisResult: analysisFor('B.ibt') }, {}));
  await act(async () => finish());
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/garage61/collector/start'))).toHaveLength(0);
  expect(result.current.analysisSnapshot.filename).toBe('B.ibt');
});

test.each([
  { enabled: false, needs_collection: true },
  { enabled: true, needs_collection: false },
])('desktop adoption respects collection policy %j', async (health) => {
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.includes('/api/external-references')
    ? response({ files: [], collector_health: health }) : original(url, options));
  const { result } = await ready();
  await act(async () => result.current.adoptDesktopAnalysis({ filename: 'C.ibt', defaultTarget: 3, defaultReference: 2,
    external: [], session: {}, analysisResult: analysisFor('C.ibt') }, {}));
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/garage61/collector/start'))).toHaveLength(0);
});

test('an unmounted desktop workspace cannot start collection after reference lookup', async () => {
  const { result, unmount } = await ready();
  const original = global.fetch.getMockImplementation();
  let finish;
  global.fetch.mockImplementation((url, options) => url.includes('/api/external-references')
    ? new Promise(resolve => { finish = resolve; }) : original(url, options));
  act(() => result.current.adoptDesktopAnalysis({ filename: 'C.ibt', defaultTarget: 3, defaultReference: 2,
    external: [], session: {}, analysisResult: analysisFor('C.ibt') }, {}));
  await waitFor(() => expect(finish).toBeDefined());
  unmount();
  await act(async () => finish(response({ files: [], collector_health: {
    enabled: true, needs_collection: true, context: { context_key: 'C-context' },
  } })));
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/garage61/collector/start'))).toHaveLength(0);
});

test('the last desktop lap pair restores on startup without changing saved reference ownership', async () => {
  localStorage.setItem('iracing.desktop.last-session.v1', JSON.stringify({ filename: 'A.ibt', target_lap: 4, reference_lap: 3, external_reference_files: [] }));
  const { result } = await ready();
  expect(requests()[0]).toMatchObject({ target_lap: 4, reference_lap: 3, external_reference_files: [] });
  expect(result.current.targetLapManual).toBe(true);
  expect(result.current.referenceLapManual).toBe(true);
});

test('a stale saved lap falls back to automatic selection after the server rejects it', async () => {
  localStorage.setItem('iracing.desktop.last-session.v1', JSON.stringify({ filename: 'A.ibt', target_lap: 99, reference_lap: 3, external_reference_files: [] }));
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze') && JSON.parse(options.body).target_lap === 99
    ? Promise.resolve({ ok: false, status: 422, json: async () => ({ detail: '선택한 랩이 없습니다.' }) }) : original(url, options));
  const { result } = await ready();
  expect(requests()).toHaveLength(2);
  expect(requests()[1]).toMatchObject({ target_lap: null, reference_lap: null });
  expect(result.current.error).toBe('');
});

