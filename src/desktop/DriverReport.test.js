import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import DriverReport, { DRIVER_REPORT_TIMEOUT_MS } from './DriverReport';
import useHistoryLibrary from './useHistoryLibrary';

jest.mock('./useHistoryLibrary');
jest.mock('./useDriverJournal', () => () => ({data:{drives:[],candidates:[],challenge_history:[]},busy:false,error:''}));
jest.mock('./DriverResults', () => () => null);

const API = 'http://localhost:24891';
const unavailable = reason => ({ value: null, status: 'UNAVAILABLE', reason });
const metric = (value, status = 'OBSERVED') => ({ value, status, reason: '확인한 운전 구간만 집계했습니다.' });
function report(game = 'iracing', period = 0) {
  return {
    schema_version: 1, game, period_days: period, state: 'EMPTY',
    coverage: { source_count: 0, native_session_count: 0, source_only_count: 0, undated_count: 0, period_session_count: 0, period_observation_count: 0, period_measured_lap_count: 0 },
    career: { distance_m: unavailable('운전 구간을 기다립니다.'), driving_time_s: unavailable('운전 구간을 기다립니다.'), wins: unavailable('최종 클래스 결과가 없습니다.'), podiums: unavailable('최종 클래스 결과가 없습니다.'), average_position: unavailable('최종 클래스 결과가 없습니다.') },
    overview: { title: '나의 주행 이야기를 시작해요', body: '주행 파일을 분석하면 확인된 기록부터 차곡차곡 연결합니다.', scope: '아직 연결된 주행 기록이 없습니다.' },
    pace: { status: 'UNAVAILABLE', median_lap_s: null, mad_pct: null, lap_count: 0, points: [], trend: 'UNAVAILABLE', reason: '측정 가능한 랩과 비교 조건을 먼저 확인합니다.' },
    style: { status: 'UNAVAILABLE', items: [], reason: '운전 성향을 설명할 근거가 아직 충분하지 않습니다.' },
    challenge: { title: '기록 한 번 살펴보기', body: '이미 있는 주행 파일을 분석하고 확인된 내용을 살펴보세요.', action: 'analysis' },
    discoveries: [], recent_drives: [], limitations: ['분석한 원본의 관측이며 게임의 평생 기록이 아닙니다.'],
  };
}
function observed(game = 'iracing', period = 0) {
  return {
    ...report(game, period), state: 'OBSERVED',
    coverage: { source_count: 3, native_session_count: 2, source_only_count: 1, undated_count: 1, period_session_count: 2, period_observation_count: 3, period_measured_lap_count: 7 },
    career: { ...report().career, distance_m: metric(12500, 'PARTIAL'), driving_time_s: metric(3860) },
    overview: { title: `${game}에서 확인한 기록`, body: '비교할 조건을 확인하며 관측을 연결합니다.', scope: '확인된 세션의 관측입니다.' },
    pace: { status: 'PARTIAL', scope: 'GT3 · Monza · 연습', median_lap_s: 91.125, mad_pct: 0.54, lap_count: 7, session_count: 2, trend: 'UNAVAILABLE', reason: '비교 조건을 더 확인합니다.', points: [{ date: '2026-10-25', median_lap_s: 92, mad_pct: 0.6, lap_count: 3 }, { date: '2026-10-26', median_lap_s: 91.125, mad_pct: 0.54, lap_count: 4 }] },
    recent_drives: [{ drive_id: 'drive-1', recorded_at: '2026-10-26T13:00:00Z', car: 'GT3 관측 차량', track: 'Monza', session_type: 'PRACTICE', identity_status: 'NATIVE', measured_laps: 4 }],
    discoveries: [{ id: 'fact-1', date: '2026-10-26', title: '연결한 첫 관측', body: '조건이 확인된 기록을 연결했습니다.' }],
  };
}
function response(data, ok = true, status = 200) { return { ok, status, json: async () => data }; }
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

let originalFetch;
beforeEach(() => {
  originalFetch = global.fetch; global.fetch = jest.fn().mockResolvedValue(response(report()));
  useHistoryLibrary.mockReturnValue({ job: null, busy: false, error: '', waiting: false, start: jest.fn(), pause: jest.fn(), retry: jest.fn() });
});
afterEach(() => { global.fetch = originalFetch; });

