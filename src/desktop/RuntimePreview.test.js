import React, { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AudioPreferences, RuntimeControls } from './RuntimePreview';
import { controlsFromHost } from './labControlState';
import { TooltipProvider } from '../components/ui/tooltip';
import useAudioOutput from '../useAudioOutput';

const headset = { kind: 'audiooutput', deviceId: 'headset', label: 'USB 헤드셋' };
const speaker = { kind: 'audiooutput', deviceId: 'speaker', label: '스피커' };
const microphone = { kind: 'audioinput', deviceId: 'mic', label: 'USB 마이크' };
const originalMedia = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
const originalSink = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'setSinkId');
let media;

test('voice settings expose the boost range in red and save normal-speed playback', async () => {
  render(<Controls />);
  await waitFor(() => expect(media.enumerateDevices).toHaveBeenCalled());
  const volume = screen.getByRole('slider', { name: '음성 볼륨' });
  expect(volume).toHaveAttribute('max', '130');
  fireEvent.change(volume, { target: { value: '130' } });
  expect(volume).toHaveAttribute('data-boosted', 'true');
  expect(volume).toHaveAttribute('aria-valuetext', '130% · 증폭');
  fireEvent.change(volume, { target: { value: '100' } });
  expect(volume).toHaveAttribute('data-boosted', 'false');
  const rate = screen.getByRole('slider', { name: '여러 문장 속도' });
  expect(rate).toHaveValue('1.3');
  expect(rate).toHaveAttribute('min', '1');
  fireEvent.change(rate, { target: { value: '1' } });
  expect(JSON.parse(localStorage.getItem('iracing.voice.playback'))).toMatchObject({ volume: 1, rate: 1, spotterRate: 1.15, pitwallRate: 1.15 });
  fireEvent.change(screen.getByRole('slider', { name: '스포터 속도' }), { target: { value: '1.2' } });
  fireEvent.change(screen.getByRole('slider', { name: '피트월 속도' }), { target: { value: '1.3' } });
  expect(JSON.parse(localStorage.getItem('iracing.voice.playback'))).toMatchObject({ rate: 1, spotterRate: 1.2, pitwallRate: 1.3 });
});

function Controls({ speechAvailable = false }) {
  const audioOutput = useAudioOutput();
  const [inputDevice, setInputDevice] = useState('mic');
  const host = { audioOutput, inputDevice, phase: 'idle', spotter: {}, wallPreferences: {}, testOutput: jest.fn() };
  const runtime = { host, controls: controlsFromHost(host), speech: { available: speechAvailable },
    // An old desktop snapshot must never override the player's current output list.
    devices: [microphone, { kind: 'audiooutput', deviceId: 'old', label: '이전 출력장치' }],
    refreshDevices: jest.fn(),
    change: (key, value) => {
      if (key === 'output') audioOutput.changeOutput(value === 'default' ? '' : value);
      else if (key === 'volume') audioOutput.changeVolume(value / 100);
      else if (key === 'rate') audioOutput.changeRate(value);
      else if (key === 'spotterRate') audioOutput.changeSpotterRate(value);
      else if (key === 'pitwallRate') audioOutput.changePitwallRate(value);
      else setInputDevice(value);
    } };
  return <TooltipProvider><RuntimeControls runtime={runtime} /><AudioPreferences runtime={runtime} /></TooltipProvider>;
}

beforeEach(() => {
  localStorage.clear();
  media = new EventTarget();
  media.enumerateDevices = jest.fn(async () => [microphone, headset, speaker]);
  media.getUserMedia = jest.fn();
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: media });
  Object.defineProperty(HTMLMediaElement.prototype, 'setSinkId', { configurable: true, value: jest.fn() });
});

afterEach(() => {
  if (originalMedia) Object.defineProperty(navigator, 'mediaDevices', originalMedia); else delete navigator.mediaDevices;
  if (originalSink) Object.defineProperty(HTMLMediaElement.prototype, 'setSinkId', originalSink); else delete HTMLMediaElement.prototype.setSinkId;
});

