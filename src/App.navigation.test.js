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
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => url.endsWith('/api/analyze') ? response({ ...analysisFor('A.ibt'), comparison: null,
    data_quality: { valid_laps: 0, coaching_ready: true, analysis_scope: 'CLEAN_SEGMENTS_ONLY', clean_segment_count: 1 },
    coaching: { clean_segment_corners: [{ id: 'C1', label: 'T1', lap_number: 2, target: { min_speed_kph: 75 } }] } }) : original(url, options));
  const { result } = await ready();
  expect(result.current.cleanSectionOnly).toBe(true);
  expect(result.current.displayComparison).toBeNull();
  expect(result.current.comparisonCorners).toHaveLength(1);
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