test('empty history keeps unknown career metrics unavailable and offers authorized entry points', async () => {
  const onNavigate = jest.fn(), onChooseSource = jest.fn();
  render(<DriverReport game="iracing" apiBase={API} onNavigate={onNavigate} onChooseSource={onChooseSource} />);
  expect(await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' })).toBeInTheDocument();
  const career = screen.getByRole('region', { name: '선택 기간 주행 통계' });
  expect(within(career).getAllByText('—')).toHaveLength(5);
  const summary = screen.getByRole('region', { name: '최근 성장 요약' });
  expect(within(summary).getAllByText('—')).toHaveLength(4);
  expect(within(summary).getByText('개인 페이스')).toBeInTheDocument();
  expect(within(summary).getByText('랩 간 편차 비율')).toBeInTheDocument();
  expect(within(summary).getByText('분석 유효 랩 비율')).toBeInTheDocument();
  expect(within(summary).getByText('시간당 인시던트 점수')).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.queryByText('0 km')).not.toBeInTheDocument();
  expect(screen.getByText('입력을 측정할 수 있는 기록을 확인하고 있어요.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '주행 선택' }));
  fireEvent.click(screen.getByRole('button', { name: '주행 분석 열기' }));
  expect(onChooseSource).toHaveBeenCalledTimes(1);
  expect(onNavigate).toHaveBeenCalledWith('summary');
  expect(fetch).toHaveBeenCalledWith(`${API}/api/driver-report?game=iracing&period_days=0`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByRole('radio', { name: '전체', exact: true })).toHaveAttribute('aria-checked', 'true');
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
});

test('real finite observations show partial totals and measured laps without inventing growth or results', async () => {
  fetch.mockResolvedValue(response(observed()));
  render(<DriverReport game="iracing" apiBase={`${API}/`} />);
  expect(await screen.findByText('12.5 km')).toBeInTheDocument();
  expect(screen.getByText('1시간 4분')).toBeInTheDocument();
  expect(screen.getByLabelText('주행 거리 12.5 km · 확인된 일부')).toBeInTheDocument();
  expect(screen.getByText('1:31.125')).toBeInTheDocument();
  const summary = screen.getByRole('region', { name: '최근 성장 요약' });
  expect(within(summary).getByLabelText('랩 간 편차 비율 0.54%')).toBeInTheDocument();
  expect(within(summary).getByLabelText('개인 페이스 —')).toBeInTheDocument();
  expect(within(summary).getByText('최근 한 기록의 측정 랩')).toBeInTheDocument();
  expect(screen.getByText('GT3 관측 차량')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: '날짜가 확인된 2개 관측의 랩 중앙값' })).toBeInTheDocument();
  expect(screen.getByText('성장 방향은 비교 근거가 쌓인 뒤 살펴봐요.')).toBeInTheDocument();
  expect(screen.queryByText(/페이스 개선/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('우승 —')).toBeInTheDocument();
  expect(screen.getByText('전체 기간 · 관측 3개 · 측정 7랩')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '통계 집계 기준' })).toHaveTextContent('부분 관측 포함');
});

test('quality summaries show observed counters and analysis verdicts, including measured zero', async () => {
  const actual = observed();
  actual.quality = {valid_lap_ratio: {value: 75, status: 'PARTIAL', valid_laps: 3, classified_laps: 4, reason: '앱 판정'},
    incidents_per_hour: {value: 0, status: 'PARTIAL', observed_time_s: 600, increase_points: 0, reason: '개인 카운터'}};
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  expect(await screen.findByLabelText('분석 유효 랩 비율 75%')).toBeInTheDocument();
  expect(screen.getByLabelText('시간당 인시던트 점수 0점/h')).toBeInTheDocument();
  expect(screen.getByText('3/4완주 랩 · 앱 분석 기준')).toBeInTheDocument();
  expect(screen.getByText('레이스·퀄리 · 트랙 위 10분 · 0점 증가')).toBeInTheDocument();
});

test('a single lap never displays zero lap-to-lap dispersion even with an old server response', async () => {
  const actual = observed(); actual.pace.lap_count = 1; actual.pace.mad_pct = 0;
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  expect(await screen.findByText('최근 1랩 · 편차는 2랩부터')).toBeInTheDocument();
  expect(screen.getByLabelText('랩 간 편차 비율 —')).toBeInTheDocument();
  expect(screen.queryByText('0%')).not.toBeInTheDocument();
});

