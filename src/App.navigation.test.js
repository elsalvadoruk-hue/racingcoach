import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

jest.mock('./components/CornerComparison', () => () => <div>입력 비교 차트</div>);

// Whole-app flows render every tab; several take 3-4 s alone and pass the 5 s default when the full suite runs in band.
jest.setTimeout(20000);

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

test('analysis replaces pending metadata with measured lap availability', async () => {
  const originalFetch = global.fetch.getMockImplementation();
  let metadataReady = false;
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('/api/files')) return response({ files: files.map((file) => file.name === 'A.ibt' && metadataReady
      ? { ...file, metadata_pending: false, valid_laps: 1, lap_count: 1 }
      : { ...file, metadata_pending: true, analyzable: null, valid_laps: null, lap_count: null }) });
    if (url.endsWith('/api/analyze')) {
      metadataReady = true;
      return response({ ...analysisFor('A.ibt'),
      data_quality: { state: 'PARTIAL', valid_laps: 1, complete_laps: 1, coaching_ready: true },
      laps: [{ lap_number: 1, status: 'VALID' }] });
    }
    return originalFetch(url, options);
  });
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  const selected = screen.getByRole('button', { name: /A\.ibt/ });
  await waitFor(() => expect(selected).toHaveTextContent('유효 1 / 1랩'));
  expect(selected).not.toHaveTextContent('확인 중');
});

test('tabs preserve feedback selections and coach links open the matching screen', async () => {
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  fireEvent.click(screen.getByRole('button', { name: '셋업 실험' }));
  fireEvent.click(screen.getByRole('button', { name: '진입', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '고속', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '언더스티어', exact: true }));
  fireEvent.change(screen.getByRole('textbox', { name: '드라이버 추가 피드백' }), { target: { value: 'T1에서 밀림' } });
  fireEvent.click(screen.getByRole('button', { name: '주행 요약' }));
  fireEvent.click(screen.getByRole('button', { name: '셋업 후보', exact: true }));
  expect(screen.getByRole('textbox', { name: '드라이버 추가 피드백' })).toHaveValue('T1에서 밀림');
  expect(screen.getByRole('button', { name: '고속', exact: true })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '피드백으로 분석 갱신' }));
  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/analyze'), expect.objectContaining({
    body: expect.stringContaining('"feedback_phase":"ENTRY"'),
  })));
});

test('manual links reveal real feature sections and preserve the current feedback', async () => {
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  fireEvent.click(screen.getByRole('button', { name: '증상 입력하러 가기' }));
  fireEvent.change(screen.getByRole('textbox', { name: '드라이버 추가 피드백' }), { target: { value: '가이드 확인 전 작성' } });
  fireEvent.click(screen.getByRole('button', { name: '처음 사용 가이드', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '증상 입력 화면 열기' }));
  expect(screen.getByRole('textbox', { name: '드라이버 추가 피드백' })).toHaveValue('가이드 확인 전 작성');
  fireEvent.click(screen.getByRole('button', { name: '사용 가이드', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '셋업 A/B 비교 열기' }));
  expect(screen.getByRole('heading', { name: '셋업 A/B 비교', exact: true })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '사용 가이드', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: 'Garage61 기준 주행 열기' }));
  expect(screen.getByRole('heading', { name: 'Garage61 기준 주행', exact: true })).toBeVisible();
});

test('a late response from the previous IBT never replaces the selected file analysis', async () => {
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  const originalFetch = global.fetch.getMockImplementation();
  let resolveOldRequest;
  global.fetch.mockImplementation((url, options) => {
    if (url.endsWith('/api/analyze') && JSON.parse(options.body).filename === 'B.ibt') {
      return new Promise((resolve) => { resolveOldRequest = resolve; });
    }
    return originalFetch(url, options);
  });
  fireEvent.click(screen.getByRole('button', { name: /A\.ibt/ }));
  fireEvent.click(screen.getByRole('option', { name: /B\.ibt/ }));
  await waitFor(() => expect(resolveOldRequest).toBeDefined());
  fireEvent.click(screen.getByRole('button', { name: /B\.ibt/ }));
  fireEvent.click(screen.getByRole('option', { name: /C\.ibt/ }));
  await screen.findAllByText('Target C.ibt');
  await act(async () => resolveOldRequest(response(analysisFor('B.ibt'))));
  expect(screen.getAllByText('Target C.ibt')[0]).toBeVisible();
  expect(screen.queryByText('Target B.ibt')).not.toBeInTheDocument();
});

