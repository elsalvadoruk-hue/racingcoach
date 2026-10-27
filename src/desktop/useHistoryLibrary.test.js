import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import useHistoryLibrary, { HISTORY_LIBRARY_POLL_MS, HISTORY_LIBRARY_WAIT_MS } from './useHistoryLibrary';

const API = 'http://localhost:24891';
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const job = (status, extra = {}) => ({ game: 'iracing', job_id: 'job-1', status, total: 3, processed: 0, updated: 0,
  skipped: 0, failed: 0, revision: 0, message: '', busy_game: null, ...extra });
const deferred = () => { let resolve; return { promise: new Promise(done => { resolve = done; }), resolve: value => resolve(value) }; };

function Harness({ game = 'iracing', onRevision = jest.fn() }) {
  const library = useHistoryLibrary(API, game, onRevision);
  return <><output aria-label="가져오기 상태">{library.job?.status || 'NONE'}</output>
    <p aria-label="대기 사유">{library.job?.wait_reason}</p>
    <p>{library.error}</p>{library.waiting ? <p>다른 게임 대기</p> : null}
    <button onClick={library.start}>시작</button><button onClick={library.pause}>정지</button><button onClick={library.retry}>재조회</button></>;
}

let previousFetch;
beforeEach(() => { previousFetch = global.fetch; global.fetch = jest.fn(); jest.useFakeTimers(); });
afterEach(() => { global.fetch = previousFetch; jest.useRealTimers(); });
const advance = async milliseconds => act(async () => { jest.advanceTimersByTime(milliseconds); });

test('initial idle starts once, reports progress revisions, and stops polling after completion', async () => {
  const onRevision = jest.fn();
  fetch.mockResolvedValueOnce(response(job('IDLE'))).mockResolvedValueOnce(response(job('SCANNING')))
    .mockResolvedValueOnce(response(job('RUNNING', { revision: 1, processed: 1, updated: 1 })))
    .mockResolvedValue(response(job('COMPLETE', { revision: 2, processed: 3, updated: 2, skipped: 1 })));
  render(<Harness onRevision={onRevision} />);
  await advance(0);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('SCANNING');
  expect(fetch.mock.calls.map(([, options]) => options.method || 'GET')).toEqual(['GET', 'POST']);
  await advance(HISTORY_LIBRARY_POLL_MS);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('RUNNING');
  expect(onRevision).toHaveBeenCalledTimes(1);
  await advance(HISTORY_LIBRARY_POLL_MS);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('COMPLETE');
  expect(onRevision).toHaveBeenCalledTimes(2);
  const calls = fetch.mock.calls.length;
  await advance(HISTORY_LIBRARY_POLL_MS * 3);
  expect(fetch).toHaveBeenCalledTimes(calls);
});

test.each(['COMPLETE', 'PAUSED'])('%s jobs reconnect without an automatic restart', async status => {
  fetch.mockResolvedValue(response(job(status)));
  render(<Harness />);
  await advance(0);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent(status);
  await advance(HISTORY_LIBRARY_POLL_MS * 2);
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '시작' }));
  await advance(0);
  expect(fetch.mock.calls[1][1].method).toBe('POST');
});

test('a running job reconnects with GET, unchanged revisions do not refresh the report, and pause waits for the server', async () => {
  const onRevision = jest.fn();
  fetch.mockResolvedValueOnce(response(job('RUNNING', { revision: 4 })))
    .mockResolvedValueOnce(response(job('RUNNING', { revision: 4 })))
    .mockResolvedValueOnce(response(job('PAUSING', { revision: 4 })))
    .mockResolvedValueOnce(response(job('PAUSED', { revision: 5, processed: 2, updated: 2 })));
  render(<Harness onRevision={onRevision} />);
  await advance(0); await advance(HISTORY_LIBRARY_POLL_MS);
  expect(onRevision).not.toHaveBeenCalled();
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '정지' }));
  await advance(0);
  expect(fetch.mock.calls[2][0]).toBe(`${API}/api/driver-history/library/pause?game=iracing`);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('PAUSING');
  await advance(HISTORY_LIBRARY_POLL_MS);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('PAUSED');
  expect(onRevision).toHaveBeenCalledTimes(1);
});

test('busy other-game status waits with reads and starts only after that game releases the worker', async () => {
  fetch.mockResolvedValueOnce(response(job('IDLE', { busy_game: 'lmu' })))
    .mockResolvedValueOnce(response(job('IDLE', { busy_game: 'lmu' })))
    .mockResolvedValueOnce(response(job('IDLE'))).mockResolvedValue(response(job('RUNNING')));
  render(<Harness />);
  await advance(0); await advance(HISTORY_LIBRARY_WAIT_MS);
  expect(screen.getByText('다른 게임 대기')).toBeInTheDocument();
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
  await advance(HISTORY_LIBRARY_WAIT_MS);
  expect(screen.queryByText('다른 게임 대기')).not.toBeInTheDocument();
  expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);
});