test('game changes abort the previous read and ignore an old response even when transport resolves late', async () => {
  const old = deferred();
  fetch.mockImplementation(url => url.includes('game=lmu') ? Promise.resolve(response(observed('lmu'))) : old.promise);
  const view = render(<DriverReport game="iracing" apiBase={API} />);
  const oldSignal = fetch.mock.calls[0][1].signal;
  view.rerender(<DriverReport game="lmu" apiBase={API} />);
  expect(oldSignal.aborted).toBe(true);
  expect(await screen.findByRole('heading', { name: 'lmu에서 확인한 기록' })).toBeInTheDocument();
  await act(async () => { old.resolve(response(observed())); });
  expect(screen.queryByText('iracing에서 확인한 기록')).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'LMU 드라이버 리포트' })).toBeInTheDocument();
});

test('period changes abort stale reads and prevent old period statistics from leaking', async () => {
  const old = deferred();
  fetch.mockImplementation(url => url.endsWith('period_days=90') ? Promise.resolve(response(observed('iracing', 90))) : old.promise);
  render(<DriverReport apiBase={API} />);
  const oldSignal = fetch.mock.calls[0][1].signal;
  fireEvent.click(screen.getByRole('radio', { name: '90일' }));
  expect(oldSignal.aborted).toBe(true);
  expect(await screen.findByText('12.5 km')).toBeInTheDocument();
  expect(screen.getByText(/최근 90일 · 관측 3개 · 측정 7랩/)).toBeInTheDocument();
  const obsolete = observed(); obsolete.career.distance_m.value = 999000;
  await act(async () => { old.resolve(response(obsolete)); });
  expect(screen.queryByText('999 km')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', { name: '90일' }));
  expect(screen.getByRole('radio', { name: '90일' })).toHaveAttribute('aria-checked', 'true');
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('a failed refresh preserves the last successful report for the same game and period', async () => {
  fetch.mockResolvedValueOnce(response(observed())).mockRejectedValueOnce(new Error('분석 서버 연결을 확인해 주세요.'));
  render(<DriverReport apiBase={API} />);
  await screen.findByText('12.5 km');
  fireEvent.click(screen.getByRole('button', { name: '새로고침' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('분석 서버 연결을 확인해 주세요.');
  expect(screen.getByText('12.5 km')).toBeInTheDocument();
  expect(screen.getByText('최근 조회에 실패했어요. 이전에 확인한 기록을 표시합니다.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '다시 시도' })).toBeEnabled();
});

test('initial API errors can be retried and do not become an empty successful report', async () => {
  fetch.mockResolvedValueOnce(response({ detail: '원장을 잠시 읽을 수 없습니다.' }, false, 503)).mockResolvedValueOnce(response(report()));
  render(<DriverReport apiBase={API} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('원장을 잠시 읽을 수 없습니다.');
  expect(screen.queryByRole('button', { name: '주행 선택' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));
  expect(await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' })).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test.each([['game', 'lmu'], ['period_days', 90], ['schema_version', 2]])('a response with the wrong %s is rejected without showing its metrics', async (key, value) => {
  fetch.mockResolvedValue(response({ ...observed(), [key]: value }));
  render(<DriverReport apiBase={API} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('요청한 게임과 기간의 리포트를 확인하지 못했습니다.');
  expect(screen.queryByText('12.5 km')).not.toBeInTheDocument();
});

test('null and unavailable zero values remain unknown and a single observation does not create a graph', async () => {
  const actual = observed();
  actual.career.wins = { value: 0, status: 'UNAVAILABLE', reason: '최종 결과 미보관' };
  actual.career.podiums = metric(null);
  actual.career.average_position = metric(Number.NaN);
  actual.pace.mad_pct = null;
  actual.pace.points = [{ date: '2026-10-26', median_lap_s: 91.125, lap_count: 4 }, { date: null, median_lap_s: 90, lap_count: 4 }];
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  await screen.findByText('1:31.125');
  expect(screen.getByLabelText('우승 —')).toBeInTheDocument();
  expect(screen.getByLabelText('포디엄 —')).toBeInTheDocument();
  expect(screen.getByLabelText('평균 순위 —')).toBeInTheDocument();
  expect(screen.queryByText('0%')).not.toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

test('observed zero counts and explicit trend are displayed without inferring a trend from a graph', async () => {
  const actual = observed(); actual.career.wins = metric(0); actual.pace.trend = 'STEADY';
  Object.assign(actual.pace, { status: 'OBSERVED', pace_change_pct: 0, mad_pct: 0 });
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  expect(await screen.findByLabelText('우승 0회')).toBeInTheDocument();
  expect(screen.getByText('비교 범위에서 비슷한 페이스')).toBeInTheDocument();
  const summary = screen.getByRole('region', { name: '최근 성장 요약' });
  expect(within(summary).getByLabelText('개인 페이스 0%')).toBeInTheDocument();
  expect(within(summary).getByLabelText('랩 간 편차 비율 0%')).toBeInTheDocument();
});

test('evidence dialog exposes partial coverage and actual record detail and returns focus to its trigger', async () => {
  fetch.mockResolvedValue(response(observed()));
  render(<DriverReport apiBase={API} />);
  await screen.findByText('12.5 km');
  const trigger = screen.getByRole('button', { name: '관측 근거' });
  trigger.focus(); fireEvent.click(trigger);
  const dialog = await screen.findByRole('dialog', { name: '관측 근거와 기록' });
  expect(within(dialog).getByText(/확인된 일부 · 확인한 운전 구간만 집계했습니다./)).toBeInTheDocument();
  expect(within(dialog).getByText('전체 기간 통계')).toBeInTheDocument();
  expect(within(dialog).getByText('2026.10.26')).toBeInTheDocument();
  expect(within(dialog).getAllByText('최종 클래스 결과가 없습니다.')).toHaveLength(3);
  fireEvent.click(within(dialog).getAllByRole('button', { name: '닫기', exact: true })[0]);
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
});

test('read cancellation on unmount and callback-only changes do not reload or mutate the report', async () => {
  fetch.mockResolvedValue(response(observed()));
  const view = render(<DriverReport apiBase={API} onChooseSource={jest.fn()} />);
  await screen.findByText('12.5 km');
  const signal = fetch.mock.calls[0][1].signal;
  view.rerender(<DriverReport apiBase={API} onChooseSource={jest.fn()} onNavigate={jest.fn()} />);
  expect(fetch).toHaveBeenCalledTimes(1);
  view.unmount(); expect(signal.aborted).toBe(true);
});

test('a hung read times out, ignores late data and supports a fresh retry', async () => {
  jest.useFakeTimers();
  try {
    const pending = deferred();
    fetch.mockReturnValueOnce(pending.promise).mockResolvedValue(response(observed()));
    render(<DriverReport apiBase={API} />);
    const signal = fetch.mock.calls[0][1].signal;
    await act(async () => { jest.advanceTimersByTime(DRIVER_REPORT_TIMEOUT_MS || 15000); });
    expect(screen.getByRole('alert')).toHaveTextContent('응답이 늦어 조회를 중단했습니다. 다시 시도해 주세요.');
    expect(signal.aborted).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));
    await act(async () => { jest.advanceTimersByTime(0); });
    expect(screen.getByText('12.5 km')).toBeInTheDocument();
    const obsolete = observed(); obsolete.career.distance_m.value = 999000;
    await act(async () => { pending.resolve(response(obsolete)); });
    expect(screen.queryByText('999 km')).not.toBeInTheDocument();
  } finally { jest.useRealTimers(); }
});

test('malformed optional nested values cannot crash the report or invent observations', async () => {
  const malformed = { ...observed(), coverage: { period_session_count: {}, undated_count: [] },
    overview: { title: {}, body: [] }, challenge: { title: [], body: {} }, style: { reason: {} },
    career: { distance_m: { status: 'OBSERVED', value: 12500, reason: {} }, wins: [] },
    pace: { status: 'OBSERVED', median_lap_s: 91.125, scope: {}, reason: {}, points: [null, {}, { date: '2026-02-31', median_lap_s: 90 }] },
    discoveries: [null, {}, { title: {}, body: [] }], recent_drives: [null, { car: {}, track: [], measured_laps: {} }], limitations: [null, {}] };
  fetch.mockResolvedValue(response(malformed));
  render(<DriverReport apiBase={API} />);
  expect(await screen.findByText('12.5 km')).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '관측 근거' }));
  expect(await screen.findByRole('dialog', { name: '관측 근거와 기록' })).toBeInTheDocument();
  expect(screen.queryByText('[object Object]')).not.toBeInTheDocument();
});

test('the pace chart orders real calendar days and scales horizontal spacing by elapsed days', async () => {
  const actual = observed();
  actual.pace.points = [
    { date: '2026-10-10', median_lap_s: 89 }, { date: '2026-10-01', median_lap_s: 91 },
    { date: '2026-10-02', median_lap_s: 90 }, { date: '2026-02-31', median_lap_s: 88 },
  ];
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  const chart = await screen.findByRole('img', { name: '날짜가 확인된 3개 관측의 랩 중앙값' });
  // Verify the actual SVG geometry; accessible text cannot prove day spacing.
  // eslint-disable-next-line testing-library/no-node-access
  const dots = [...chart.querySelectorAll('circle')];
  expect(dots.map(dot => dot.textContent.slice(0, 10))).toEqual(['2026.10.01', '2026.10.02', '2026.10.10']);
  const x = dots.map(dot => Number(dot.getAttribute('cx')));
  expect((x[2] - x[1]) / (x[1] - x[0])).toBeCloseTo(8);
});

test('evidence distinguishes all connected records from the selected comparison period', async () => {
  fetch.mockResolvedValue(response(observed()));
  render(<DriverReport apiBase={API} />);
  await screen.findByText('12.5 km');
  fireEvent.click(screen.getByRole('button', { name: '관측 근거' }));
  const dialog = await screen.findByRole('dialog', { name: '관측 근거와 기록' });
  expect(within(dialog).getByText('선택 기간의 최근 기록')).toBeInTheDocument();
  expect(within(dialog).getByText(/날짜 미확인 기록도 포함합니다/)).toBeInTheDocument();
  expect(within(dialog).getByText(/세션을 확인하지 못한 출처도 측정 가능한 값은 부분 관측으로/)).toBeInTheDocument();
});

test('measurement evidence explains source-only observations, unknown replay and omitted totals in Korean', async () => {
  const actual = observed();
  Object.assign(actual.coverage, { observation_count: 3, measured_observation_count: 2, measured_lap_count: 7,
    replay_unknown_observation_count: 1, driving_unavailable_observation_count: 2,
    blocking_reason_counts: [{ code: 'SOURCE_ONLY', count: 1, reason: '독립 세션을 확인할 수 없습니다.' }] });
  Object.assign(actual.recent_drives[0], { identity_status: 'SOURCE_ONLY', replay_status: 'UNKNOWN', in_selected_period: false });
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  await screen.findByText('12.5 km');
  fireEvent.click(screen.getByRole('button', { name: '관측 근거' }));
  const dialog = await screen.findByRole('dialog', { name: '관측 근거와 기록' });
  expect(within(dialog).getByText('측정 가능한 랩')).toBeInTheDocument();
  expect(within(dialog).getByText('7랩')).toBeInTheDocument();
  expect(within(dialog).getByText(/측정 가능한 랩은 공식 유효 랩 수가 아닙니다/)).toBeInTheDocument();
  expect(within(dialog).getByText(/독립 세션 미확인 · 파일에서 측정한 부분 관측/)).toBeInTheDocument();
  expect(within(dialog).getByText(/리플레이 여부 미확인 · 관측한 측정값만 표시/)).toBeInTheDocument();
  expect(within(dialog).getByText('독립 세션을 확인할 수 없습니다. · 1개 관측')).toBeInTheDocument();
  expect(within(dialog).queryByText('SOURCE_ONLY')).not.toBeInTheDocument();
});

const refreshJob = (status, extra = {}) => ({ game: 'iracing', status, total: 12, processed: 0, updated: 0, skipped: 0, failed: 0, message: '', ...extra });

test('existing sources are refreshed only after an explicit action, polled once and followed by a report reload', async () => {
  jest.useFakeTimers();
  try {
    const actual = observed(); actual.coverage.outdated_source_count = 12;
    let posted = false;
    fetch.mockImplementation(async (url, options) => {
      if (url.includes('/api/driver-report?')) return response(actual);
      if (options.method === 'POST') { posted = true; return response(refreshJob('RUNNING')); }
      return response(refreshJob(posted ? 'COMPLETE' : 'IDLE', posted ? { processed: 12, updated: 10, skipped: 1, failed: 1, message: '기존 기록 확인을 마쳤습니다.' } : {}));
    });
    render(<DriverReport apiBase={API} />);
    await act(async () => { jest.advanceTimersByTime(0); });
    expect(fetch.mock.calls.some(([, options]) => options.method === 'POST')).toBe(false);
    expect(screen.getByText(/12개 원본의 측정 범위를 다시 확인/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '기존 기록 다시 확인' }));
    await act(async () => { jest.advanceTimersByTime(0); });
    expect(screen.getByText('기존 기록을 다시 확인하고 있어요')).toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(1500); });
    expect(screen.getByText('기존 기록 확인을 마쳤습니다.')).toBeInTheDocument();
    expect(screen.getByText('처리 12/12개 · 갱신 10개 · 건너뜀 1개 · 실패 1개')).toBeInTheDocument();
    expect(screen.getByText(/확인하지 못한 원본은 기존 관측을 유지합니다/)).toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);
    expect(fetch.mock.calls.filter(([url]) => url.includes('/api/driver-report?'))).toHaveLength(2);
  } finally { jest.useRealTimers(); }
});

test('an existing background check reconnects with GET and stops polling on game change', async () => {
  jest.useFakeTimers();
  try {
    const actual = observed(); actual.coverage.outdated_source_count = 12;
    fetch.mockImplementation(async url => response(url.includes('/api/driver-history/refresh?') ? refreshJob('RUNNING')
      : url.includes('game=lmu') ? observed('lmu') : actual));
    const view = render(<DriverReport apiBase={API} />);
    await act(async () => { jest.advanceTimersByTime(0); });
    expect(screen.getByText('기존 기록을 다시 확인하고 있어요')).toBeInTheDocument();
    expect(fetch.mock.calls.some(([, options]) => options.method === 'POST')).toBe(false);
    const signal = fetch.mock.calls.find(([url]) => url.includes('/api/driver-history/refresh?'))[1].signal;
    view.rerender(<DriverReport apiBase={API} game="lmu" />);
    await act(async () => { jest.advanceTimersByTime(0); });
    expect(signal.aborted).toBe(true);
    const calls = fetch.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(3000); });
    expect(fetch).toHaveBeenCalledTimes(calls);
    expect(screen.queryByText('기존 기록을 다시 확인하고 있어요')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'LMU 드라이버 리포트' })).toBeInTheDocument();
  } finally { jest.useRealTimers(); }
});

test('unknown replay cannot advertise growth and repeated dates do not imply elapsed time', async () => {
  const actual = observed();
  Object.assign(actual.pace, { status: 'OBSERVED', replay_status: 'UNKNOWN', pace_change_pct: -10, trend: 'IMPROVING',
    points: [{ date: '2026-10-26', median_lap_s: 91 }, { date: '2026-10-26', median_lap_s: 90 }] });
  actual.recent_drives[0].session_type = 'constructor';
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  await screen.findByText('1:31.125');
  expect(screen.getByLabelText('개인 페이스 —')).toBeInTheDocument();
  expect(screen.queryByText('비교 범위에서 페이스 개선')).not.toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.getByText(/Monza · 종류 미확인/)).toBeInTheDocument();
});