test('an active trial keeps A across a new file, requires explicit B, and restores after reload', async () => {
  const view = render(<App />);
  await screen.findAllByText('Target A.ibt');
  fireEvent.click(screen.getByRole('button', { name: '셋업 실험', exact: true }));
  fireEvent.change(screen.getByRole('combobox', { name: '실험 목표' }), { target: { value: 'COMFORT' } });
  fireEvent.click(screen.getAllByRole('button', { name: '이 변경 시험하기' })[0]);
  const trial = screen.getByRole('region', { name: '진행 중 셋업 실험' });
  expect(trial).toHaveTextContent('A: A.ibt');
  fireEvent.click(screen.getAllByRole('button', { name: /A\.ibt/ })[0]);
  fireEvent.click(screen.getByRole('option', { name: /B\.ibt/ }));
  await screen.findAllByText('Target B.ibt');
  expect(trial).toHaveTextContent('A: A.ibt');
  expect(trial).toHaveTextContent('변경 후 주행 연결 대기');
  fireEvent.click(screen.getByRole('button', { name: '현재 주행을 변경 후 B로 연결' }));
  fireEvent.change(screen.getByRole('combobox', { name: '주행 후 운전 편의' }), { target: { value: '4' } });
  fireEvent.click(screen.getByRole('button', { name: 'A/B 결과 확인' }));
  await waitFor(() => expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/experiments/compare')).length).toBe(1));
  const firstComparison = JSON.parse(global.fetch.mock.calls.find(([url]) => url.endsWith('/api/experiments/compare'))[1].body);
  expect(firstComparison).toMatchObject({ baseline_file: 'A.ibt', candidate_file: 'B.ibt', goal: 'COMFORT', driver_feedback: null });
  fireEvent.change(screen.getByRole('combobox', { name: '주행 후 증상 변화' }), { target: { value: 'IMPROVED' } });
  fireEvent.change(screen.getByRole('combobox', { name: '주행 후 운전 편의' }), { target: { value: '4' } });
  fireEvent.change(screen.getByRole('textbox', { name: '셋업 변경 부작용' }), { target: { value: '탈출은 약간 불안' } });
  view.unmount();
  render(<App />);
  expect(screen.getByRole('region', { name: '진행 중 셋업 실험' })).toHaveTextContent('A: A.ibt');
  expect(screen.getByRole('region', { name: '진행 중 셋업 실험' })).toHaveTextContent('B: B.ibt');
  expect(screen.getByRole('combobox', { name: '주행 후 운전 편의' })).toHaveValue('4');
  expect(screen.getByRole('textbox', { name: '셋업 변경 부작용' })).toHaveValue('탈출은 약간 불안');
  await screen.findAllByText('Target B.ibt');
});

test('a candidate cannot start after its symptom input changes until analysis is refreshed', async () => {
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  fireEvent.click(screen.getByRole('button', { name: '셋업 실험', exact: true }));
  expect(screen.getAllByRole('button', { name: '이 변경 시험하기' })[0]).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '언더스티어', exact: true }));
  expect(screen.getAllByRole('button', { name: '이 변경 시험하기' })[0]).toBeDisabled();
  expect(screen.getByText(/이전 분석 조건의 결과/)).toBeInTheDocument();
});

test('a new telemetry event offers B without replacing the active trial baseline', async () => {
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'A.ibt', file_signature: [1] } }) }));
  fireEvent.click(screen.getByRole('button', { name: '셋업 실험', exact: true }));
  fireEvent.click(screen.getAllByRole('button', { name: '이 변경 시험하기' })[0]);
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [2] } }) }));
  expect(await screen.findByRole('button', { name: '새 주행 확인' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '진행 중 셋업 실험' })).toHaveTextContent('A: A.ibt');
  expect(screen.queryByText('Target B.ibt')).not.toBeInTheDocument();
});

