import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import LiveCoach from '../components/LiveCoach';
import { VoiceRecorder } from '../voiceAudio';

const mockOpen = jest.fn(), mockClose = jest.fn();
const mockStop = jest.fn();
const mockAudio = { stop: mockStop, stopNormal: mockStop, play: jest.fn(), refresh: jest.fn(),
  isPriorityBlocked: jest.fn(() => false), outputId: '', devices: [], volume: 1, muted: false };
const mockSpotter = { enabled: false, view: null, settings: {}, toggle: jest.fn() };
jest.mock('../useAudioOutput', () => ({ __esModule: true, default: () => mockAudio }));
jest.mock('../useSpotter', () => ({ __esModule: true, default: () => mockSpotter }));
jest.mock('../useRadioDiagnostics', () => ({ __esModule: true, default: () => jest.fn() }));
jest.mock('../voiceAudio', () => ({ ...jest.requireActual('../voiceAudio'), VoiceRecorder: jest.fn().mockImplementation(() => ({ open: mockOpen, close: mockClose })) }));

beforeEach(() => {
  localStorage.clear(); mockOpen.mockReset().mockResolvedValue(undefined); mockClose.mockReset();
  VoiceRecorder.mockImplementation(() => ({ open: mockOpen, close: mockClose }));
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ speech: {}, notes: [] }) }));
  global.WebSocket = class { close() {} };
});

test('the production host opens the selected microphone and disarms it before a device change', async () => {
  localStorage.setItem('iracing.voice.inputDevice', 'usb-mic');
  let host;
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host = value; return null; }}</LiveCoach>);
  await act(async () => { await host.prepare(); });
  expect(mockOpen).toHaveBeenCalledWith('usb-mic'); expect(host.armed).toBe(true);
  act(() => host.changeInputDevice('second-mic'));
  expect(host.armed).toBe(false); expect(mockClose).toHaveBeenCalled();
  expect(localStorage.getItem('iracing.voice.inputDevice.iracing')).toBe('second-mic');
  await act(async () => { await host.prepare(); });
  expect(mockOpen).toHaveBeenLastCalledWith('second-mic');
});

test('a late microphone permission response cannot rearm a device after another input was selected', async () => {
  let finishOpen, host, pending;
  mockOpen.mockImplementation(() => new Promise(resolve => { finishOpen = resolve; }));
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host = value; return null; }}</LiveCoach>);
  act(() => { pending = host.prepare(); });
  await waitFor(() => expect(host.phase).toBe('arming'));
  act(() => host.changeInputDevice('different-mic'));
  await act(async () => { finishOpen(); await pending; });
  expect(host.armed).toBe(false); expect(host.inputDevice).toBe('different-mic'); expect(host.phase).toBe('idle');
});