test('a failed background state read preserves the report and retry resumes with GET rather than another POST', async () => {
  jest.useFakeTimers();
  try {
    const actual = observed(); actual.coverage.outdated_source_count = 1;
    let statusReads = 0;
    fetch.mockImplementation(async url => {
      if (url.includes('/api/driver-report?')) return response(actual);
      statusReads += 1;
      if (statusReads === 2) throw new TypeError('Failed to fetch');
      return response(refreshJob(statusReads === 1 ? 'RUNNING' : 'FAILED', { failed: 1, message: '원본을 다시 선택해 분석해 주세요.' }));
    });
    render(<DriverReport apiBase={API} />);
    await act(async () => { jest.advanceTimersByTime(0); });
    await act(async () => { jest.advanceTimersByTime(1500); });
    expect(screen.getByText('12.5 km')).toBeInTheDocument();
    expect(screen.getByText('기록 확인 상태를 가져오지 못했습니다. 연결 상태를 확인해 주세요.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '진행 상태 다시 확인' }));
    await act(async () => { jest.advanceTimersByTime(0); });
    expect(screen.getByText('원본을 다시 선택해 분석해 주세요.')).toBeInTheDocument();
    expect(fetch.mock.calls.some(([, options]) => options.method === 'POST')).toBe(false);
    expect(fetch.mock.calls.filter(([url]) => url.includes('/api/driver-report?'))).toHaveLength(2);
  } finally { jest.useRealTimers(); }
});

test('all, 30 and 90-day views update headline statistics and scope with their own report queries', async () => {
  fetch.mockImplementation(async url => {
    const days=Number(new URL(url).searchParams.get('period_days'));
    const payload=observed('iracing',days);
    payload.career.distance_m.value=days===30?3000:days===90?9000:12500;
    payload.career.driving_time_s.value=days===30?600:days===90?1800:3860;
    return response(payload);
  });
  render(<DriverReport apiBase={API} />);
  await screen.findByText('전체 기간 · 관측 3개 · 측정 7랩');
  fireEvent.click(screen.getByRole('radio', { name: '30일', exact: true }));
  expect(await screen.findByText(/최근 30일 · 관측 3개 · 측정 7랩/)).toBeInTheDocument();
  expect(screen.getByLabelText('주행 거리 3 km · 확인된 일부')).toBeInTheDocument();
  expect(screen.getByLabelText('주행 시간 10분')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'통계 집계 기준'})).toHaveTextContent('최근 30일');
  fireEvent.click(screen.getByRole('button', { name: '관측 근거', exact: true }));
  const dialog = await screen.findByRole('dialog', { name: '관측 근거와 기록' });
  expect(within(dialog).getByText('최근 30일에 기록된 항목만 보여줍니다.')).toBeInTheDocument();
  fireEvent.click(within(dialog).getAllByRole('button', { name: '닫기', exact: true })[0]);
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('radio', { name: '90일', exact: true }));
  await screen.findByText(/최근 90일 · 관측 3개 · 측정 7랩/);
  expect(screen.getByLabelText('주행 거리 9 km · 확인된 일부')).toBeInTheDocument();
  expect(screen.getByLabelText('주행 시간 30분')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'통계 집계 기준'})).toHaveTextContent('최근 90일');
  fireEvent.click(screen.getByRole('radio', { name: '전체', exact: true }));
  await screen.findByText('전체 기간 · 관측 3개 · 측정 7랩');
  expect(screen.getByText('12.5 km')).toBeInTheDocument();
  expect(fetch.mock.calls.map(([url]) => new URL(url).searchParams.get('period_days'))).toEqual(['0', '30', '90', '0']);
});