test('a locked new recording preserves the previous analysis then opens after unlocking', async () => {
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  let locked = true;
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('/api/files')) return response({ files: files.map((file) => file.name === 'B.ibt' && locked
      ? { ...file, analyzable: false, retryable: true, status: 'WAITING_FILE', reason: 'IBT 파일 읽기 대기' } : file) });
    return original(url, options);
  });
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'A.ibt', file_signature: [1] } }) }));
  const callsBefore = global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/analyze')).length;
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [2], retryable: true, analyzable: false, reason: 'IBT 파일 읽기 대기' } }) }));
  expect(screen.getAllByText('Target A.ibt')[0]).toBeVisible();
  expect(screen.queryByText('Target B.ibt')).not.toBeInTheDocument();
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/analyze')).length).toBe(callsBefore);
  locked = false;
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'B.ibt', file_signature: [2], retryable: false } }) }));
  await screen.findAllByText('Target B.ibt');
});

test('starting with only a locked file waits and analyzes it when the same signature unlocks', async () => {
  let source;
  global.EventSource = class { constructor() { source = this; } close() {} };
  const original = global.fetch.getMockImplementation();
  let locked = true;
  global.fetch.mockImplementation((url, options) => {
    const file = { ...files[0], analyzable: locked ? false : true, retryable: locked, status: locked ? 'WAITING_FILE' : 'READY', reason: locked ? 'IBT 파일 읽기 대기' : null };
    if (url.includes('/api/files')) return response({ files: [file] });
    if (url.endsWith('/api/status')) return response({ telemetry: { ...file, file_name: file.name, file_signature: [1] } });
    return original(url, options);
  });
  render(<App />);
  await screen.findByText('IBT 파일 읽기 대기');
  expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/api/analyze'))).toBe(false);
  locked = false;
  await act(async () => source.onmessage({ data: JSON.stringify({ telemetry: { file_name: 'A.ibt', file_signature: [1], retryable: false } }) }));
  await screen.findAllByText('Target A.ibt');
});

test('late reference discovery uses recommendations despite an initially autosaved empty draft', async () => {
  const original = global.fetch.getMockImplementation();
  let resolveReferences;
  const pending = new Promise((resolve) => { resolveReferences = resolve; });
  global.fetch.mockImplementation((url, options) => url.includes('/api/external-references') ? pending : original(url, options));
  render(<App />);
  await waitFor(() => expect(JSON.parse(localStorage.getItem('iracing-tuner.workflow.v1')).drafts['A.ibt'].referenceFiles).toEqual([]));
  await act(async () => resolveReferences(response({ files: [{ name: 'one.csv', usable: true }], recommended_selection: ['one.csv'] })));
  await waitFor(() => {
    const calls = global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/analyze'));
    expect(calls.length).toBeGreaterThan(0);
    expect(JSON.parse(calls[0][1].body).external_reference_files).toEqual(['one.csv']);
  });
  expect(screen.getByRole('combobox', { name: '비교 기준 방식' })).toHaveValue('AGGREGATE');
  expect(screen.getByRole('button', { name: '비교 기준 적용' })).toBeEnabled();
});

