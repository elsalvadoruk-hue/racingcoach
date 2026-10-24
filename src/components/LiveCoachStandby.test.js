import React from 'react';
import { act, renderHook, render, screen, fireEvent, waitFor } from '@testing-library/react';
import useSpotter from '../useSpotter';
import LiveCoach from './LiveCoach';
import useAudioOutput from '../useAudioOutput';
import { STANDBY_MESSAGE } from '../audioHost';

jest.mock('../useAudioOutput');
let sockets, output;
const originalFetch = global.fetch, originalSocket = global.WebSocket;
beforeEach(() => {
  localStorage.clear(); sockets = [];
  global.WebSocket = jest.fn(() => {
    const socket = { close: jest.fn() }; sockets.push(socket); return socket;
  });
  output = { devices: [], outputId: '', supported: false, refresh: jest.fn(), changeOutput: jest.fn(),
    play: jest.fn(async (_encoded, _mime, options) => { options?.onStatus?.('started', ''); return true; }),
    stop: jest.fn(), volume: 1, muted: false, changeVolume: jest.fn(), toggleMute: jest.fn(),
    isPlaying: false, setPriorityGate: jest.fn(), cancelPriority: jest.fn(), standby: false };
  useAudioOutput.mockImplementation(() => output);
  global.fetch = jest.fn(async url => ({ ok: true, json: async () =>
    url.endsWith('/reports') ? { pit_wall: { active: true, session_type: 'Race', state: 4 }, report: null } :
    url.endsWith('/status') ? { connected: true, game: 'lmu', snapshot: { session_state: 4, session_type: 'Race' } } :
    { notes: [], voice: 'Fenrir', clips: { test: { audio_base64: 'test' }, yellow: { audio_base64: 'yellow' } } }
  }));
});
afterEach(() => { global.fetch = originalFetch; global.WebSocket = originalSocket; jest.useRealTimers(); });

test('a silent observer never logs failures for the speaking client, including disconnect and disable', async () => {
  const heard = jest.fn(), silent = jest.fn();
  const standby = { ...output, play: jest.fn(), standby: true };
  const speaker = renderHook(() => useSpotter('http://test', output, { recordDelivery: heard }));
  const observer = renderHook(() => useSpotter('http://test', standby, { recordDelivery: silent }));
  await act(async () => speaker.result.current.toggle());
  await act(async () => observer.result.current.toggle());
  const event = { id: 'run:1', key: 'yellow', engineer: 'spotter', cached: true, priority: 2,
    serial: 1, run_id: 'run', expires_in_ms: 10000 };
  const frame = events => ({ type: 'spotter', active: true, occupied: false, state: 1, raw_state: 1,
    backend_version: '2.1.2', run_id: 'run', serial: 1, events: [], radio: { connected: true, run_id: 'run', quiet: true, events } });
  await act(async () => { for (const socket of sockets) socket.onmessage({ data: JSON.stringify(frame([event])) }); });
  expect(output.play).toHaveBeenCalledTimes(1);
  expect(standby.play).not.toHaveBeenCalled();
  act(() => output.play.mock.calls[0][2].onStatus('completed', ''));
  await act(async () => { for (const socket of sockets) socket.onmessage({ data: JSON.stringify(frame([])) }); });
  expect(heard.mock.calls.map(call => call[1])).toEqual(['started', 'completed']);
  expect(heard.mock.calls[0][0].backend_version).toBe('2.1.2');
  await act(async () => sockets[1].onmessage({ data: JSON.stringify(frame([{ ...event, id: 'run:2' }])) }));
  act(() => sockets[1].onclose());
  await act(async () => observer.result.current.toggle());
  expect(silent).not.toHaveBeenCalled();
});

test('standby explains who speaks without consuming reports and recovers after takeover', async () => {
  output.standby = true;
  const app = render(<LiveCoach apiBase="http://test" visible />);
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 시작' }));
  await waitFor(() => expect(screen.getByText(STANDBY_MESSAGE)).toBeVisible());
  expect(screen.queryByText('자동 보고 활성 · 진행 중인 Race 세션 대기')).not.toBeInTheDocument();
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/reports'))).toHaveLength(0);
  output.standby = false;
  app.rerender(<LiveCoach apiBase="http://test" visible />);
  await waitFor(() => expect(screen.getByText('자동 보고 활성 · 상황 변화를 확인 중')).toBeVisible(), { timeout: 3500 });
  expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/reports'))).toBe(true);
});