test('background library progress reflects processed files while existing records and navigation remain usable', async () => {
  const pause = jest.fn();
  const actual = observed(); actual.coverage.outdated_source_count = 1;
  fetch.mockImplementation(async url => response(url.includes('/api/driver-history/refresh?') ? refreshJob('IDLE') : actual));
  useHistoryLibrary.mockReturnValue({ job: { status: 'RUNNING', total: 10, processed: 3, updated: 2, skipped: 1, failed: 0 },
    busy: false, error: '', waiting: false, pause, start: jest.fn(), retry: jest.fn() });
  render(<DriverReport apiBase={API} onNavigate={jest.fn()} />);
  await screen.findByText('12.5 km');
  const progress = screen.getByRole('progressbar', { name: '전체 기록 분석 진행' });
  expect(progress).toHaveAttribute('value', '3'); expect(progress).toHaveAttribute('max', '10');
  expect(screen.getByText('처리 3/10개 · 반영 2개 · 재사용/제외 1개 · 실패 0개')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'가져오기 상세'}));
  expect(screen.getByText(/다른 화면을 사용해도 계속되며/)).toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('dialog',{name:'과거 기록 가져오기'})).getAllByRole('button',{name:'닫기',exact:true})[0]);
  expect(screen.getByRole('button', { name: '기존 기록 다시 확인', exact: true })).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'도전 상세'}));
  expect(within(screen.getByRole('dialog',{name:'도전 목표 상세'})).getByRole('button', { name: '주행 분석에서 확인', exact: true })).toBeEnabled();
  fireEvent.click(within(screen.getByRole('dialog',{name:'도전 목표 상세'})).getAllByRole('button',{name:'닫기',exact:true})[0]);
  expect(screen.getByRole('radio', { name: '90일', exact: true })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '분석 일시정지', exact: true }));
  expect(pause).toHaveBeenCalledTimes(1);
});

