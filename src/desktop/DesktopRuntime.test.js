import { GameContext } from '../lib/gameContext';
import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import { DesktopRuntimeProvider } from './DesktopRuntime';
import { voiceCapability, loadVoiceCapability } from './labVoiceCapability';
import { listAudioDevices } from './labAudioDevices';

let mockHost;
const mockHostMounts = jest.fn();
jest.mock('../components/LiveCoach', () => ({ __esModule: true, default: (props) => { mockHostMounts(props); return props.children(mockHost); } }));

function host() {
  return { apiBase: 'http://localhost:24158', status: { speech: { enabled: true, recognition_installed: true, credential_configured: true } },
    armed: false, phase: 'idle', inputDevice: 'saved-mic', mode: 'measured', speak: true, wallEnabled: false,
    wallPreferences: { level: 'important' }, notes: [],
    audioOutput: { outputId: 'saved-speaker', volume: .63, muted: true, rate: 1.3, spotterRate: 1.05, pitwallRate: 1.25, effect: { level: 'off' },
      refresh: jest.fn(), changeOutput: jest.fn(), changeVolume: jest.fn(), toggleMute: jest.fn(), changeRate: jest.fn(), changeSpotterRate: jest.fn(), changePitwallRate: jest.fn(), changeEffect: jest.fn() },
    spotter: { enabled: false, settings: { voice: 'Aoede' }, toggle: jest.fn(), changeSettings: jest.fn() },
    changeInputDevice: jest.fn(), prepare: jest.fn(), setMode: jest.fn(), setSpeak: jest.fn(), toggleWall: jest.fn(), changeWallPreferences: jest.fn() };
}

beforeEach(() => {
  mockHost = host(); mockHostMounts.mockClear();
  global.fetch = jest.fn(async (url, options) => ({ ok: true, json: async () => ({ supported: true, auto_show: options?.method === 'PUT' ? JSON.parse(options.body).enabled : false }) }));
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { enumerateDevices: jest.fn(async () => []) } });
});

test('desktop controls operate the one production radio host and retain its saved preferences', async () => {
  let runtime;
  const { rerender } = render(<DesktopRuntimeProvider apiBase={mockHost.apiBase}>{value => { runtime = value; return <output>{value.controls.volume}</output>; }}</DesktopRuntimeProvider>);
  await waitFor(() => expect(runtime.overlay?.supported).toBe(true));
  expect(runtime.controls).toMatchObject({ volume: 63, muted: true, rate: 1.3, microphone: 'saved-mic', output: 'saved-speaker', spotterSettings: { voice: 'Aoede' } });
  await act(async () => {
    await runtime.change('spotter', true); await runtime.change('pitwall', true);
    await runtime.change('microphone', 'second-mic'); await runtime.change('output', 'default');
    await runtime.change('volume', 42); await runtime.change('muted', false); await runtime.change('rate', 1.35);
    await runtime.change('spotterRate', 1); await runtime.change('pitwallRate', 1.2);
    await runtime.change('spotterSettings', { repeat_seconds: 5 }); await runtime.change('wallPreferences', { hold_lead_s: 2 });
  });
  expect(mockHost.spotter.toggle).toHaveBeenCalledTimes(1); expect(mockHost.toggleWall).toHaveBeenCalledTimes(1);
  expect(mockHost.changeInputDevice).toHaveBeenCalledWith('second-mic'); expect(mockHost.audioOutput.changeOutput).toHaveBeenCalledWith('');
  expect(mockHost.audioOutput.changeVolume).toHaveBeenCalledWith(.42); expect(mockHost.audioOutput.toggleMute).toHaveBeenCalledTimes(1);
  expect(mockHost.audioOutput.changeSpotterRate).toHaveBeenCalledWith(1);
  expect(mockHost.audioOutput.changePitwallRate).toHaveBeenCalledWith(1.2);
  expect(mockHost.audioOutput.changeRate).toHaveBeenCalledWith(1.35); expect(mockHost.spotter.changeSettings).toHaveBeenCalledWith({ repeat_seconds: 5 });
  expect(mockHost.changeWallPreferences).toHaveBeenCalledWith({ hold_lead_s: 2 });
  rerender(<DesktopRuntimeProvider apiBase={mockHost.apiBase} visible>{value => <output>{value.controls.volume}</output>}</DesktopRuntimeProvider>);
  expect(mockHostMounts.mock.calls.at(-1)[0]).toMatchObject({ visible: true, monitor: true });
});

test('overlay switch sends a real visibility update and uses the acknowledged state', async () => {
  let runtime;
  render(<DesktopRuntimeProvider apiBase={mockHost.apiBase}>{value => { runtime = value; return null; }}</DesktopRuntimeProvider>);
  await waitFor(() => expect(runtime.overlay?.supported).toBe(true));
  await act(async () => { await runtime.change('overlay', true); });
  expect(global.fetch).toHaveBeenCalledWith(`${mockHost.apiBase}/api/overlay/visibility?game=iracing`, expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true }) }));
  expect(runtime.controls.overlay).toBe(true);
});

test('missing Google configuration cannot arm a microphone or masquerade as a verified connection', async () => {
  mockHost.status.speech.credential_configured = false;
  let runtime;
  render(<DesktopRuntimeProvider apiBase={mockHost.apiBase}>{value => { runtime = value; return null; }}</DesktopRuntimeProvider>);
  await act(async () => { await runtime.change('armed', true); });
  expect(mockHost.prepare).not.toHaveBeenCalled(); expect(runtime.speech.available).toBe(false);
  expect(voiceCapability({ speech: { enabled: true, credential_configured: true, recognition_installed: true } })).toEqual({ available: true, reason: 'configured', verified: false });
  const fetcher = jest.fn(async () => ({ ok: true, json: async () => mockHost.status }));
  await loadVoiceCapability(fetcher, undefined, mockHost.apiBase);
  expect(fetcher).toHaveBeenCalledWith(`${mockHost.apiBase}/api/live-coach/status`, expect.any(Object));
});

test('device permission is requested only by an explicit action and every temporary track is released', async () => {
  const stopOne = jest.fn(), stopTwo = jest.fn();
  const media = { enumerateDevices: jest.fn(async () => [{ kind: 'audioinput', deviceId: 'mic', label: 'USB' }]),
    getUserMedia: jest.fn(async () => ({ getTracks: () => [{ stop: stopOne }, { stop: stopTwo }] })) };
  await listAudioDevices(media, null);
  expect(media.getUserMedia).not.toHaveBeenCalled();
  const result = await listAudioDevices(media, null, { ask: true });
  expect(media.getUserMedia).toHaveBeenCalledWith({ audio: true }); expect(stopOne).toHaveBeenCalledTimes(1); expect(stopTwo).toHaveBeenCalledTimes(1);
  expect(result).toMatchObject({ permission: 'granted', devices: [{ deviceId: 'mic' }] });
});


test('LMU header overlay requests include its selected game', async () => {
  let runtime;
  render(<GameContext.Provider value={{ selectedGame: 'lmu', activeGame: 'lmu' }}><DesktopRuntimeProvider apiBase={mockHost.apiBase}>{value => { runtime = value; return null; }}</DesktopRuntimeProvider></GameContext.Provider>);
  await waitFor(() => expect(runtime.overlay?.supported).toBe(true));
  await act(async () => runtime.change('overlay', true));
  expect(global.fetch.mock.calls.every(([url]) => new URL(url).searchParams.get('game') === 'lmu')).toBe(true);
});
