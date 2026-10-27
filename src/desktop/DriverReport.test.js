import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import DriverReport from './DriverReport';

const API = 'http://localhost:24891';
const unavailable = reason => ({ value: null, status: 'UNAVAILABLE', reason });
const metric = (value, status = 'OBSERVED') => ({ value, status, reason: '확인한 운전 구간만 집계했습니다.' });
function report(game = 'iracing', period = 30) {
  return {
    schema_version: 1, game, period_days: period, state: 'EMPTY',
    coverage: { source_count: 0, native_session_count: 0, source_only_count: 0, undated_count: 0, period_session_count: 0 },
    career: { distance_m: unavailable('운전 구간을 기다립니다.'), driving_time_s: unavailable('운전 구간을 기다립니다.'), wins: unavailable('최종 클래스 결과가 없습니다.'), podiums: unavailable('최종 클래스 결과가 없습니다.'), average_position: unavailable('최종 클래스 결과가 없습니다.') },
    overview: { title: '나의 주행 이야기를 시작해요', body: '주행 파일을 분석하면 확인된 기록부터 차곡차곡 연결합니다.', scope: '아직 연결된 주행 기록이 없습니다.' },
    pace: { status: 'UNAVAILABLE', median_lap_s: null, mad_pct: null, lap_count: 0, points: [], trend: 'UNAVAILABLE', reason: '측정 가능한 랩과 비교 조건을 먼저 확인합니다.' },
    style: { status: 'UNAVAILABLE', items: [], reason: '운전 성향을 설명할 근거가 아직 충분하지 않습니다.' },
    challenge: { title: '기록 한 번 살펴보기', body: '이미 있는 주행 파일을 분석하고 확인된 내용을 살펴보세요.', action: 'analysis' },
    discoveries: [], recent_drives: [], limitations: ['분석한 원본의 관측이며 게임의 평생 기록이 아닙니다.'],
  };
}
function observed(game = 'iracing', period = 30) {
  return {
    ...report(game, period), state: 'OBSERVED',
    coverage: { source_count: 3, native_session_count: 2, source_only_count: 1, undated_count: 1, period_session_count: 2 },
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
beforeEach(() => { originalFetch = global.fetch; global.fetch = jest.fn().mockResolvedValue(response(report())); });
afterEach(() => { global.fetch = originalFetch; });

test('empty history keeps unknown career metrics unavailable and offers authorized entry points', async () => {
  const onNavigate = jest.fn(), onChooseSource = jest.fn();
  render(<DriverReport game="iracing" apiBase={API} onNavigate={onNavigate} onChooseSource={onChooseSource} />);
  expect(await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' })).toBeInTheDocument();
  const career = screen.getByRole('region', { name: '앱에 기록된 전체 누적 통계' });
  expect(within(career).getAllByText('—')).toHaveLength(5);
  const summary = screen.getByRole('region', { name: '최근 성장 요약' });
  expect(within(summary).getAllByText('—')).toHaveLength(4);
  expect(within(summary).getByText('개인 페이스')).toBeInTheDocument();
  expect(within(summary).getByText('랩 간 편차 비율')).toBeInTheDocument();
  expect(within(summary).getByText('유효 랩 비율')).toBeInTheDocument();
  expect(within(summary).getByText('시간당 인시던트 점수')).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.queryByText('0 km')).not.toBeInTheDocument();
  expect(screen.getByText('아직은 관찰 중이에요')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '주행 선택' }));
  fireEvent.click(screen.getByRole('button', { name: '주행 분석 열기' }));
  expect(onChooseSource).toHaveBeenCalledTimes(1);
  expect(onNavigate).toHaveBeenCalledWith('summary');
  expect(fetch).toHaveBeenCalledWith(`${API}/api/driver-report?game=iracing&period_days=30`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
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
  expect(screen.getByText(/날짜 미확인 1개는 기간 비교에서 제외/)).toBeInTheDocument();
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

test('period changes abort stale reads and keep the accumulated career scope independent', async () => {
  const old = deferred();
  fetch.mockImplementation(url => url.endsWith('period_days=90') ? Promise.resolve(response(observed('iracing', 90))) : old.promise);
  render(<DriverReport apiBase={API} />);
  const oldSignal = fetch.mock.calls[0][1].signal;
  fireEvent.click(screen.getByRole('radio', { name: '90일' }));
  expect(oldSignal.aborted).toBe(true);
  expect(await screen.findByText('12.5 km')).toBeInTheDocument();
  expect(screen.getByText(/최근 90일 · 확인된 2세션/)).toBeInTheDocument();
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
  expect(within(dialog).getByText('전체 누적 통계')).toBeInTheDocument();
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