test('library discovery is indeterminate, pausing cannot be repeated, and paused work can be resumed', async () => {
  const start = jest.fn();
  const state = { job: { status: 'SCANNING', total: 0, processed: 0, updated: 0, skipped: 0, failed: 0 },
    busy: false, error: '', waiting: false, start, pause: jest.fn(), retry: jest.fn() };
  useHistoryLibrary.mockReturnValue(state);
  const view = render(<DriverReport apiBase={API} />);
  await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' });
  expect(screen.getByRole('progressbar', { name: '전체 기록 분석 진행' })).not.toHaveAttribute('value');
  useHistoryLibrary.mockReturnValue({ ...state, job: { ...state.job, status: 'PAUSING' } });
  view.rerender(<DriverReport apiBase={API} />);
  expect(screen.getByRole('button', { name: '일시정지 중…', exact: true })).toBeDisabled();
  expect(screen.getByText('현재 파일 분석을 중단하고 자원을 정리하고 있습니다.')).toBeInTheDocument();
  useHistoryLibrary.mockReturnValue({ ...state, job: { ...state.job, status: 'PAUSED' } });
  view.rerender(<DriverReport apiBase={API} />);
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '분석 이어하기', exact: true }));
  expect(start).toHaveBeenCalledTimes(1);
});

