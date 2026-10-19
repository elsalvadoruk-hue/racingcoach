import { act, renderHook, waitFor } from '@testing-library/react';
import useSpotter from './useSpotter';

const originalFetch = global.fetch, originalSocket = global.WebSocket;
let sockets, audio, manifest;
beforeEach(() => {
  localStorage.clear();
  sockets = [];
  manifest = { voice: 'ko-KR-Chirp3-HD-Fenrir', clips: Object.fromEntries(['left', 'right', 'clear_left', 'unavailable', 'test'].map((key) => [key, { audio_base64: key, text: key }])) };
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => manifest }));
  global.WebSocket = jest.fn(() => { const socket = { close: jest.fn() }; sockets.push(socket); return socket; });
  audio = { play: jest.fn(async (_encoded, _mime, options) => { options?.onStatus?.('started'); return true; }), setPriorityGate: jest.fn(), cancelPriority: jest.fn(), muted: false, volume: 1 };
});
afterEach(() => { global.fetch = originalFetch; global.WebSocket = originalSocket; jest.useRealTimers(); });
const frame = (raw, key = null, extra = {}) => ({ type: 'spotter', active: true, occupied: raw !== 1,
  state: raw, raw_state: raw, run_id: 'run', serial: 1,
  events: key ? [{ serial: 1, key, state: raw, run_id: 'run', expires_in_ms: 500 }] : [], ...extra });
const send = (socket, message) => act(() => socket.onmessage({ data: JSON.stringify(message) }));
const enable = async (result) => act(async () => result.current.toggle());

test('only explicit enable prepares clips and starts a dedicated websocket without a microphone', async () => {
  const { result } = renderHook(() => useSpotter('http://test', audio));
  expect(global.fetch).not.toHaveBeenCalled(); expect(sockets).toHaveLength(0);
  await enable(result);
  expect(global.fetch).toHaveBeenCalledWith('http://test/api/live-coach/spotter/prepare', expect.objectContaining({ method: 'POST',
    body: JSON.stringify({ repeat_seconds: 3, clear_delay: .15, traffic_enabled: true, pit_box_enabled: false }) }));
  expect(global.WebSocket).toHaveBeenCalledWith('ws://test/api/live-coach/spotter/events');
  expect(result.current.voice).toBe('Fenrir');
  send(sockets[0], frame(2, 'left'));
  expect(audio.setPriorityGate).toHaveBeenCalledWith(true);
  expect(audio.play).toHaveBeenCalledWith('left', 'audio/mpeg', expect.objectContaining({ priority: 3 }));
  const valid = audio.play.mock.calls[0][2].valid;
  expect(valid()).toBe(true);
  send(sockets[0], frame(3, 'right', { serial: 2, events: [{ serial: 2, key: 'right', state: 3, run_id: 'run', expires_in_ms: 500 }] }));
  expect(valid()).toBe(false);
  expect(result.current.view.last_text).toBe('right');
});

test('stale, mismatched and duplicate events cannot replay old directions', async () => {
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  send(sockets[0], frame(2, 'left'));
  send(sockets[0], frame(2, 'left'));
  expect(audio.play).toHaveBeenCalledTimes(1);
  send(sockets[0], frame(3, null, { serial: 2, events: [{ serial: 2, key: 'left', state: 2, run_id: 'run', expires_in_ms: 500 }] }));
  send(sockets[0], frame(2, null, { serial: 3, events: [{ serial: 3, key: 'left', state: 2, run_id: 'run', expires_in_ms: 0 }] }));
  expect(audio.play).toHaveBeenCalledTimes(1);
});

test('mute suppresses audio but preserves visual spotting and normal audio priority gate', async () => {
  audio.muted = true;
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  send(sockets[0], frame(2, 'left'));
  expect(result.current.view.last_text).toBe('left');
  expect(audio.play).not.toHaveBeenCalled();
  expect(audio.setPriorityGate).toHaveBeenCalledWith(true);
});

test('loss announces once, never clears, and reconnect starts with the current direction', async () => {
  jest.useFakeTimers();
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  send(sockets[0], frame(2, 'left'));
  act(() => sockets[0].onclose());
  expect(result.current.view.active).toBe(false);
  expect(audio.play.mock.calls.map((call) => call[0])).toEqual(['left', 'unavailable']);
  act(() => jest.advanceTimersByTime(1000));
  expect(sockets).toHaveLength(2);
  send(sockets[1], frame(3, null, { type: 'ready', current_key: 'right', serial: 10 }));
  expect(audio.play.mock.calls.at(-1)[0]).toBe('right');
  send(sockets[1], frame(null, null, { active: false, occupied: false, serial: 10 }));
  const cancellations = audio.cancelPriority.mock.calls.length;
  send(sockets[1], frame(null, null, { active: false, occupied: false, serial: 10 }));
  expect(audio.cancelPriority).toHaveBeenCalledTimes(cancellations);
});

