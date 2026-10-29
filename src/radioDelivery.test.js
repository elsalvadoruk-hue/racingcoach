import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { GameContext } from './lib/gameContext';
import useAudioOutput from './useAudioOutput';
import useSpotter from './useSpotter';
import * as radioEffect from './radioEffect';

jest.mock('./lib/voiceLibrary', () => ({
  ...jest.requireActual('./lib/voiceLibrary'), __esModule: true,
  default: () => ({ revision: 0, trackSelection: action => action() }),
}));
const saved = { Audio: global.Audio, fetch: global.fetch, WebSocket: global.WebSocket };
const originalSink = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'setSinkId');
const originalMedia = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
let players, sockets, delivery, resolvePlay, hangPlay;
const item = { id: 'review:1', key: 'pit_exit', text: '피트 출구입니다.', engineer: 'spotter',
  priority: 2, serial: 1, run_id: 'review', expires_in_ms: 10000, alert_categories: ['spotter.pit'] };
const frame = (events = [item]) => ({ type: 'spotter', active: true, occupied: false,
  raw_state: 1, state: 1, serial: 1, run_id: 'review',
  radio: { connected: true, run_id: 'review', quiet: true, session_time: 10, events }, events: [] });
const send = message => sockets[0].onmessage({ data: JSON.stringify(message) });
const wrapper = ({ children }) => <GameContext.Provider value={{ selectedGame: 'iracing', activeGame: 'iracing', liveGame: 'iracing' }}>{children}</GameContext.Provider>;

beforeEach(() => {
  jest.useFakeTimers(); localStorage.clear(); players = []; sockets = []; delivery = jest.fn(); hangPlay = false;
  global.Audio = jest.fn(src => {
    const player = { src, pause: jest.fn(), setSinkId: jest.fn(async () => {}),
      play: jest.fn(() => hangPlay ? new Promise(resolve => { resolvePlay = resolve; }) : Promise.resolve()) };
    players.push(player); return player;
  });
  global.WebSocket = jest.fn(() => { const socket = { close: jest.fn() }; sockets.push(socket); return socket; });
  global.fetch = jest.fn(async url => ({ ok: true, json: async () => url.endsWith('/render')
    ? { event: item, audio_base64: 'test-clip' }
    : { voice: 'Fenrir', clips: { test: { audio_base64: 'test' } } } }));
});
afterEach(() => {
  jest.restoreAllMocks(); Object.assign(global, saved); jest.useRealTimers();
  if (originalSink) Object.defineProperty(HTMLMediaElement.prototype, 'setSinkId', originalSink);
  else delete HTMLMediaElement.prototype.setSinkId;
  if (originalMedia) Object.defineProperty(navigator, 'mediaDevices', originalMedia);
  else delete navigator.mediaDevices;
});

test('GLOG-003 invalid pending playback cannot start after delayed play resolution', async () => {
  hangPlay = true;
  const view = renderHook(useAudioOutput, { wrapper });
  const status = jest.fn(); let allowed = true, pending;
  act(() => { pending = view.result.current.play('test', 'audio/mpeg', { priority: 2, valid: () => allowed, onStatus: status }); });
  await act(async () => { await Promise.resolve(); });
  expect(players[0].play).toHaveBeenCalled();
  allowed = false;
  await act(async () => { resolvePlay(); await pending; });
  expect(status).not.toHaveBeenCalledWith('started', '');
  expect(players[0].pause).toHaveBeenCalled();
  expect(view.result.current.audioBusy()).toBe(false);
  view.unmount();
});

test('GLOG-003 queue removal stops pending playback with one terminal delivery', async () => {
  hangPlay = true;
  const view = renderHook(() => {
    const audio = useAudioOutput();
    const spotter = useSpotter('http://fixture', audio, { recordDelivery: delivery });
    return { audio, spotter };
  }, { wrapper });
  await act(async () => view.result.current.spotter.toggle());
  await act(async () => send(frame()));
  expect(players).toHaveLength(1);
  expect(players[0].play).toHaveBeenCalled();
  await act(async () => send(frame([])));
  expect(delivery.mock.calls.some(([, s]) => s === 'cancelled')).toBe(true);
  expect(view.result.current.audio.audioBusy()).toBe(false);
  expect(players[0].pause).toHaveBeenCalled();
  await act(async () => { jest.advanceTimersByTime(5500); });
  const terminal = delivery.mock.calls.filter(([event, s]) => event.key === item.key && ['cancelled', 'failed'].includes(s));
  expect(terminal.map(([, s]) => s)).toEqual(['cancelled']);
  expect(view.result.current.audio.audioBusy()).toBe(false);
  view.unmount();
});