test('observation-only pace changes are visible without claiming matched conditions or skill growth', async () => {
  const actual = observed();
  Object.assign(actual.pace, { comparison_status: 'OBSERVATION_ONLY', observation_change_pct: -4.2, replay_status: 'UNKNOWN' });
  fetch.mockResolvedValue(response(actual));
  render(<DriverReport apiBase={API} />);
  expect(await screen.findByLabelText('관측 페이스 변화 −4.2%')).toBeInTheDocument();
  expect(screen.getByText('조건 차이가 포함된 기록 변화')).toBeInTheDocument();
  expect(screen.getByText('조건 차이가 포함된 관측')).toBeInTheDocument();
  expect(screen.queryByText('비교 범위에서 페이스 개선')).not.toBeInTheDocument();
  expect(screen.queryByText('동일 조건의 랩타임 변화')).not.toBeInTheDocument();
});

test('background progress coalesces report refreshes instead of cancelling a slow report read', async () => {
  const first = deferred();
  const newer = observed(); newer.career.distance_m.value = 13000;
  fetch.mockReturnValueOnce(first.promise).mockResolvedValue(response(newer));
  render(<DriverReport apiBase={API} />);
  const signal = fetch.mock.calls[0][1].signal;
  const onProgress = useHistoryLibrary.mock.calls[0][2];
  act(() => { onProgress(); onProgress(); onProgress(); });
  expect(signal.aborted).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { first.resolve(response(observed())); });
  expect(await screen.findByText('13 km')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2);
});

