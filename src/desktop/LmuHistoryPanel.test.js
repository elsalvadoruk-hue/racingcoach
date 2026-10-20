import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import LmuHistoryPanel from './LmuHistoryPanel';
import { requestJson } from '../analysis/analysisClient';
import { rememberSession } from './sessionData';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./LmuPracticePanel', () => () => <div>기존 반복 연습</div>);
jest.mock('./sessionData', () => ({
  data: {}, lapTime: value => Number.isFinite(value) ? `${value.toFixed(3)} s` : '—', rememberSession: jest.fn(),
}));

const FILE_A = 'a'.repeat(24), FILE_B = 'b'.repeat(24);
const session = { provider: 'LMU', filename: FILE_A, file_name: 'race.duckdb',
  reference_filename: FILE_B, defaultTarget: 1, defaultReference: 7 };
const record = { record_id: 'history1', created_at: '2026-10-20T01:00:00Z', updated_at: '2026-10-20T01:00:00Z',
  selection: { filename: FILE_A, target_lap: 1, reference_filename: FILE_B, reference_lap: 7 }, note: '제동 확인',
  summary: { file_name: 'race.duckdb', reference_file_name: 'qualifying.duckdb', car_name: 'GT3', track_name: 'Monza',
    session_type: 'Race', target_time_s: 122.1597, reference_time_s: 115.6653, lap_count: 2, measured_lap_count: 1, valid: null } };
const listing = { records: [record], total: 1, tracks: ['Monza', 'Spa'], cars: ['GT3'], session_types: ['Race'] };

beforeEach(() => { jest.clearAllMocks(); requestJson.mockResolvedValue(listing); });

test('LMU history displays measured records and practice with unknown validity', async () => {
  render(<LmuHistoryPanel session={session} onOpenSession={jest.fn()} />);
  expect(await screen.findByText('GT3 · Monza')).toBeInTheDocument();
  expect(screen.getByText('1랩 · 122.160 s')).toBeInTheDocument();
  expect(screen.getByText('기준 7랩 · 115.665 s')).toBeInTheDocument();
  expect(screen.getByText('유효성 확인 필요')).toBeInTheDocument();
  expect(screen.getByText('기존 반복 연습')).toBeInTheDocument();
  expect(requestJson.mock.calls.every(([path]) => path.startsWith('/api/lmu/'))).toBe(true);
  expect(requestJson.mock.calls.every(([, options]) => !options.method)).toBe(true);
});

test('save sends current target and cross-file reference identity but no client-computed metrics', async () => {
  render(<LmuHistoryPanel session={session} onOpenSession={jest.fn()} />);
  await screen.findByText('GT3 · Monza');
  fireEvent.change(screen.getByLabelText('주행 메모'), { target: { value: '다음 주행 확인' } });
  fireEvent.click(screen.getByRole('button', { name: '현재 분석 기록 저장' }));
  await screen.findByText(/현재 분석을 LMU 기록에 저장했습니다/);
  const [, options] = requestJson.mock.calls.find(([path, args]) => path === '/api/lmu/history' && args.method === 'POST');
  expect(JSON.parse(options.body)).toEqual({ filename: FILE_A, target_lap: 1, reference_filename: FILE_B,
    reference_lap: 7, note: '다음 주행 확인' });
  await waitFor(() => expect(requestJson.mock.calls.filter(([path, args]) => path === '/api/lmu/history' && !args.method)).toHaveLength(2));
});

test('open applies the fresh server session and remembers LMU source selection', async () => {
  const onOpenSession = jest.fn();
  requestJson.mockImplementation(async path => path.endsWith('/open') ? { session, record } : listing);
  render(<LmuHistoryPanel session={session} onOpenSession={onOpenSession} />);
  fireEvent.click(await screen.findByRole('button', { name: '분석 열기 · race.duckdb · 1랩' }));
  await waitFor(() => expect(onOpenSession).toHaveBeenCalledWith(session));
  expect(rememberSession).toHaveBeenCalledWith(session, 1, 7, []);
  expect(requestJson.mock.calls.find(([path]) => path.endsWith('/open'))[0]).toBe('/api/lmu/history/history1/open');
});

test('filter search includes track/car/session and aborts previous request', async () => {
  render(<LmuHistoryPanel session={session} />);
  await screen.findByText('GT3 · Monza');
  const firstSignal = requestJson.mock.calls[0][1].signal;
  fireEvent.change(screen.getByLabelText('기록 검색'), { target: { value: '제동' } });
  await waitFor(() => expect(requestJson.mock.calls.some(([path]) => path.includes('q=%EC%A0%9C%EB%8F%99'))).toBe(true));
  expect(firstSignal.aborted).toBe(true);
  fireEvent.change(screen.getByLabelText('트랙'), { target: { value: 'Monza' } });
  fireEvent.change(screen.getByLabelText('차량'), { target: { value: 'GT3' } });
  fireEvent.change(screen.getByLabelText('세션'), { target: { value: 'Race' } });
  await waitFor(() => expect(requestJson.mock.calls.some(([path]) => path.includes('track=Monza&car=GT3&session_type=Race'))).toBe(true));
});

test('missing telemetry on reopen reports error and leaves active selection intact', async () => {
  const onOpenSession = jest.fn();
  requestJson.mockImplementation(async path => {
    if (path.endsWith('/open')) throw new Error('파일이 이동되거나 삭제됐습니다.');
    return listing;
  });
  render(<LmuHistoryPanel session={session} onOpenSession={onOpenSession} />);
  fireEvent.click(await screen.findByRole('button', { name: '분석 열기 · race.duckdb · 1랩' }));
  expect(await screen.findByText('파일이 이동되거나 삭제됐습니다.')).toBeInTheDocument();
  expect(onOpenSession).not.toHaveBeenCalled(); expect(rememberSession).not.toHaveBeenCalled();
});

test('game change unmount aborts reopen and cannot adopt an old LMU session', async () => {
  let complete;
  const onOpenSession = jest.fn();
  requestJson.mockImplementation(path => path.endsWith('/open') ? new Promise(resolve => { complete = resolve; }) : Promise.resolve(listing));
  const view = render(<LmuHistoryPanel session={session} onOpenSession={onOpenSession} />);
  fireEvent.click(await screen.findByRole('button', { name: '분석 열기 · race.duckdb · 1랩' }));
  const signal = requestJson.mock.calls.find(([path]) => path.endsWith('/open'))[1].signal;
  view.unmount(); expect(signal.aborted).toBe(true);
  await act(async () => { complete({ session }); });
  expect(onOpenSession).not.toHaveBeenCalled(); expect(rememberSession).not.toHaveBeenCalled();
});

test('no LMU analysis disables save and empty history stays readable', async () => {
  requestJson.mockResolvedValue({ records: [], total: 0 });
  render(<LmuHistoryPanel session={{}} />);
  expect(screen.getByRole('button', { name: '현재 분석 기록 저장' })).toBeDisabled();
  expect(await screen.findByText('저장된 주행 분석이 없습니다.')).toBeInTheDocument();
  expect(screen.getByText('기존 반복 연습')).toBeInTheDocument();
});