test('watchdog cancels a frozen stream and rejects expired clips while sink routing is pending', async () => {
  jest.useFakeTimers();
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  send(sockets[0], frame(2, 'left'));
  const valid = audio.play.mock.calls[0][2].valid;
  act(() => jest.advanceTimersByTime(700));
  expect(valid()).toBe(false);
  expect(result.current.view.active).toBe(false);
  expect(sockets[0].close).toHaveBeenCalled();
});

test('disabling invalidates callbacks and later enable reuses the loaded manifest', async () => {
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  send(sockets[0], frame(2, 'left'));
  const valid = audio.play.mock.calls[0][2].valid;
  await enable(result);
  expect(result.current.enabled).toBe(false); expect(valid()).toBe(false);
  send(sockets[0], frame(2, 'left'));
  expect(audio.play).toHaveBeenCalledTimes(1);
  await enable(result);
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/prepare'))).toHaveLength(1);
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/settings'))).toHaveLength(1);
});

test('cancelled preparation cannot enable a spotter after its response arrives', async () => {
  let resolve;
  global.fetch = jest.fn(() => new Promise((done) => { resolve = done; }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  let pending;
  act(() => { pending = result.current.toggle(); });
  expect(result.current.preparing).toBe(true);
  await enable(result);
  await act(async () => { resolve({ ok: true, json: async () => manifest }); await pending; });
  expect(result.current.enabled).toBe(false); expect(sockets).toHaveLength(0);
});

test('preparation failure is visible and does not claim the spotter is enabled', async () => {
  global.fetch = jest.fn(async () => ({ ok: false, json: async () => ({ detail: '음성 연결 필요' }) }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  await waitFor(() => expect(result.current.error).toBe('음성 연결 필요'));
  expect(result.current.enabled).toBe(false); expect(sockets).toHaveLength(0);
});

const radioEvent = (key, extra = {}) => ({ id: `run:${key}`, serial: 1, key, text: key,
  engineer: 'spotter', priority: 1, run_id: 'run', cached: false, expires_in_ms: 15000, ...extra });
const radioFrame = (events, quiet = true, extra = {}) => frame(1, null, { serial: 0,
  radio: { connected: true, run_id: 'run', events, quiet }, ...extra });

test('prepared routine speech waits until the hold allows a start while urgent spotting stays immediate', async () => {
  global.fetch = jest.fn(async (url) => ({ ok: true, json: async () =>
    url.endsWith('/render') ? { event: { id: 'run:lap_time' }, audio_base64: 'lap-audio' } : manifest }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  const event = radioEvent('lap_time', { text: '이번 랩은 1분 40초입니다.', brief: true });
  // The server folds the driver's hold into the flag: false while braking or shortly before it.
  const timed = (startable) => radioFrame([event], true, { radio: {
    connected: true, run_id: 'run', events: [event], quiet: startable, brief_quiet: startable, speech_window_s: 1,
  } });
  send(sockets[0], timed(false));
  await waitFor(() => expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/render'))).toBe(true));
  await act(async () => {});
  expect(audio.play).not.toHaveBeenCalled();
  send(sockets[0], timed(true));   // a one-second "window" no longer matters: the sentence need not fit
  await waitFor(() => expect(audio.play).toHaveBeenCalledWith('lap-audio', 'audio/mpeg', expect.objectContaining({ priority: 1 })));
  expect(audio.play.mock.calls[0][2].windowSeconds).toBeUndefined();
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/render'))).toHaveLength(1);
  send(sockets[0], frame(2, 'left', { radio: { connected: true, quiet: false, speech_window_s: 0 } }));
  expect(audio.play).toHaveBeenCalledWith('left', 'audio/mpeg', expect.objectContaining({ priority: 3 }));
});

test('lap audio prepares before a short driving window and synthesis is reused once', async () => {
  global.fetch = jest.fn(async (url) => ({ ok: true, json: async () =>
    url.endsWith('/render') ? { event: { id: 'run:lap_time' }, audio_base64: 'lap-audio' } : manifest }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  const event = radioEvent('lap_time', { brief: true });
  send(sockets[0], radioFrame([event], false));
  await waitFor(() => expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/render'))).toBe(true));
  await act(async () => {});
  expect(audio.play).not.toHaveBeenCalled();
  send(sockets[0], radioFrame([event], false, { radio: { connected: true, run_id: 'run', events: [event], quiet: false, brief_quiet: true } }));
  await waitFor(() => expect(audio.play).toHaveBeenCalledWith('lap-audio', 'audio/mpeg', expect.objectContaining({ priority: 1 })));
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/render'))).toHaveLength(1);
});

test('discarded undelivered calls record expiry reason without replaying', async () => {
  const recordDelivery = jest.fn();
  const { result } = renderHook(() => useSpotter('http://test', audio, { busy: true, recordDelivery }));
  await enable(result);
  const event = radioEvent('lap_time', { expires_in_ms: 1 });
  send(sockets[0], radioFrame([event], false));
  await new Promise((resolve) => setTimeout(resolve, 10));
  send(sockets[0], radioFrame([], false));
  expect(recordDelivery).toHaveBeenCalledWith(expect.objectContaining({ event_id: event.id }), 'expired', 'no_playback_window');
  expect(audio.play).not.toHaveBeenCalled();
});

test('routine lap report waits for quiet driving and is acknowledged once it was heard to the end', async () => {
  global.fetch = jest.fn(async (url) => ({ ok: true, json: async () =>
    url.endsWith('/render') ? { event: { id: 'run:lap_time' }, audio_base64: 'lap-audio' } : manifest }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  const event = radioEvent('lap_time');
  send(sockets[0], radioFrame([event], false));
  expect(audio.play).not.toHaveBeenCalled();
  send(sockets[0], radioFrame([event]));
  await waitFor(() => expect(audio.play).toHaveBeenCalledWith('lap-audio', 'audio/mpeg', expect.objectContaining({ priority: 1, interrupt: false })));
  const acks = () => global.fetch.mock.calls.filter(([url]) => url.endsWith('/ack')).length;
  expect(acks()).toBe(0);   // still playing: the server keeps the call
  send(sockets[0], radioFrame([event]));
  expect(audio.play).toHaveBeenCalledTimes(1);
  act(() => audio.play.mock.calls[0][2].onStatus('completed'));
  expect(acks()).toBe(1);
  send(sockets[0], radioFrame([event]));
  expect(audio.play).toHaveBeenCalledTimes(1);
});

test('a call cut off by something more urgent is said again from the start, at most twice', async () => {
  global.fetch = jest.fn(async (url) => ({ ok: true, json: async () =>
    url.endsWith('/render') ? { event: { id: 'run:lap_time' }, audio_base64: 'lap-audio' } : manifest }));
  jest.spyOn(Date, 'now');
  let clock = 1000000;
  Date.now.mockImplementation(() => clock);
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  const event = radioEvent('lap_time');
  const acks = () => global.fetch.mock.calls.filter(([url]) => url.endsWith('/ack')).length;
  for (const heardSoFar of [1, 2, 3]) {
    clock += 2000;
    send(sockets[0], radioFrame([event]));
    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(heardSoFar));
    act(() => audio.play.mock.calls.at(-1)[2].onStatus('interrupted', 'preempted'));
    expect(acks()).toBe(heardSoFar < 3 ? 0 : 1);   // the third cut gives up and lets the server drop it
  }
  clock += 2000;
  send(sockets[0], radioFrame([event]));
  expect(audio.play).toHaveBeenCalledTimes(3);
  Date.now.mockRestore();
});

test('a call the driver stopped himself is not repeated', async () => {
  global.fetch = jest.fn(async (url) => ({ ok: true, json: async () =>
    url.endsWith('/render') ? { event: { id: 'run:lap_time' }, audio_base64: 'lap-audio' } : manifest }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  const event = radioEvent('lap_time');
  send(sockets[0], radioFrame([event]));
  await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1));
  act(() => audio.play.mock.calls[0][2].onStatus('interrupted', 'driver_or_radio_pause'));
  expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/ack'))).toBe(true);
  send(sockets[0], radioFrame([event]));
  expect(audio.play).toHaveBeenCalledTimes(1);
});

test('cached warning does not wait for a routine cloud synthesis and overlap invalidates late routine audio', async () => {
  manifest.clips.blue = { variants: [{ audio_base64: 'blue0', text: '청기' }, { audio_base64: 'blue1', text: '뒤쪽 확인' }] };
  let resolve;
  global.fetch = jest.fn((url) => url.endsWith('/render') ? new Promise((done) => { resolve = done; })
    : Promise.resolve({ ok: true, json: async () => manifest }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  const lap = radioEvent('lap_time');
  send(sockets[0], radioFrame([lap]));
  const blue = radioEvent('blue', { priority: 2, cached: true, variant: 1 });
  send(sockets[0], radioFrame([lap, blue], false));
  await waitFor(() => expect(audio.play).toHaveBeenCalledWith('blue1', 'audio/mpeg', expect.objectContaining({ priority: 2 })));
  send(sockets[0], radioFrame([lap], true, { occupied: true, raw_state: 2, state: 2 }));
  await act(async () => resolve({ ok: true, json: async () => ({ event: lap, audio_base64: 'old-lap' }) }));
  expect(audio.play.mock.calls.some(([encoded]) => encoded === 'old-lap')).toBe(false);
});

test('mute records dynamic basic text without synthesizing and specialist stop prevents detailed calls', async () => {
  audio.muted = true;
  const { result } = renderHook(() => useSpotter('http://test', audio, { specialists: false }));
  await enable(result);
  send(sockets[0], radioFrame([radioEvent('repair_detail', { engineer: 'race' })]));
  expect(global.fetch).toHaveBeenCalledTimes(1);
  send(sockets[0], radioFrame([radioEvent('lap_time')]));
  await waitFor(() => expect(result.current.view.last_text).toBe('lap_time'));
  expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/render'))).toBe(false);
  expect(audio.play).not.toHaveBeenCalled();
});

test('a rejected overlap retries the current state once, without waiting for a new serial', async () => {
  jest.useFakeTimers();
  audio.play.mockImplementationOnce(async () => false);
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  send(sockets[0], frame(2, 'left'));
  await act(async () => {});
  act(() => jest.advanceTimersByTime(300));
  send(sockets[0], frame(2));
  await act(async () => {});
  expect(audio.play.mock.calls.map(([encoded]) => encoded)).toEqual(['left', 'left']);
  expect(result.current.view.delivery_status).toBe('started');
  act(() => jest.advanceTimersByTime(300)); send(sockets[0], frame(2));
  expect(audio.play).toHaveBeenCalledTimes(2);
});

test('sink routing may finish after event expiry when live confirmed overlap is still current', async () => {
  jest.useFakeTimers();
  let resolve;
  audio.play.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result); send(sockets[0], frame(2, 'left'));
  const valid = audio.play.mock.calls[0][2].valid;
  for (let n = 0; n < 4; n += 1) {
    act(() => jest.advanceTimersByTime(300)); send(sockets[0], frame(2));
  }
  expect(valid()).toBe(true);
  await act(async () => resolve(true));
  expect(audio.play).toHaveBeenCalledTimes(1);
});

test('brief raw clear candidates leave confirmed overlap audio intact', async () => {
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result); send(sockets[0], frame(2, 'left'));
  await act(async () => {});
  const valid = audio.play.mock.calls[0][2].valid;
  const count = audio.cancelPriority.mock.calls.length;
  send(sockets[0], frame(1, null, { state: 2, occupied: true }));
  expect(valid()).toBe(true);
  send(sockets[0], frame(2));
  expect(audio.cancelPriority).toHaveBeenCalledTimes(count);
  send(sockets[0], frame(1, 'clear_left', { serial: 2, events: [{ key: 'clear_left', state: 1,
    run_id: 'run', serial: 2, expires_in_ms: 500 }] }));
  expect(valid()).toBe(false);
  expect(audio.play.mock.calls.at(-1)[0]).toBe('clear_left');
});

test('interruptions retry only the latest direction and never resurrect an old side', async () => {
  jest.useFakeTimers();
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result); send(sockets[0], frame(2, 'left'));
  await act(async () => {});
  const options = audio.play.mock.calls[0][2];
  act(() => options.onStatus('interrupted', 'output_changed'));
  act(() => jest.advanceTimersByTime(300)); send(sockets[0], frame(2));
  await act(async () => {});
  expect(audio.play.mock.calls.map(([encoded]) => encoded)).toEqual(['left', 'left']);
  send(sockets[0], frame(3, null, { serial: 2, events: [{ serial: 2, key: 'right', state: 3,
    run_id: 'run', expires_in_ms: 500 }] }));
  await act(async () => {});
  act(() => options.onStatus('failed', 'old_callback'));
  act(() => jest.advanceTimersByTime(300)); send(sockets[0], frame(3, null, { serial: 2 }));
  expect(audio.play.mock.calls.map(([encoded]) => encoded)).toEqual(['left', 'left', 'right']);
});

test('expired clear is not retried after late routing failure', async () => {
  jest.useFakeTimers();
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result); send(sockets[0], frame(2, 'left'));
  await act(async () => {});
  let resolve;
  audio.play.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  send(sockets[0], frame(1, null, { serial: 2, events: [{ key: 'clear_left', state: 1,
    run_id: 'run', serial: 2, expires_in_ms: 200 }] }));
  const valid = audio.play.mock.calls.at(-1)[2].valid;
  act(() => jest.advanceTimersByTime(300)); send(sockets[0], frame(1, null, { serial: 2 }));
  expect(valid()).toBe(false);
  await act(async () => resolve(false));
  act(() => jest.advanceTimersByTime(300)); send(sockets[0], frame(1, null, { serial: 2 }));
  expect(audio.play.mock.calls.map(([encoded]) => encoded)).toEqual(['left', 'clear_left']);
});

test('unmute speaks the authoritative current overlap without a new event', async () => {
  jest.useFakeTimers(); audio.muted = true;
  const { result, rerender } = renderHook(() => useSpotter('http://test', audio));
  await enable(result); send(sockets[0], frame(2, 'left'));
  expect(audio.play).not.toHaveBeenCalled();
  audio.muted = false; rerender();
  act(() => jest.advanceTimersByTime(100)); send(sockets[0], frame(2));
  await act(async () => {});
  expect(audio.play.mock.calls.map(([encoded]) => encoded)).toEqual(['left']);
});

test('disable cancels pending retry and rejected promises never escape', async () => {
  jest.useFakeTimers(); audio.play.mockRejectedValueOnce(new Error('device failure'));
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result); send(sockets[0], frame(2, 'left'));
  await act(async () => {});
  expect(result.current.view.delivery_status).toBe('failed');
  await enable(result);
  act(() => jest.advanceTimersByTime(500));
  expect(audio.play).toHaveBeenCalledTimes(1);
});

test('spotter preferences persist and apply without reconnecting or preparing new clips', async () => {
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await act(async () => result.current.changeSettings({ repeat_seconds: 5, clear_delay: .3 }));
  expect(global.fetch).not.toHaveBeenCalled();
  await enable(result);
  expect(JSON.parse(global.fetch.mock.calls[0][1].body).repeat_seconds).toBe(5);
  await act(async () => result.current.changeSettings({ pit_box_enabled: true }));
  expect(global.fetch.mock.calls.at(-1)[0]).toBe('http://test/api/live-coach/spotter/settings');
  expect(JSON.parse(localStorage.getItem('iracing.spotter.settings')).pit_box_enabled).toBe(true);
  expect(sockets).toHaveLength(1);
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/prepare'))).toHaveLength(1);
});

test('short traffic and pit calls use cached audio, are not started late and are never cut once started', async () => {
  manifest.clips.traffic_faster_behind = { audio_base64: 'traffic', text: '뒤에서 빠른 클래스 접근.' };
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  const event = radioEvent('traffic_faster_behind', { cached: true, priority: 2, expires_in_ms: 2000 });
  send(sockets[0], radioFrame([event], false));
  await act(async () => {});
  const options = audio.play.mock.calls[0][2];
  expect(options.workloadValid()).toBe(true);
  send(sockets[0], radioFrame([], false));
  expect(options.workloadValid()).toBe(false);
  expect(options.whilePlaying).toBeUndefined();
  expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/render'))).toBe(false);
});

test('settings respond immediately while saving and roll back when the server rejects them', async () => {
  const { result } = renderHook(() => useSpotter('http://test', audio));
  await enable(result);
  let resolve, saving;
  global.fetch.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  act(() => { saving = result.current.changeSettings({ pit_box_enabled: true }); });
  expect(result.current.settings.pit_box_enabled).toBe(true);
  expect(result.current.settingsBusy).toBe(true);
  await act(async () => { resolve({ ok: false }); await saving; });
  expect(result.current.settings.pit_box_enabled).toBe(false);
  expect(result.current.settingsBusy).toBe(false);
  expect(result.current.error).toContain('설정을 적용하지 못했습니다');
  expect(localStorage.getItem('iracing.spotter.settings')).toBeNull();
});
