import { act, renderHook } from '@testing-library/react';
import useRadioDiagnostics from './useRadioDiagnostics';

beforeEach(() => {
  jest.useFakeTimers();
  global.fetch = jest.fn().mockResolvedValue({ ok: true });
});
afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });

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
