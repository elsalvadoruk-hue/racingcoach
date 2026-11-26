import { act, renderHook, waitFor } from '@testing-library/react';
import useLocalSpeech from './useLocalSpeech';

const originalFetch = global.fetch;
beforeEach(() => {
  localStorage.clear();
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ ok: true }) }));
});
afterEach(() => { global.fetch = originalFetch; jest.useRealTimers(); });

test('idle opens no model, microphone or network request', () => {
  const view = renderHook(() => useLocalSpeech('http://test', 'iracing', false));
  expect(view.result.current.mode).toBe('local');
  expect(view.result.current.model).toBe('sensevoice');
  expect(global.fetch).not.toHaveBeenCalled();
  view.unmount();
  expect(global.fetch).not.toHaveBeenCalled();
});

test('activation is explicit and shutdown releases the same owner', async () => {
  const view = renderHook(() => useLocalSpeech('http://test', 'iracing', false));
  await act(async () => view.result.current.activate());
  expect(view.result.current.active).toBe(true);
  const owner = view.result.current.owner;
  await act(async () => view.result.current.stop());
  expect(view.result.current.active).toBe(false);
  const calls = global.fetch.mock.calls;
  expect(calls[0][0]).toMatch(/activate$/);
  expect(JSON.parse(calls[0][1].body)).toEqual({ owner, model: 'sensevoice' });
  expect(calls[1][0]).toMatch(/deactivate$/);
  expect(JSON.parse(calls[1][1].body).owner).toBe(owner);
});

test('chosen model persists when the app is reopened', () => {
  const first = renderHook(() => useLocalSpeech('http://test', 'iracing', false));
  act(() => first.result.current.setModel('base'));
  first.unmount();
  const reopened = renderHook(() => useLocalSpeech('http://test', 'iracing', false));
  expect(reopened.result.current.model).toBe('base');
  expect(global.fetch).not.toHaveBeenCalled();
});

test('game changes and loss of audio ownership unload a ready model', async () => {
  const view = renderHook(({ game, standby }) => useLocalSpeech('http://test', game, standby),
    { initialProps: { game: 'iracing', standby: false } });
  await act(async () => view.result.current.activate());
  view.rerender({ game: 'lmu', standby: false });
  await waitFor(() => expect(view.result.current.active).toBe(false));
  await act(async () => view.result.current.activate());
  view.rerender({ game: 'lmu', standby: true });
  await waitFor(() => expect(view.result.current.active).toBe(false));
});

test('closing during model loading never leaves a later ready model alive', async () => {
  let ready;
  global.fetch.mockImplementationOnce(() => new Promise(resolve => { ready = resolve; }));
  const view = renderHook(() => useLocalSpeech('http://test', 'iracing', false));
  let pending;
  act(() => { pending = view.result.current.activate().catch(e => e); });
  view.unmount();
  ready({ ok: true, json: async () => ({ ok: true }) });
  const result = await pending;
  expect(result.message).toContain('취소');
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/deactivate'))).toHaveLength(2);
});

test('active model refreshes its lease and a lost lease clears active state', async () => {
  jest.useFakeTimers();
  const onError = jest.fn();
  const view = renderHook(() => useLocalSpeech('http://test', 'iracing', false, onError));
  await act(async () => view.result.current.activate());
  global.fetch.mockImplementation(async url => ({ ok: !url.endsWith('/heartbeat'), json: async () => ({ detail: '다시 준비' }) }));
  await act(async () => jest.advanceTimersByTime(15000));
  expect(onError).toHaveBeenCalledWith('다시 준비');
  expect(view.result.current.active).toBe(false);
});