test('GLOG-001 a nearer pit distance replaces the previous distance', async () => {
  const view = renderHook(useAudioOutput, { wrapper });
  let first, second;
  await act(async () => { first = await view.result.current.play('50m', 'audio/mpeg', { priority: 2, pitBoxDistance: 50 }); });
  await act(async () => { second = await view.result.current.play('20m', 'audio/mpeg', { priority: 2, pitBoxDistance: 20 }); });
  expect(first).toBe(true); expect(second).toBe(true);
  expect(players).toHaveLength(2); expect(players[0].pause).toHaveBeenCalled();
  view.unmount();
});

test('GLOG-002 an immediate lap can preempt an ordinary report', async () => {
  const view = renderHook(() => {
    const audio = useAudioOutput();
    const spotter = useSpotter('http://fixture', audio, { recordDelivery: delivery });
    return { audio, spotter };
  }, { wrapper });
  await act(async () => view.result.current.spotter.toggle());
  await act(async () => view.result.current.audio.play('ordinary-report', 'audio/mpeg', { routineReport: true }));
  const lap = { ...item, id: 'review:2', key: 'lap_time', engineer: 'spotter',
    priority: 1, immediate: true, expires_in_ms: 2000, alert_categories: ['spotter.lap'] };
  await act(async () => send(frame([lap])));
  for (let n = 0; n < 10; n += 1) {
    await act(async () => { jest.advanceTimersByTime(200); send(frame([{ ...lap, expires_in_ms: Math.max(0, 1800 - n * 200) }])); });
  }
  await act(async () => send(frame([])));
  expect(players).toHaveLength(2);
  expect(delivery.mock.calls.some(([event, status]) => event.key === 'lap_time' && status === 'started')).toBe(true);
  view.unmount();
});

test.each(['routing', 'effect', 'play'])('queue cancellation releases pending %s and a late resolution cannot affect the next call', async stage => {
  let resolveStage;
  const deferred = new Promise(resolve => { resolveStage = resolve; });
  if (stage === 'effect') jest.spyOn(radioEffect, 'warm').mockReturnValueOnce(deferred);
  if (stage === 'routing') {
    Object.defineProperty(HTMLMediaElement.prototype, 'setSinkId', { configurable: true, value: jest.fn() });
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { enumerateDevices: async () => [] } });
    global.Audio.mockImplementationOnce(src => {
      const p = { src, pause: jest.fn(), setSinkId: jest.fn(() => deferred), play: jest.fn(async () => {}) };
      players.push(p); return p;
    });
  }
  const view = renderHook(useAudioOutput, { wrapper });
  if (stage === 'play') { hangPlay = true; }
  const controller = new AbortController(), status = jest.fn();
  let pending;
  act(() => { pending = view.result.current.play('old', 'audio/mpeg', { signal: controller.signal, onStatus: status }); });
  await act(async () => { await Promise.resolve(); });
  if (stage === 'play') resolveStage = resolvePlay;
  act(() => controller.abort());
  await act(async () => pending);
  expect(view.result.current.audioBusy()).toBe(false);
  expect(status.mock.calls).toEqual([['cancelled', 'event_cancelled']]);
  hangPlay = false;
  await act(async () => view.result.current.play('new', 'audio/mpeg', { priority: 3 }));
  await act(async () => resolveStage());
  expect(players[1].pause).not.toHaveBeenCalled();
  expect(view.result.current.audioBusy()).toBe(true);
  expect(status.mock.calls).toHaveLength(1);
  view.unmount();
});

test('an already started sentence finishes after the queue condition disappears', async () => {
  hangPlay = true;
  const view = renderHook(() => {
    const audio = useAudioOutput();
    return { audio, spotter: useSpotter('http://fixture', audio, { recordDelivery: delivery }) };
  }, { wrapper });
  await act(async () => view.result.current.spotter.toggle());
  await act(async () => send(frame()));
  act(() => players[0].onplaying());
  await act(async () => send(frame([])));
  expect(players[0].pause).not.toHaveBeenCalled();
  await act(async () => { resolvePlay(); });
  act(() => players[0].onended());
  expect(delivery.mock.calls.filter(([event]) => event.key === item.key).map(([, status]) => status)).toEqual(['started', 'completed']);
  view.unmount();
});

test.each([2, 3, 4])('a nearer distance cannot replace unrelated priority %s speech', async priority => {
  const view = renderHook(useAudioOutput, { wrapper });
  await act(async () => view.result.current.play('other', 'audio/mpeg', { priority }));
  let started;
  await act(async () => { started = await view.result.current.play('10m', 'audio/mpeg', { priority: 2, pitBoxDistance: 10 }); });
  expect(started).toBe(false); expect(players[0].pause).not.toHaveBeenCalled();
  view.unmount();
});

test.each([1, 2, 3, 4])('a lap waits for priority %s speech and the nearby-car gate', async priority => {
  const view = renderHook(useAudioOutput, { wrapper });
  await act(async () => view.result.current.play('other', 'audio/mpeg', { priority }));
  expect(view.result.current.canStartLap()).toBe(false);
  act(() => { view.result.current.stop(); view.result.current.setPriorityGate(true); });
  expect(view.result.current.canStartLap()).toBe(false);
  view.unmount();
});