test.each([
  ['DRIVING', '주행이 끝난 뒤 주행 분석까지 마치면 자동으로 이어집니다.'],
  ['ANALYZING', '선택한 주행 분석이 끝나면 자동으로 이어집니다.'],
  ['COOLDOWN', '앱이 잠시 유휴 상태가 되면 자동으로 이어집니다.'],
  ['RESOURCE_BUSY', 'CPU와 메모리 여유가 확보되면 자동으로 이어집니다.'],
])('automatic %s waiting explains resume conditions and still permits manual pause', async (reason, explanation) => {
  const pause = jest.fn();
  fetch.mockResolvedValue(response(observed()));
  useHistoryLibrary.mockReturnValue({ job: { status: 'WAITING', wait_reason: reason, message: '파일 분석을 자동으로 기다립니다.',
    total: 10, processed: 4, updated: 4, skipped: 0, failed: 0 }, busy: false, error: '', waiting: false, start: jest.fn(), pause, retry: jest.fn() });
  render(<DriverReport apiBase={API} />);
  await screen.findByText('12.5 km');
  expect(screen.getByText('전체 기록 분석 자동 대기 중')).toBeInTheDocument();
  expect(screen.getByText(explanation)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'가져오기 상세'}));
  expect(screen.getByText('파일 분석을 자동으로 기다립니다.')).toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('dialog',{name:'과거 기록 가져오기'})).getAllByRole('button',{name:'닫기',exact:true})[0]);
  expect(screen.getByRole('progressbar', { name: '전체 기록 분석 진행' })).toHaveAttribute('value', '4');
  fireEvent.click(screen.getByRole('button', { name: '분석 일시정지', exact: true }));
  expect(pause).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button', { name: '분석 이어하기', exact: true })).not.toBeInTheDocument();
});