test('a start conflict polls state rather than sending repeated start requests while the worker is occupied', async () => {
  fetch.mockResolvedValueOnce(response(job('IDLE'))).mockResolvedValueOnce(response({ detail: '다른 게임 분석 중' }, 409))
    .mockResolvedValueOnce(response(job('IDLE', { busy_game: 'lmu' }))).mockResolvedValueOnce(response(job('IDLE')))
    .mockResolvedValue(response(job('SCANNING')));
  render(<Harness />);
  await advance(0);
  expect(screen.getByText('다른 게임 대기')).toBeInTheDocument();
  await advance(HISTORY_LIBRARY_WAIT_MS);
  expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);
  await advance(HISTORY_LIBRARY_WAIT_MS);
  expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(2);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('SCANNING');
});

test('status read failure retains the known job and retry reads status before performing any mutation', async () => {
  fetch.mockResolvedValueOnce(response(job('RUNNING', { revision: 1 }))).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockResolvedValue(response(job('COMPLETE', { revision: 2, processed: 3 })));
  render(<Harness />);
  await advance(0); await advance(HISTORY_LIBRARY_POLL_MS);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('RUNNING');
  expect(screen.getByText(/기록 가져오기 상태를 확인하지 못했습니다/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '재조회' }));
  await advance(0);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('COMPLETE');
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
});

test('game changes cancel old reads and ignore late state without pausing the server', async () => {
  const old = deferred();
  fetch.mockImplementation(url => url.includes('game=lmu') ? Promise.resolve(response(job('PAUSED', { game: 'lmu' }))) : old.promise);
  const view = render(<Harness />);
  const signal = fetch.mock.calls[0][1].signal;
  view.rerender(<Harness game="lmu" />);
  await advance(0);
  expect(signal.aborted).toBe(true);
  await act(async () => { old.resolve(response(job('IDLE'))); });
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('PAUSED');
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
  const activeSignal = fetch.mock.calls[1][1].signal;
  view.unmount();
  expect(activeSignal.aborted).toBe(true);
  const calls = fetch.mock.calls.length;
  await advance(HISTORY_LIBRARY_POLL_MS * 3);
  expect(fetch).toHaveBeenCalledTimes(calls);
});

test('unmounting a running job cancels polling without sending pause and callback identity does not restart the effect', async () => {
  fetch.mockResolvedValue(response(job('RUNNING')));
  const view = render(<Harness onRevision={jest.fn()} />);
  await advance(0);
  view.rerender(<Harness onRevision={jest.fn()} />);
  expect(fetch).toHaveBeenCalledTimes(1);
  view.unmount(); await advance(HISTORY_LIBRARY_POLL_MS * 3);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
});

test('a response for another game never starts an import or replaces the current job', async () => {
  fetch.mockResolvedValue(response(job('IDLE', { game: 'lmu' })));
  render(<Harness />);
  await advance(0);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('NONE');
  expect(screen.getByText('과거 기록 가져오기 상태를 읽지 못했습니다. 다시 확인해 주세요.')).toBeInTheDocument();
  await advance(HISTORY_LIBRARY_WAIT_MS * 2);
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('automatic waiting keeps polling without another start and resumes only when the server changes phase', async () => {
  const onRevision = jest.fn();
  fetch.mockResolvedValueOnce(response(job('WAITING', { wait_reason: 'DRIVING' })))
    .mockResolvedValueOnce(response(job('WAITING', { wait_reason: 'COOLDOWN' })))
    .mockResolvedValueOnce(response(job('RUNNING', { revision: 1, processed: 1, updated: 1 })))
    .mockResolvedValue(response(job('COMPLETE', { revision: 2, processed: 3, updated: 3 })));
  render(<Harness onRevision={onRevision} />);
  await advance(0);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('WAITING');
  expect(screen.getByLabelText('대기 사유')).toHaveTextContent('DRIVING');
  await advance(HISTORY_LIBRARY_POLL_MS);
  expect(screen.getByLabelText('대기 사유')).toHaveTextContent('COOLDOWN');
  expect(onRevision).not.toHaveBeenCalled();
  await advance(HISTORY_LIBRARY_POLL_MS);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('RUNNING');
  expect(onRevision).toHaveBeenCalledTimes(1);
  await advance(HISTORY_LIBRARY_POLL_MS);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('COMPLETE');
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
});

test('manual pause from automatic waiting stays paused until an explicit resume', async () => {
  fetch.mockResolvedValueOnce(response(job('WAITING', { wait_reason: 'RESOURCE_BUSY' })))
    .mockResolvedValueOnce(response(job('PAUSED'))).mockResolvedValue(response(job('RUNNING')));
  render(<Harness />);
  await advance(0);
  fireEvent.click(screen.getByRole('button', { name: '정지' }));
  await advance(0);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('PAUSED');
  await advance(HISTORY_LIBRARY_POLL_MS * 4);
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: '시작' }));
  await advance(0);
  expect(screen.getByLabelText('가져오기 상태')).toHaveTextContent('RUNNING');
  expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(2);
});