test('local, aggregate and single reference modes send consistent explicit references', async () => {
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => {
    if (url.includes('/api/external-references')) return Promise.resolve(response({ files: [
      { name: 'one.csv', driver_name: 'Driver One', usable: true }, { name: 'two.csv', driver_name: 'Driver Two', usable: true },
    ], recommended_selection: ['one.csv', 'two.csv'] }));
    if (url.endsWith('/api/analyze')) {
      const request = JSON.parse(options.body); const value = analysisFor(request.filename);
      if (request.external_reference_files.length) value.external_reference = { status: 'READY', aggregate: { target_time: 'Target A.ibt', reference_median_time: 'External reference', gap_to_reference_median_s: 2 }, corners: [], visualizations: { trace: [] }, top_losses: [] };
      return Promise.resolve(response(value));
    }
    return original(url, options);
  });
  render(<App />);
  await screen.findAllByText('External reference');
  fireEvent.change(screen.getByRole('combobox', { name: '비교 기준 방식' }), { target: { value: 'LOCAL' } });
  fireEvent.click(screen.getByRole('button', { name: '비교 기준 적용' }));
  await waitFor(() => expect(screen.queryAllByText('External reference')).toHaveLength(0));
  const lastRequest = () => JSON.parse(global.fetch.mock.calls.filter(([url]) => url.endsWith('/api/analyze')).slice(-1)[0][1].body);
  expect(lastRequest().external_reference_files).toEqual([]);
  fireEvent.change(screen.getByRole('combobox', { name: '비교 기준 방식' }), { target: { value: 'SINGLE' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Garage61 실제 기준 랩' }), { target: { value: 'two.csv' } });
  fireEvent.click(screen.getByRole('button', { name: '비교 기준 적용' }));
  await screen.findAllByText('External reference');
  expect(lastRequest().external_reference_files).toEqual(['two.csv']);
});

test('clean corner observations enable exploration without inventing valid lap pace', async () => {
  const original = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => {
    if (!url.endsWith('/api/analyze')) return original(url, options);
    const value = analysisFor('A.ibt');
    value.comparison = null;
    value.data_quality = { valid_laps: 0, coaching_ready: true, analysis_scope: 'CLEAN_SEGMENTS_ONLY', clean_segment_count: 1, state: 'PARTIAL', messages: [] };
    value.coaching = { clean_segment_corners: [{ id: 'C1', label: 'T1', lap_number: 2, entry_pct: .1, apex_pct: .12, exit_pct: .15, speed_class: 'LOW', target: { min_speed_kph: 75 }, scope: 'COACHING_ONLY' }] };
    return Promise.resolve(response(value));
  });
  render(<App />);
  expect(await screen.findByText(/유효 완주랩 0개 · 깨끗한 코너 구간만 관측/)).toBeInTheDocument();
  expect(screen.queryByText('Target A.ibt')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '셋업 실험', exact: true }));
  expect(screen.getAllByRole('button', { name: '이 변경 시험하기' })[0]).toBeEnabled();
});

test('exploration is selected by default and evidence mode is sent without losing feedback', async () => {
  const originalFetch = global.fetch.getMockImplementation();
  global.fetch.mockImplementation((url, options) => {
    if (url.endsWith('/api/analyze')) {
      const request = JSON.parse(options.body);
      const exploratory = request.setup_mode === 'EXPLORE' && request.feedback_symptom;
      const candidate = { setting: 'rear_arb_blades', label: 'Rear ARB blades', original_value: 3, value: 4,
        unit: 'blade', tier: 'EXPLORATORY', confidence: .35, source: 'DRIVER_FEEDBACK_EXPLORATION' };
      return Promise.resolve(response({ ...analysisFor(request.filename),
        setup_recommendation: { setup_mode: request.setup_mode, decision: exploratory ? 'TEST' : 'HOLD',
          decision_basis: exploratory ? 'DRIVER_FEEDBACK_EXPLORATION' : 'NO_ACTIONABLE_SETUP_EVIDENCE',
          experimental_candidates: exploratory ? [candidate] : [],
          exploration: { reason: exploratory ? '선택한 증상으로 작은 변경을 시험합니다.' : '증상을 선택하세요.' } },
      }));
    }
    return originalFetch(url, options);
  });
  render(<App />);
  await screen.findAllByText('Target A.ibt');
  fireEvent.click(screen.getByRole('button', { name: '셋업 실험', exact: true }));
  expect(screen.getByRole('button', { name: '탐색 추천', exact: true })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '탐색 추천', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '진입', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '고속', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '언더스티어', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '피드백으로 분석 갱신', exact: true }));
  expect(await screen.findByText('탐색 후보 · 근거 약함')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '근거 우선 추천', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '주행 요약', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '셋업 실험', exact: true }));
  expect(screen.getByRole('button', { name: '근거 우선 추천', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: '언더스티어', exact: true })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '피드백으로 분석 갱신', exact: true }));
  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/analyze'), expect.objectContaining({
    body: expect.stringContaining('"setup_mode":"EVIDENCE"'),
  })));
  await waitFor(() => expect(screen.queryByText('탐색 후보 · 근거 약함')).not.toBeInTheDocument());
});
