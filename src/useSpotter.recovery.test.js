import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { GameContext } from './lib/gameContext';
import useAudioOutput from './useAudioOutput';
import useSpotter from './useSpotter';

jest.mock('./lib/voiceLibrary', () => ({
  ...jest.requireActual('./lib/voiceLibrary'), __esModule: true,
  default: () => ({ revision: 0, trackSelection: action => action() }),
}));

const saved = { Audio: global.Audio, fetch: global.fetch, WebSocket: global.WebSocket };
let sockets, players, delivery, failMedia;
const event = { id: 'synthetic-run:1', key: 'race_result', engineer: 'race', priority: 2,
  serial: 1, run_id: 'synthetic-run', expires_in_ms: 10000, alert_categories: ['spotter.result'] };
const frame = connected => ({ type: 'spotter', active: connected, occupied: false,
  raw_state: 1, state: 1, serial: 1, run_id: connected ? 'synthetic-run' : null,
  radio: { connected, run_id: connected ? 'synthetic-run' : null, quiet: true,
    session_time: connected ? 10 : null, events: connected ? [event] : [] }, events: [] });
const send = (socket, message) => socket.onmessage({ data: JSON.stringify(message) });
const refresh = async (delay = 0) => act(async () => { jest.advanceTimersByTime(delay); send(sockets[0], frame(true)); });
const wrapper = ({ children }) => <GameContext.Provider value={{ selectedGame: 'lmu', activeGame: 'lmu', liveGame: 'lmu' }}>{children}</GameContext.Provider>;

beforeEach(() => {
  jest.useFakeTimers(); localStorage.clear(); sockets = []; players = []; failMedia = false;
  delivery = jest.fn();
  global.Audio = jest.fn(src => {
    const player = { src, pause: jest.fn(), setSinkId: jest.fn(async () => {}),
      play: jest.fn(async () => { if (failMedia) player.onerror?.(); }) };
    players.push(player); return player;
  });
  global.WebSocket = jest.fn(() => {
    const socket = { close: jest.fn() }; sockets.push(socket); return socket;
  });
  global.fetch = jest.fn(async url => ({ ok: true, json: async () => url.endsWith('/render')
    ? { event, audio_base64: 'synthetic-result' }
    : { voice: 'Fenrir', clips: { test: { audio_base64: 'synthetic-test' }, left: { audio_base64: 'synthetic-left' }, unavailable: { audio_base64: 'synthetic-unavailable', text: '연결 대기' } } } }));
});
afterEach(() => { Object.assign(global, saved); jest.useRealTimers(); });

const setup = async () => {
  const utils = renderHook(() => {
    const audio = useAudioOutput();
    const spotter = useSpotter('http://test', audio, { recordDelivery: delivery });
    return { audio, spotter };
  }, { wrapper });
  await act(async () => utils.result.current.spotter.toggle());
  await act(async () => send(sockets[0], frame(true)));
  return utils;
};

test.each(['disconnect frame', 'socket closed'])('connection notice lets an already started result finish: %s', async mode => {
  const utils = await setup();
  expect(delivery.mock.calls.some(([e, s]) => e.key === 'race_result' && s === 'started')).toBe(true);
  await act(async () => {
    if (mode === 'disconnect frame') send(sockets[0], frame(false));
    else sockets[0].onclose();
  });
  expect(delivery.mock.calls.some(([e, s]) => e.key === 'race_result' && s === 'interrupted')).toBe(false);
  expect(players).toHaveLength(1);
  expect(players[0].pause).not.toHaveBeenCalled();
  await act(async () => players[0].onended());
  expect(delivery.mock.calls.some(([e, s]) => e.key === 'race_result' && s === 'completed')).toBe(true);
  expect(players).toHaveLength(1);
  utils.unmount();
});

test('pre-start media failures back off and retire after three attempts', async () => {
  failMedia = true;
  const utils = await setup();
  for (let i = 0; i < 9; i += 1) await refresh();
  expect(players).toHaveLength(1);
  for (const delay of [500, 1000]) {
    for (let elapsed = 0; elapsed < delay; elapsed += 100) {
      await refresh(100);
    }
  }
  for (let i = 0; i < 20; i += 1) await refresh(100);
  const failed = delivery.mock.calls.filter(([e, s, r]) => e.key === 'race_result' && s === 'failed' && r === 'media_error');
  expect(failed).toHaveLength(3);
  expect(players).toHaveLength(3);
  expect(delivery.mock.calls.filter(([, s]) => s === 'started')).toHaveLength(0);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/ack'))).toHaveLength(1);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/render'))).toHaveLength(3);
  utils.unmount();
});

test('a new urgent event plays while another event waits to retry', async () => {
  failMedia = true;
  const utils = await setup();
  failMedia = false;
  const urgent = { ...event, id: 'synthetic-run:2', serial: 2, key: 'pit_speed', priority: 3 };
  await act(async () => send(sockets[0], { ...frame(true), radio: { ...frame(true).radio, events: [event, urgent] } }));
  expect(delivery.mock.calls.some(([e, s]) => e.event_id === urgent.id && s === 'started')).toBe(true);
  utils.unmount();
});

test('a recovered retry can complete and acknowledge once', async () => {
  failMedia = true;
  const utils = await setup();
  failMedia = false;
  await act(async () => { jest.advanceTimersByTime(500); send(sockets[0], frame(true)); });
  expect(players).toHaveLength(2);
  await act(async () => players[1].onended());
  expect(delivery.mock.calls.some(([e, s]) => e.key === 'race_result' && s === 'completed')).toBe(true);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/ack'))).toHaveLength(1);
  utils.unmount();
});

test('real overlap safety still interrupts the result', async () => {
  const utils = await setup();
  await act(async () => send(sockets[0], { ...frame(true), occupied: true, state: 2, raw_state: 2,
    events: [{ key: 'left', state: 2, serial: 2, run_id: event.run_id, expires_in_ms: 600 }] }));
  expect(players[0].pause).toHaveBeenCalled();
  utils.unmount();
});

test('disconnect notice plays when audio is idle and is never deferred into a new session', async () => {
  const utils = await setup();
  await act(async () => players[0].onended());
  await act(async () => send(sockets[0], frame(false)));
  expect(players).toHaveLength(2);
  expect(delivery.mock.calls.some(([e, s]) => e.key === 'unavailable' && s === 'started')).toBe(true);
  await act(async () => { send(sockets[0], frame(true)); jest.advanceTimersByTime(300); });
  expect(players).toHaveLength(2);
  expect(players[1].pause).toHaveBeenCalled();
  utils.unmount();
});

test('disabling radio also stops an idle connection notice that already started', async () => {
  const utils = await setup();
  await act(async () => players[0].onended());
  await act(async () => send(sockets[0], frame(false)));
  expect(players).toHaveLength(2);
  await act(async () => utils.result.current.spotter.toggle());
  expect(players[1].pause).toHaveBeenCalled();
  utils.unmount();
});

test('disabling radio cancels a pending failure retry', async () => {
  failMedia = true;
  const utils = await setup();
  await act(async () => utils.result.current.spotter.toggle());
  failMedia = false;
  await act(async () => { jest.advanceTimersByTime(500); send(sockets[0], frame(true)); });
  expect(players).toHaveLength(1);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/ack'))).toHaveLength(0);
  utils.unmount();
});
