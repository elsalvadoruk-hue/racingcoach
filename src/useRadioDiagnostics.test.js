import { act, renderHook } from '@testing-library/react';
import useRadioDiagnostics from './useRadioDiagnostics';
import packageInfo from '../package.json';

beforeEach(() => {
  jest.useFakeTimers();
  global.fetch = jest.fn().mockResolvedValue({ ok: true });
});
afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });

test('queued feedback retains the loaded frontend and originating backend versions', async () => {
  const { result } = renderHook(() => useRadioDiagnostics('http://synthetic'));
  act(() => result.current({ event_id: 'old-frame', backend_version: '2.1.2' }, 'completed'));
  await act(async () => jest.advanceTimersByTime(1000));
  const [event] = JSON.parse(fetch.mock.calls[0][1].body).events;
  expect(event.frontend_version).toBe(packageInfo.version);
  expect(event.backend_version).toBe('2.1.2');
});

test('setting playback starts notify the HUD immediately while ordinary logs stay batched', async () => {
  const { result } = renderHook(() => useRadioDiagnostics('http://synthetic'));
  act(() => result.current({ event_id: 'lap', key: 'lap_time' }, 'started'));
  expect(fetch).not.toHaveBeenCalled();
  await act(async () => result.current({ event_id: 'setting', key: 'control_bias_forward' }, 'started'));
  expect(fetch).toHaveBeenCalledTimes(1);
  const events = JSON.parse(fetch.mock.calls[0][1].body).events;
  expect(events.map(e => e.event_id)).toEqual(['lap', 'setting']);
  act(() => result.current({ event_id: 'setting', key: 'control_bias_forward' }, 'completed'));
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => jest.advanceTimersByTime(1000));
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('a failed start notification retries on the normal timer without a busy loop', async () => {
  fetch.mockRejectedValueOnce(new Error('offline'));
  const { result } = renderHook(() => useRadioDiagnostics('http://synthetic'));
  await act(async () => result.current({ event_id: 'setting', key: 'control_tc_more' }, 'started'));
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => jest.advanceTimersByTime(1000));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetch.mock.calls[1][1].body).events[0].event_id).toBe('setting');
});

test('a setting that starts during another request is sent when that request finishes', async () => {
  let finish;
  fetch.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const { result } = renderHook(() => useRadioDiagnostics('http://synthetic'));
  act(() => result.current({ event_id: 'lap', key: 'lap_time' }, 'started'));
  act(() => jest.advanceTimersByTime(1000));
  act(() => result.current({ event_id: 'setting', key: 'control_abs_more' }, 'started'));
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => finish({ ok: true }));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetch.mock.calls[1][1].body).events[0].event_id).toBe('setting');
});

test('shadow diagnostics stay out of actual playback counters and retry on the timer', async () => {
  fetch.mockRejectedValueOnce(new Error('offline'));
  const { result } = renderHook(() => useRadioDiagnostics('http://synthetic'));
  await act(async () => result.current.observeBanter({ candidate_id: 'shadow', run_id: 'run', muted: false }));
  expect(fetch).toHaveBeenCalledTimes(1);
  const body = JSON.parse(fetch.mock.calls[0][1].body);
  expect(body.events).toEqual([]);
  expect(body.banter[0].candidate_id).toBe('shadow');
  await act(async () => jest.advanceTimersByTime(1000));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(body);
});

test('shadow observations arriving during a request flush after it finishes', async () => {
  let finish;
  fetch.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const { result } = renderHook(() => useRadioDiagnostics('http://synthetic'));
  act(() => result.current.observeBanter({ candidate_id: 'first' }));
  act(() => result.current.observeBanter({ candidate_id: 'second' }));
  await act(async () => finish({ ok: true }));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetch.mock.calls[1][1].body).banter).toEqual([{ candidate_id: 'second' }]);
});

test('oval observations retry independently without actual playback records', async () => {
  fetch.mockRejectedValueOnce(new Error('offline'));
  const { result } = renderHook(() => useRadioDiagnostics('http://synthetic'));
  await act(async () => result.current.observeOval({ candidate_id: 'oval', run_id: 'run', muted: false }));
  const body = JSON.parse(fetch.mock.calls[0][1].body);
  expect(body.events).toEqual([]);
  expect(body.oval).toEqual([{ candidate_id: 'oval', run_id: 'run', muted: false }]);
  await act(async () => jest.advanceTimersByTime(1000));
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(body);
});