test('header output works without speech API and stays in sync with detailed settings on unplug and reconnect', async () => {
  localStorage.setItem('iracing.voice.outputDevice', 'headset');
  render(<Controls />);
  const trigger = screen.getByRole('button', { name: '출력장치 선택' });
  await waitFor(() => expect(trigger).toHaveTextContent('USB 헤드셋'));
  expect(screen.queryByRole('button', { name: '입력장치 선택' })).not.toBeInTheDocument();
  const settings = screen.getByRole('combobox', { name: '출력장치', exact: true });
  expect(settings).toHaveValue('headset');
  fireEvent.click(trigger);
  const dialog = screen.getByRole('dialog', { name: '오디오 출력장치' });
  const select = within(dialog).getByRole('combobox', { name: '출력장치 선택' });
  await waitFor(() => expect(within(dialog).getByRole('button', { name: '목록 갱신' })).toBeEnabled());
  expect(within(select).queryByRole('option', { name: 'USB 마이크' })).not.toBeInTheDocument();
  expect(within(select).queryByRole('option', { name: '이전 출력장치' })).not.toBeInTheDocument();
  expect(media.getUserMedia).not.toHaveBeenCalled();
  fireEvent.change(select, { target: { value: 'speaker' } });
  expect(localStorage.getItem('iracing.voice.outputDevice')).toBe('speaker');
  expect(settings).toHaveValue('speaker');
  media.enumerateDevices.mockResolvedValue([microphone, headset]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  expect(select).toHaveValue('speaker');
  expect(settings).toHaveValue('speaker');
  expect(within(dialog).getByRole('status')).toHaveTextContent('저장된 선택은 유지');
  expect(localStorage.getItem('iracing.voice.outputDevice')).toBe('speaker');
  media.enumerateDevices.mockResolvedValue([microphone, headset, speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  expect(select).toHaveValue('speaker');
  expect(settings).toHaveValue('speaker');
  expect(within(dialog).getByRole('status')).toHaveTextContent('다시 연결');
  fireEvent.click(within(dialog).getByRole('button', { name: '선택 완료' }));
  expect(trigger).toHaveTextContent('스피커');
});

test('hidden output IDs are revealed by explicit opening and the temporary microphone is released', async () => {
  media.enumerateDevices.mockResolvedValue([{ kind: 'audiooutput', deviceId: '', label: '' }]);
  const stop = jest.fn();
  media.getUserMedia.mockImplementation(async () => {
    media.enumerateDevices.mockResolvedValue([headset]);
    return { getTracks: () => [{ stop }] };
  });
  render(<Controls />);
  await act(async () => {});
  expect(media.getUserMedia).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '출력장치 선택' }));
  const select = within(screen.getByRole('dialog')).getByRole('combobox');
  await waitFor(() => expect(within(select).getByRole('option', { name: 'USB 헤드셋' })).toBeInTheDocument());
  expect(media.getUserMedia).toHaveBeenCalledTimes(1);
  expect(stop).toHaveBeenCalledTimes(1);
});

test('microphone selection remains available in detailed settings and cannot change the output', async () => {
  render(<Controls speechAvailable />);
  const select = screen.getByRole('combobox', { name: '마이크 입력장치' });
  fireEvent.change(select, { target: { value: 'default' } });
  await act(async () => {});
  expect(select).toHaveValue('default');
  expect(screen.getByRole('combobox', { name: '출력장치', exact: true })).toHaveValue('default');
  expect(localStorage.getItem('iracing.voice.outputDevice')).toBeNull();
});

test('unsupported browsers keep an explanation and a disabled output selector', async () => {
  delete HTMLMediaElement.prototype.setSinkId;
  render(<Controls />);
  fireEvent.click(screen.getByRole('button', { name: '출력장치 선택' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByRole('combobox')).toBeDisabled();
  expect(within(dialog).getByRole('status')).toHaveTextContent('최신 Chrome 또는 Edge');
  await act(async () => {});
  expect(media.getUserMedia).not.toHaveBeenCalled();
});
