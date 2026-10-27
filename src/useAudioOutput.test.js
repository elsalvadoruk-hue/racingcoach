import React from 'react';
import { GameContext } from './lib/gameContext';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import useAudioOutput, { OUTPUT_GRACE_MS, AUDIO_START_TIMEOUT_MS, AUDIO_STALL_TIMEOUT_MS } from './useAudioOutput';
import { RESUMABLE } from './voiceAudio';
import LiveCoach from './components/LiveCoach';
import * as radioEffect from './radioEffect';

const storageKey = 'iracing.voice.outputDevice';
const headset = { kind: 'audiooutput', deviceId: 'headset', label: 'USB Headset' };
const speaker = { kind: 'audiooutput', deviceId: 'speaker', label: 'Speakers' };
const originalAudio = global.Audio;
const originalFetch = global.fetch;
const originalMedia = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
const originalSink = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'setSinkId');
let media, players;

test('stalled priority audio releases the radio and records a failed terminal status', async () => {
  jest.useFakeTimers();
  const { result, unmount } = renderHook(useAudioOutput);
  const status = jest.fn();
  await act(async () => result.current.play('side', 'audio/mpeg', { priority: 3, onStatus: status }));
  players[0].currentTime = 0;
  act(() => jest.advanceTimersByTime(AUDIO_STALL_TIMEOUT_MS + 500));
  expect(status).toHaveBeenCalledWith('failed', 'playback_stalled');
  expect(result.current.audioBusy()).toBe(false);
  await act(async () => result.current.play('next', 'audio/mpeg', { priority: 1 }));
  expect(players[1].play).toHaveBeenCalled();
  unmount(); jest.useRealTimers();
});

test('hanging output routing settles playback and permits the next call', async () => {
  jest.useFakeTimers();
  const { result, unmount } = renderHook(useAudioOutput);
  let pending;
  const status = jest.fn();
  act(() => { pending = result.current.play('first', 'audio/mpeg', { priority: 3, onStatus: status }); });
  players[0].setSinkId.mockImplementation(() => new Promise(() => {}));
  // Restart with a routing promise that never settles.
  act(() => result.current.stop());
  global.Audio.mockImplementationOnce(src => {
    const player = { src, pause: jest.fn(), play: jest.fn(), setSinkId: jest.fn(() => new Promise(() => {})) };
    players.push(player); return player;
  });
  act(() => { pending = result.current.play('hung', 'audio/mpeg', { priority: 3, onStatus: status }); });
  await act(async () => { jest.advanceTimersByTime(AUDIO_START_TIMEOUT_MS + 500); await pending; });
  expect(status).toHaveBeenCalledWith('failed', 'playback_start_timeout');
  expect(result.current.audioBusy()).toBe(false);
  unmount(); jest.useRealTimers();
});

test('a lap start deadline also cancels delayed native play and cannot stop a newer clip', async () => {
  jest.useFakeTimers();
  const { result, unmount } = renderHook(useAudioOutput);
  let finish, pending;
  const status = jest.fn();
  global.Audio.mockImplementationOnce(src => {
    const player = { src, pause: jest.fn(), play: jest.fn(() => new Promise(resolve => { finish = resolve; })), setSinkId: jest.fn(async () => {}) };
    players.push(player); return player;
  });
  act(() => { pending = result.current.play('lap', 'audio/mpeg', { onStatus: status, startDeadline: Date.now() + 2000 }); });
  await act(async () => {});
  await act(async () => { jest.advanceTimersByTime(2100); await pending; });
  expect(status).toHaveBeenCalledWith('cancelled', 'start_deadline');
  expect(result.current.audioBusy()).toBe(false);
  await act(async () => result.current.play('new', 'audio/mpeg', { priority: 2 }));
  await act(async () => finish());
  expect(players[1].pause).not.toHaveBeenCalled();
  expect(result.current.audioBusy()).toBe(true);
  unmount(); jest.useRealTimers();
});

test('advancing speech survives the start deadline and long duration, including a missing ended event', async () => {
  jest.useFakeTimers();
  const { result, unmount } = renderHook(useAudioOutput);
  const status = jest.fn();
  await act(async () => result.current.play('long-lap', 'audio/mpeg', { startDeadline: Date.now() + 2000, onStatus: status }));
  for (let i = 1; i <= 25; i += 1) {
    players[0].currentTime = i;
    act(() => jest.advanceTimersByTime(1000));
  }
  expect(status.mock.calls.map(([state]) => state)).toEqual(['started']);
  expect(result.current.audioBusy()).toBe(true);
  players[0].ended = true;
  act(() => jest.advanceTimersByTime(250));
  expect(status).toHaveBeenCalledWith('completed', '');
  expect(result.current.audioBusy()).toBe(false);
  unmount();
});

test('a stalled later sentence releases the whole multipart call', async () => {
  jest.useFakeTimers();
  const { result, unmount } = renderHook(useAudioOutput);
  const status = jest.fn();
  await act(async () => result.current.play(['first', 'second'], 'audio/mpeg', { onStatus: status }));
  await act(async () => players[0].onended());
  expect(players[1].play).toHaveBeenCalled();
  act(() => jest.advanceTimersByTime(AUDIO_STALL_TIMEOUT_MS + 500));
  expect(status.mock.calls.map(([state]) => state)).toEqual(['started', 'failed']);
  expect(result.current.audioBusy()).toBe(false);
  expect(players[1].pause).toHaveBeenCalled();
  unmount();
});

test.each(['effect', 'play'])('a hanging %s operation settles and cannot alter the next call after unmount', async stage => {
  jest.useFakeTimers();
  let resolve, pending;
  const blocked = new Promise(done => { resolve = done; });
  if (stage === 'effect') jest.spyOn(radioEffect, 'warm').mockReturnValueOnce(blocked);
  else global.Audio.mockImplementationOnce(src => {
    const part = { src, pause: jest.fn(), play: jest.fn(() => blocked), setSinkId: jest.fn(async () => {}) };
    players.push(part); return part;
  });
  const { result, unmount } = renderHook(useAudioOutput);
  const status = jest.fn();
  act(() => { pending = result.current.play('hung', 'audio/mpeg', { priority: 3, onStatus: status }); });
  await act(async () => {});
  await act(async () => { jest.advanceTimersByTime(AUDIO_START_TIMEOUT_MS + 500); await pending; });
  expect(status).toHaveBeenCalledWith('failed', 'playback_start_timeout');
  await act(async () => result.current.play('recovered'));
  await act(async () => resolve());
  expect(players[1].pause).not.toHaveBeenCalled();
  expect(result.current.audioBusy()).toBe(true);
  unmount();
  await act(async () => { jest.advanceTimersByTime(0); });
  jest.runAllTicks();
  expect(jest.getTimerCount()).toBe(0);
});

test('LMU edits leave the active iRacing audio untouched and are applied when LMU connects', async () => {
  localStorage.setItem(storageKey, 'headset');
  localStorage.setItem('iracing.voice.playback', JSON.stringify({ volume: .6, pitwallRate: 1.15 }));
  let context = { selectedGame: 'lmu', activeGame: 'iracing', liveGame: 'iracing' };
  const wrapper = ({ children }) => <GameContext.Provider value={context}>{children}</GameContext.Provider>;
  const { result, rerender } = renderHook(useAudioOutput, { wrapper });
  expect(result.current.volume).toBe(.6);
  await act(async () => result.current.play('iracing'));
  act(() => { result.current.changeVolume(.2); result.current.changePitwallRate(1.4); result.current.changeOutput('speaker'); result.current.changeEffect({ enabled: true }); });
  expect(players[0].pause).not.toHaveBeenCalled();
  expect(players[0].volume).toBe(.6);
  await act(async () => result.current.play('still-iracing'));
  expect(players[1].setSinkId).toHaveBeenCalledWith('headset');
  expect(players[1].playbackRate).toBe(1.15);
  expect(players[1].volume).toBe(.6);
  context = { ...context, activeGame: 'lmu', liveGame: 'lmu' }; rerender();
  await act(async () => result.current.play('lmu'));
  expect(players[2].setSinkId).toHaveBeenCalledWith('speaker');
  expect(players[2].playbackRate).toBe(1.4);
  expect(players[2].volume).toBe(.2);
  context = { ...context, selectedGame: 'iracing' }; rerender();
  expect(result.current.volume).toBe(.6);
  expect(result.current.outputId).toBe('headset');
  expect(localStorage.getItem(storageKey)).toBe('headset');
  expect(JSON.parse(localStorage.getItem('iracing.voice.playback')).volume).toBe(.6);
});

test('130% is saved while native media stays within its allowed range, with an explicit fallback', async () => {
  const { result, unmount } = renderHook(useAudioOutput);
  act(() => result.current.changeVolume(1.3));
  act(() => result.current.changePitwallRate(1));
  await act(async () => result.current.play('boosted'));
  expect(players[0].volume).toBe(1);
  expect(players[0].playbackRate).toBe(1);
  expect(result.current.message).toContain('100%로 재생'); // no Web Audio in this browser mock
  expect(result.current.volume).toBe(1.3);
  unmount();
  const restored = renderHook(useAudioOutput);
  expect(restored.result.current.volume).toBe(1.3);
  expect(restored.result.current.pitwallRate).toBe(1);
  await waitFor(() => expect(restored.result.current.devices).toHaveLength(2));
  act(() => restored.result.current.changeVolume(9));
  expect(restored.result.current.volume).toBe(1.3);
});

test('real audio duration must fit the current learned straight before playback', async () => {
  const { result } = renderHook(useAudioOutput);
  global.Audio.mockImplementationOnce(() => {
    const player = { duration: 10, pause: jest.fn(), play: jest.fn(), setSinkId: jest.fn(async () => {}) };
    players.push(player); return player;
  });
  const delivery = jest.fn();
  let started;
  await act(async () => { started = await result.current.play('long-report', 'audio/mpeg', {
    windowSeconds: () => 4, workloadValid: () => true, onStatus: delivery,
  }); });
  expect(started).toBe(false);
  expect(players[0].play).not.toHaveBeenCalled();
  expect(delivery).toHaveBeenCalledWith('cancelled', 'speech_window_too_short');
});

test('a report that has started is not cut off when braking begins, only when its situation is over', async () => {
  const { result } = renderHook(useAudioOutput);
  let quiet = true, current = true;
  const delivery = jest.fn();
  await act(async () => result.current.play('report', 'audio/mpeg', {
    workloadValid: () => quiet, whilePlaying: () => current, onStatus: delivery,
  }));
  expect(players[0].play).toHaveBeenCalled();
  quiet = false;
  await act(async () => new Promise((resolve) => setTimeout(resolve, 350)));
  expect(players[0].pause).not.toHaveBeenCalled();
  current = false;
  await waitFor(() => expect(players[0].pause).toHaveBeenCalled());
  expect(delivery).toHaveBeenCalledWith('interrupted', 'condition_changed');
});

test('metadata loading rechecks the live speech budget instead of its old value', async () => {
  const { result } = renderHook(useAudioOutput);
  let budget = 20, pending;
  act(() => { pending = result.current.play('report', 'audio/mpeg', { windowSeconds: () => budget }); });
  await waitFor(() => expect(players[0].onloadedmetadata).toEqual(expect.any(Function)));
  budget = 2;
  players[0].duration = 8;
  await act(async () => { players[0].onloadedmetadata(); await pending; });
  expect(players[0].play).not.toHaveBeenCalled();
});

test('delivery callback distinguishes completion, interruption and playback failure', async () => {
  const { result } = renderHook(useAudioOutput);
  const first = jest.fn(), second = jest.fn();
  await act(async () => result.current.play('first', 'audio/mpeg', { onStatus: first }));
  expect(first).toHaveBeenCalledWith('started', '');
  await act(async () => result.current.play('urgent', 'audio/mpeg', { priority: 3, onStatus: second }));
  expect(first).toHaveBeenCalledWith('interrupted', 'preempted');
  act(() => players[1].onended());
  expect(second).toHaveBeenCalledWith('completed', '');
  expect(second.mock.calls.filter(([status]) => status === 'interrupted')).toHaveLength(0);
  const failed = jest.fn();
  await act(async () => result.current.play('broken', 'audio/mpeg', { onStatus: failed }));
  act(() => players[2].onerror());
  expect(failed).toHaveBeenCalledWith('failed', 'media_error');
});

beforeEach(() => {
  localStorage.clear(); players = [];
  media = new EventTarget();
  media.enumerateDevices = jest.fn(async () => [headset, speaker]);
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: media });
  Object.defineProperty(HTMLMediaElement.prototype, 'setSinkId', { configurable: true, value: jest.fn() });
  global.Audio = jest.fn((src) => {
    const player = { src, pause: jest.fn(), play: jest.fn(async () => {}), setSinkId: jest.fn(async () => {}) };
    players.push(player); return player;
  });
});
afterEach(() => {
  jest.useRealTimers();
  global.Audio = originalAudio;
  global.fetch = originalFetch;
  if (originalMedia) Object.defineProperty(navigator, 'mediaDevices', originalMedia); else delete navigator.mediaDevices;
  if (originalSink) Object.defineProperty(HTMLMediaElement.prototype, 'setSinkId', originalSink); else delete HTMLMediaElement.prototype.setSinkId;
  jest.restoreAllMocks();
});

test.each(['coach', 'spotter'])('answers from %s and replay retain the speaker channel and selected device', async (engineer) => {
  localStorage.setItem('iracing.voice.playback', JSON.stringify({ rate: 1.4, spotterRate: 1, pitwallRate: 1.3 }));
  const fetchMock = global.fetch = jest.fn(async (url) => ({ ok: true, json: async () =>
    url.endsWith('/utterances') ? { note: { id: 1, created_at: 1, text: '앞이 밀려', reply: '진입 속도를 줄여보세요.', context: { engineer } }, audio_base64: 'answer' }
      : url.endsWith('/captures') ? { capture_id: 'capture' } : url.endsWith('/notes') ? { notes: [] } : {} }));
  render(<LiveCoach apiBase="http://localhost:8000" visible />);
  await screen.findByRole('option', { name: 'USB Headset' });
  fireEvent.change(screen.getByRole('combobox', { name: /오디오 출력장치/ }), { target: { value: 'headset' } });
  fireEvent.change(document.getElementById('voice-volume'), { target: { value: '45' } });
  fireEvent.change(screen.getByLabelText('텍스트로 질문하거나 느낌 남기기'), { target: { value: '앞이 밀려' } });
  fireEvent.click(screen.getByRole('button', { name: '기록 보내기' }));
  await waitFor(() => expect(players[0]?.play).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: '음소거', exact: true }));
  expect(players[0].muted).toBe(true);
  expect(players[0].pause).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '음소거 해제', exact: true }));
  expect(players[0].muted).toBe(false);
  expect(players[0].volume).toBe(.45);
  fireEvent.click(screen.getByRole('button', { name: '답변 다시 듣기' }));
  await waitFor(() => expect(players[1]?.play).toHaveBeenCalled());
  // Delivery reports and the live status poll can arrive while local audio starts.
  const contentRequests = () => fetchMock.mock.calls.filter(([url, options = {}]) =>
    !url.endsWith('/radio-delivery') &&
    !(url.endsWith('/api/live-coach/status') && (options.method || 'GET').toUpperCase() === 'GET'));
  const requests = contentRequests().length;
  fireEvent.click(screen.getByRole('button', { name: '테스트 소리' }));
  await waitFor(() => expect(players[2]?.play).toHaveBeenCalled());
  expect(contentRequests()).toHaveLength(requests);
  expect(players[2].src).toMatch(/^data:audio\/wav;base64,/);
  players.forEach((player, index) => {
    expect(player.setSinkId).toHaveBeenCalledWith('headset');
    expect(player.playbackRate).toBe(index < 2 && engineer === 'spotter' ? 1 : 1.3); expect(player.preservesPitch).toBe(true);
    expect(player.volume).toBe(.45); expect(player.muted).toBe(false);
  });
  expect(players[0].pause).toHaveBeenCalled();
  expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe('headset');
});

test('saved output survives remount and is not discarded before microphone permission reveals IDs', async () => {
  localStorage.setItem(storageKey, 'headset');
  localStorage.setItem('iracing.voice.playback', JSON.stringify({ volume: .35, muted: true }));
  media.enumerateDevices.mockResolvedValue([{ kind: 'audiooutput', deviceId: '', label: '' }]);
  const first = renderHook(useAudioOutput);
  await act(async () => {});
  expect(first.result.current.outputId).toBe('headset');
  expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe('headset');
  first.unmount();
  media.enumerateDevices.mockResolvedValue([headset]);
  const second = renderHook(useAudioOutput);
  await waitFor(() => expect(second.result.current.devices).toEqual([headset]));
  await act(async () => second.result.current.play('reply'));
  expect(players[0].setSinkId).toHaveBeenCalledWith('headset');
  expect(players[0].volume).toBe(.35); expect(players[0].muted).toBe(true);
  act(() => second.result.current.changeVolume(0));
  expect(players[0].volume).toBe(0); expect(players[0].muted).toBe(false);
  expect(JSON.parse(localStorage.getItem('iracing.voice.playback.iracing'))).toEqual({ volume: 0, muted: false, rate: 1.3, spotterRate: 1.15, pitwallRate: 1.15 });
});

test('a Bluetooth disconnect preserves the saved choice and reconnect restores the next call without replaying old audio', async () => {
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toHaveLength(2));
  act(() => result.current.changeOutput('headset'));
  await act(async () => result.current.play('reply'));
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  expect(players[0].pause).not.toHaveBeenCalled();             // not before OUTPUT_GRACE_MS
  await waitFor(() => expect(players[0].pause).toHaveBeenCalled(), { timeout: OUTPUT_GRACE_MS + 1000 });
  expect(result.current.outputId).toBe('headset');
  expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe('headset');
  // The notice is set right after the pause; under a loaded full run it lands a render later.
  await waitFor(() => expect(result.current.message).toContain('연결이 끊'));
  expect(players).toHaveLength(1);
  media.enumerateDevices.mockResolvedValue([headset, speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  expect(result.current.message).toContain('다시 연결');
  expect(players).toHaveLength(1);
  await act(async () => result.current.play('next'));
  expect(players[1].setSinkId.mock.calls).toEqual([['headset']]);
  expect(players[1].play).toHaveBeenCalled();
});

test('missing saved device uses default temporarily while retaining the preference and effect routing', async () => {
  localStorage.setItem(storageKey, 'missing');
  const warm = jest.spyOn(radioEffect, 'warm').mockImplementation(() => {});
  const attach = jest.spyOn(radioEffect, 'attach').mockReturnValue(null);
  const { result } = renderHook(useAudioOutput);
  global.Audio.mockImplementationOnce(() => {
    const player = { pause: jest.fn(), play: jest.fn(async () => {}), setSinkId: jest.fn()
      .mockRejectedValueOnce(new DOMException('gone', 'NotFoundError')).mockResolvedValueOnce() };
    players.push(player); return player;
  });
  await act(async () => result.current.play('reply'));
  expect(players[0].setSinkId.mock.calls).toEqual([['missing'], ['']]);
  expect(players[0].play).toHaveBeenCalled();
  expect(result.current.outputId).toBe('missing');
  expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe('missing');
  expect(result.current.message).toContain('기본 장치로 재생');
  expect(warm).toHaveBeenLastCalledWith(expect.any(Object), '');
  expect(attach).toHaveBeenLastCalledWith(players[0], expect.any(Object), expect.objectContaining({ sinkId: '' }));
  // A new call retries the saved output even when the browser missed a connection event.
  await act(async () => result.current.play('restored'));
  expect(players[1].setSinkId.mock.calls).toEqual([['missing']]);
  expect(attach).toHaveBeenLastCalledWith(players[1], expect.any(Object), expect.objectContaining({ sinkId: 'missing' }));
});

test('denied device permission does not send sound to an unintended output', async () => {
  const { result } = renderHook(useAudioOutput);
  act(() => result.current.changeOutput('headset'));
  global.Audio.mockImplementationOnce(() => {
    const player = { pause: jest.fn(), play: jest.fn(), setSinkId: jest.fn(async () => { throw new DOMException('denied', 'NotAllowedError'); }) };
    players.push(player); return player;
  });
  await act(async () => result.current.play('reply'));
  expect(players[0].play).not.toHaveBeenCalled();
  expect(result.current.outputId).toBe('headset');
  expect(result.current.message).toContain('사용 권한');
});

test('changing output while routing is pending cancels old audio before it can play', async () => {
  const { result } = renderHook(useAudioOutput);
  let resolveSink;
  global.Audio.mockImplementationOnce(() => {
    const player = { pause: jest.fn(), play: jest.fn(), setSinkId: jest.fn(() => new Promise((resolve) => { resolveSink = resolve; })) };
    players.push(player); return player;
  });
  let pending;
  act(() => { pending = result.current.play('reply'); });
  act(() => result.current.changeOutput('speaker'));
  await act(async () => { resolveSink(); await pending; });
  expect(players[0].play).not.toHaveBeenCalled();
  expect(players[0].pause).toHaveBeenCalled();
});

test('unsupported browser still plays on system default without using setSinkId', async () => {
  delete HTMLMediaElement.prototype.setSinkId;
  const { result } = renderHook(useAudioOutput);
  await act(async () => result.current.play('reply'));
  expect(result.current.supported).toBe(false);
  expect(players[0].setSinkId).not.toHaveBeenCalled();
  expect(players[0].play).toHaveBeenCalled();
});

test('spotter preempts an engineer and normal replies cannot interrupt or play during overlap', async () => {
  const { result } = renderHook(useAudioOutput);
  await act(async () => result.current.play('engineer'));
  act(() => result.current.setPriorityGate(true));
  expect(players[0].pause).toHaveBeenCalled();
  await act(async () => result.current.play('spotter', 'audio/mpeg', { priority: 3 }));
  await act(async () => result.current.play('late-engineer'));
  expect(players).toHaveLength(2);
  act(() => result.current.stopNormal());
  expect(players[1].pause).not.toHaveBeenCalled();
  act(() => players[1].onended());
  await act(async () => result.current.play('another-engineer'));
  expect(players).toHaveLength(2);
  act(() => result.current.setPriorityGate(false));
  await act(async () => result.current.play('after-clear'));
  expect(players).toHaveLength(3);
});

test('outdated spotter audio is discarded after asynchronous output routing', async () => {
  const { result } = renderHook(useAudioOutput);
  let resolveSink, valid = true;
  global.Audio.mockImplementationOnce(() => {
    const player = { pause: jest.fn(), play: jest.fn(), setSinkId: jest.fn(() => new Promise((done) => { resolveSink = done; })) };
    players.push(player); return player;
  });
  let pending;
  act(() => { pending = result.current.play('left', 'audio/mpeg', { priority: 1, valid: () => valid }); });
  valid = false;
  await act(async () => { resolveSink(); await pending; });
  expect(players[0].play).not.toHaveBeenCalled();
  expect(result.current.isPlaying).toBe(false);
});

test('four-role arbitration waits for routine speech, preempts it for warning and preserves overlap priority', async () => {
  const { result } = renderHook(useAudioOutput);
  await act(async () => result.current.play('coach'));
  await act(async () => result.current.play('lap', 'audio/mpeg', { priority: 1, interrupt: false }));
  expect(players).toHaveLength(1);
  await act(async () => result.current.play('yellow', 'audio/mpeg', { priority: 2 }));
  expect(players[0].pause).toHaveBeenCalled();
  await act(async () => result.current.play('another-warning', 'audio/mpeg', { priority: 2 }));
  expect(players).toHaveLength(2);
  act(() => result.current.setPriorityGate(true));
  await act(async () => result.current.play('left', 'audio/mpeg', { priority: 3 }));
  expect(players[1].pause).toHaveBeenCalled();
  await act(async () => result.current.play('warning-while-overlap', 'audio/mpeg', { priority: 2 }));
  expect(players).toHaveLength(3);
  expect(result.current.audioBusy()).toBe(true);
});

test('the answer to the driver is interrupted by nothing but the driver himself', async () => {
  const { result } = renderHook(useAudioOutput);
  const delivery = jest.fn();
  await act(async () => result.current.play('lap', 'audio/mpeg', { priority: 1 }));
  await act(async () => result.current.play('answer', 'audio/mpeg', { priority: 4, onStatus: delivery }));
  expect(players[0].pause).toHaveBeenCalled();   // the answer goes first, over routine speech
  let started;
  for (const priority of [0, 1, 2, 3]) {
    await act(async () => { started = await result.current.play('other', 'audio/mpeg', { priority }); });
    expect(started).toBe(false);                 // warnings and side-by-side calls wait for it
  }
  act(() => result.current.setPriorityGate(true));   // a car alongside
  act(() => result.current.cancelPriority(3));       // the overlap state changed
  act(() => result.current.cancelPriority());        // the data connection dropped
  expect(players[1].pause).not.toHaveBeenCalled();
  expect(delivery).not.toHaveBeenCalledWith('interrupted', expect.anything());
  act(() => result.current.stopNormal());            // the driver keys the radio again
  expect(players[1].pause).toHaveBeenCalled();
  expect(delivery).toHaveBeenCalledWith('interrupted', 'driver_or_radio_pause');
});

test('several whole sentences play back to back as one call and nothing slips in between', async () => {
  const { result } = renderHook(useAudioOutput);
  expect(result.current).toMatchObject({ spotterRate: 1.15, pitwallRate: 1.15, rate: 1.3 });
  const delivery = jest.fn();
  await act(async () => result.current.play(['first', 'second'], 'audio/mpeg', { priority: 1, onStatus: delivery }));
  expect(players).toHaveLength(1);
  expect(players[0].play).toHaveBeenCalled();
  await act(async () => players[0].onended());
  expect(players).toHaveLength(2);                       // the second sentence follows at once
  expect(players[1].playbackRate).toBe(1.3);
  await waitFor(() => expect(players[1].play).toHaveBeenCalled());
  expect(delivery).not.toHaveBeenCalledWith('completed', expect.anything());
  let started;
  await act(async () => { started = await result.current.play('other', 'audio/mpeg', { priority: 1 }); });
  expect(started).toBe(false);                           // still the same call
  await act(async () => players[1].onended());
  expect(delivery).toHaveBeenCalledWith('completed', '');
  expect(result.current.audioBusy()).toBe(false);
});

test('a sentence joined from number pieces arrives as a whole WAV data URL between MP3 sentences', async () => {
  const { result } = renderHook(useAudioOutput);
  await act(async () => result.current.play(['first', 'data:audio/wav;base64,UklGRg'], 'audio/mpeg', { priority: 1 }));
  expect(players[0].src).toBe('data:audio/mpeg;base64,first');
  await act(async () => players[0].onended());
  expect(players[1].src).toBe('data:audio/wav;base64,UklGRg');   // used as it is, not wrapped as an MP3
});

test('three absolute rates are independent, multipart overrides the role and formation stays fixed', async () => {
  const { result } = renderHook(useAudioOutput);
  act(() => { result.current.changeSpotterRate(1); result.current.changePitwallRate(1.25); result.current.changeRate(1.4); });
  await act(async () => result.current.play('left', 'audio/mpeg', { channel: 'spotter' }));
  expect(players[0].playbackRate).toBe(1);
  await act(async () => players[0].onended());
  await act(async () => result.current.play(['one'], 'audio/mpeg', { channel: 'pitwall' }));
  expect(players[1].playbackRate).toBe(1.25); // A one-element array is still a single sentence.
  await act(async () => players[1].onended());
  for (const channel of ['spotter', 'pitwall']) {
    const offset = players.length;
    await act(async () => result.current.play(['first', 'second'], 'audio/mpeg', { channel }));
    expect(players[offset].playbackRate).toBe(1.4); // Absolute, not 1.25 * 1.4.
    act(() => result.current.changeRate(1));
    await act(async () => players[offset].onended());
    expect(players[offset + 1].playbackRate).toBe(1.4); // Capture speed for the whole call.
    expect(players[offset + 1].preservesPitch).toBe(true);
    await act(async () => players[offset + 1].onended());
    act(() => result.current.changeRate(1.4));
  }
  await act(async () => result.current.play(['hello', 'grid'], 'audio/mpeg', { channel: 'spotter', rate: 1.15 }));
  expect(players[6].playbackRate).toBe(1.15);
  act(() => result.current.changeSpotterRate(3));
  act(() => result.current.changePitwallRate(0));
  expect(result.current.spotterRate).toBe(1.4);
  expect(result.current.pitwallRate).toBe(1);
  expect(result.current.rate).toBe(1.4);
});

test('old multipart preference migrates without changing single sentence defaults and all three restore', async () => {
  localStorage.setItem('iracing.voice.playback', JSON.stringify({ rate: 1.3, volume: .6 }));
  const { result, unmount } = renderHook(useAudioOutput);
  expect(result.current).toMatchObject({ rate: 1.3, spotterRate: 1.15, pitwallRate: 1.15 });
  act(() => { result.current.changeSpotterRate(1); result.current.changePitwallRate(1.2); });
  unmount();
  const restored = renderHook(useAudioOutput);
  expect(restored.result.current).toMatchObject({ rate: 1.3, spotterRate: 1, pitwallRate: 1.2, volume: .6 });
  await waitFor(() => expect(restored.result.current.devices).toHaveLength(2));
});

test('a chain that is cut reports one interruption and does not start its next sentence', async () => {
  const { result } = renderHook(useAudioOutput);
  expect(result.current).toMatchObject({ spotterRate: 1.15, pitwallRate: 1.15, rate: 1.3 });
  const delivery = jest.fn();
  await act(async () => result.current.play(['first', 'second'], 'audio/mpeg', { priority: 1, onStatus: delivery }));
  await act(async () => result.current.play('left', 'audio/mpeg', { priority: 3 }));
  expect(delivery).toHaveBeenCalledWith('interrupted', 'preempted');
  await act(async () => players[0].onended?.());
  expect(players).toHaveLength(2);                       // only the urgent call was added
});

test('pressing refresh with no devices listed asks the browser once, closes the microphone and lists the outputs', async () => {
  const hidden = [{ kind: 'audiooutput', deviceId: '', label: '' }];
  media.enumerateDevices.mockResolvedValue(hidden);
  const track = { stop: jest.fn() };
  media.getUserMedia = jest.fn(async () => { media.enumerateDevices.mockResolvedValue([headset, speaker]); return { getTracks: () => [track] }; });
  const { result } = renderHook(useAudioOutput);
  await act(async () => {});
  expect(result.current.devices).toEqual([]);
  expect(media.getUserMedia).not.toHaveBeenCalled();           // opening the page never asks
  await act(async () => result.current.refresh({ ask: true }));
  expect(media.getUserMedia).toHaveBeenCalledWith({ audio: true });
  expect(track.stop).toHaveBeenCalled();
  expect(result.current.devices).toEqual([headset, speaker]);
  delete media.getUserMedia;
});

test('a blocked microphone says how to allow it instead of listing only the default device silently', async () => {
  media.enumerateDevices.mockResolvedValue([{ kind: 'audiooutput', deviceId: '', label: '' }]);
  media.getUserMedia = jest.fn(async () => { const error = new Error('blocked'); error.name = 'NotAllowedError'; throw error; });
  const { result } = renderHook(useAudioOutput);
  await act(async () => result.current.refresh({ ask: true }));
  expect(result.current.message).toContain('마이크를 허용');
  delete media.getUserMedia;
});

test('devicechange and repeated refresh during permission cannot cancel the requested output discovery', async () => {
  media.enumerateDevices.mockResolvedValue([]);
  let permit;
  const stop = jest.fn();
  media.getUserMedia = jest.fn(() => new Promise(resolve => { permit = resolve; }));
  const { result } = renderHook(useAudioOutput);
  await act(async () => {});
  let pending;
  act(() => { pending = result.current.refresh({ ask: true }); });
  await waitFor(() => expect(media.getUserMedia).toHaveBeenCalledTimes(1));
  expect(result.current.refreshing).toBe(true);
  act(() => {
    media.dispatchEvent(new Event('devicechange'));
    window.dispatchEvent(new Event('focus'));
    result.current.refresh({ ask: true });
  });
  await act(async () => {
    media.enumerateDevices.mockResolvedValue([headset, speaker]);
    permit({ getTracks: () => [{ stop }] });
    await pending;
  });
  expect(media.getUserMedia).toHaveBeenCalledTimes(1);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(result.current.devices).toEqual([headset, speaker]);
  expect(result.current.refreshing).toBe(false);
});

test('returning to the app discovers connections missed in the background without requesting permission', async () => {
  media.enumerateDevices.mockResolvedValue([speaker]);
  media.getUserMedia = jest.fn();
  const { result, unmount } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toEqual([speaker]));
  media.enumerateDevices.mockResolvedValue([headset, speaker]);
  await act(async () => window.dispatchEvent(new Event('focus')));
  expect(result.current.devices).toEqual([headset, speaker]);
  act(() => result.current.changeOutput('headset'));
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => document.dispatchEvent(new Event('visibilitychange')));
  expect(result.current.devices).toEqual([speaker]);
  expect(result.current.outputId).toBe('headset');
  expect(media.getUserMedia).not.toHaveBeenCalled();
  unmount();
  media.enumerateDevices.mockClear();
  window.dispatchEvent(new Event('focus'));
  document.dispatchEvent(new Event('visibilitychange'));
  media.dispatchEvent(new Event('devicechange'));
  expect(media.enumerateDevices).not.toHaveBeenCalled();
});

test('an older device list cannot overwrite a newer connection refresh', async () => {
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toEqual([headset, speaker]));
  let stale;
  media.enumerateDevices.mockImplementationOnce(() => new Promise(resolve => { stale = resolve; }));
  let pending;
  act(() => { pending = result.current.refresh(); });
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  await act(async () => { stale([headset, speaker]); await pending; });
  expect(result.current.devices).toEqual([speaker]);
});

test('a temporarily hidden or empty device list leaves playing audio and the preference intact', async () => {
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toHaveLength(2));
  act(() => result.current.changeOutput('headset'));
  await act(async () => result.current.play('answer'));
  for (const devices of [[], [{ kind: 'audiooutput', deviceId: '', label: '' }], [headset, speaker]]) {
    media.enumerateDevices.mockResolvedValue(devices);
    await act(async () => media.dispatchEvent(new Event('devicechange')));
    expect(players[0].pause).not.toHaveBeenCalled();
    expect(result.current.outputId).toBe('headset');
    expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe('headset');
  }
});

test.each(['', 'speaker'])('an explicit choice of %s during disconnect wins over the old device returning', async (choice) => {
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toHaveLength(2));
  act(() => result.current.changeOutput('headset'));
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  act(() => result.current.changeOutput(choice));
  media.enumerateDevices.mockResolvedValue([headset, speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  await act(async () => result.current.play('new-call'));
  expect(result.current.outputId).toBe(choice);
  expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe(choice);
  if (choice) expect(players[0].setSinkId.mock.calls).toEqual([[choice]]);
});

test('temporary fallback survives page reload with the original device still selected', async () => {
  localStorage.setItem(storageKey, 'headset');
  const first = renderHook(useAudioOutput);
  await waitFor(() => expect(first.result.current.devices).toHaveLength(2));
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  global.Audio.mockImplementationOnce(src => {
    const player = { src, pause: jest.fn(), play: jest.fn(async () => {}), setSinkId: jest.fn()
      .mockRejectedValueOnce(new DOMException('gone', 'NotFoundError')).mockResolvedValueOnce() };
    players.push(player); return player;
  });
  await act(async () => first.result.current.play('temporary'));
  expect(players[0].setSinkId.mock.calls).toEqual([['headset'], ['']]);
  first.unmount();
  media.enumerateDevices.mockResolvedValue([headset, speaker]);
  const second = renderHook(useAudioOutput);
  await waitFor(() => expect(second.result.current.devices).toHaveLength(2));
  await act(async () => second.result.current.play('after-reload'));
  expect(second.result.current.outputId).toBe('headset');
  expect(players[1].setSinkId.mock.calls).toEqual([['headset']]);
});

test.each(['NotAllowedError', 'AbortError', 'NotFoundError'])('multipart routing handles %s explicitly and keeps effects on the actual output', async (name) => {
  const warm = jest.spyOn(radioEffect, 'warm').mockImplementation(() => {});
  const attach = jest.spyOn(radioEffect, 'attach').mockReturnValue(null);
  const { result } = renderHook(useAudioOutput);
  act(() => result.current.changeOutput('headset'));
  const delivery = jest.fn();
  await act(async () => result.current.play(['first', 'second'], 'audio/mpeg', { onStatus: delivery }));
  global.Audio.mockImplementationOnce(src => {
    const player = { src, pause: jest.fn(), play: jest.fn(async () => {}), setSinkId: jest.fn()
      .mockRejectedValueOnce(new DOMException('device failure', name)).mockResolvedValueOnce() };
    players.push(player); return player;
  });
  await act(async () => players[0].onended());
  expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe('headset');
  if (name === 'NotFoundError') {
    expect(players[1].setSinkId.mock.calls).toEqual([['headset'], ['']]);
    expect(players[1].play).toHaveBeenCalled();
    expect(warm).toHaveBeenLastCalledWith(expect.any(Object), '');
    expect(attach).toHaveBeenLastCalledWith(players[1], expect.any(Object), expect.objectContaining({ sinkId: '' }));
    act(() => players[1].onended());
    expect(delivery).toHaveBeenCalledWith('completed', '');
  } else {
    expect(players[1].setSinkId.mock.calls).toEqual([['headset']]);
    expect(players[1].play).not.toHaveBeenCalled();
    expect(delivery).toHaveBeenCalledWith('failed', 'output_routing');
    expect(result.current.isPlaying).toBe(false);
  }
});

test('an old routing rejection cannot replace a newer output choice or fall back after cancellation', async () => {
  const { result } = renderHook(useAudioOutput);
  act(() => result.current.changeOutput('headset'));
  let rejectSink;
  global.Audio.mockImplementationOnce(() => {
    const player = { pause: jest.fn(), play: jest.fn(), setSinkId: jest.fn(() => new Promise((resolve, reject) => { rejectSink = reject; })) };
    players.push(player); return player;
  });
  let pending;
  act(() => { pending = result.current.play('old-call'); });
  act(() => result.current.changeOutput('speaker'));
  await act(async () => {
    rejectSink(new DOMException('gone', 'NotFoundError'));
    await pending;
  });
  expect(players[0].setSinkId.mock.calls).toEqual([['headset']]);
  expect(players[0].play).not.toHaveBeenCalled();
  expect(localStorage.getItem(`${storageKey}.iracing`) ?? localStorage.getItem(storageKey)).toBe('speaker');
  await act(async () => result.current.play('new-call'));
  expect(players[1].setSinkId.mock.calls).toEqual([['speaker']]);
});

test('default output users continue playing through device list changes without acquiring microphone permission', async () => {
  media.getUserMedia = jest.fn();
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toHaveLength(2));
  await act(async () => result.current.play('default-call'));
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  expect(players[0].pause).not.toHaveBeenCalled();
  expect(players[0].setSinkId.mock.calls).toEqual([['']]);
  expect(result.current.outputId).toBe('');
  expect(media.getUserMedia).not.toHaveBeenCalled();
});

test('an output that drops out and returns within the grace leaves the call playing', async () => {
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toHaveLength(2));
  act(() => result.current.changeOutput('headset'));
  const delivery = jest.fn();
  await act(async () => result.current.play('race-start', 'audio/mpeg', { onStatus: delivery }));
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  media.enumerateDevices.mockResolvedValue([headset, speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  await act(async () => new Promise((resolve) => setTimeout(resolve, OUTPUT_GRACE_MS + 200)));
  expect(players[0].pause).not.toHaveBeenCalled();
  expect(delivery.mock.calls.map(([status]) => status)).toEqual(['started']);
  expect(result.current.message).toBe('');
});

test('a call cut by a lost output is reported as one to say again', async () => {
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toHaveLength(2));
  act(() => result.current.changeOutput('headset'));
  const delivery = jest.fn();
  await act(async () => result.current.play('grid', 'audio/mpeg', { onStatus: delivery }));
  media.enumerateDevices.mockResolvedValue([speaker]);
  await act(async () => media.dispatchEvent(new Event('devicechange')));
  await waitFor(() => expect(delivery).toHaveBeenCalledWith('interrupted', 'output_removed'), { timeout: OUTPUT_GRACE_MS + 1000 });
  expect(RESUMABLE).toEqual(expect.arrayContaining(['output_removed', 'output_error']));
});

test('a clip failing after it was heard is an output failure to say again, before that a broken clip', async () => {
  const { result } = renderHook(useAudioOutput);
  const heard = jest.fn(), broken = jest.fn();
  await act(async () => result.current.play('heard', 'audio/mpeg', { onStatus: heard }));
  act(() => players[0].onplaying());
  act(() => players[0].onerror());
  expect(heard).toHaveBeenCalledWith('interrupted', 'output_error');
  expect(heard).not.toHaveBeenCalledWith('failed', expect.anything());
  expect(result.current.message).toContain('다시 말합니다');
  expect(result.current.isPlaying).toBe(false);
  await act(async () => result.current.play('broken', 'audio/mpeg', { onStatus: broken }));
  act(() => players[1].onerror());
  expect(broken).toHaveBeenCalledWith('failed', 'media_error');
});

test('a lost effect context stops only the call it carried, as one to say again', async () => {
  let lost;
  jest.spyOn(radioEffect, 'onLost').mockImplementation((listener) => { lost = listener; return () => {}; });
  const release = jest.fn();
  jest.spyOn(radioEffect, 'warm').mockImplementation(() => {});
  const attach = jest.spyOn(radioEffect, 'attach').mockReturnValue({ boosted: true, start() {}, update() {}, release });
  const { result } = renderHook(useAudioOutput);
  const delivery = jest.fn();
  await act(async () => result.current.play('carried', 'audio/mpeg', { onStatus: delivery }));
  act(() => lost());
  expect(delivery).toHaveBeenCalledWith('interrupted', 'output_error');
  expect(release).toHaveBeenCalled();
  attach.mockReturnValue(null);
  const plain = jest.fn();
  await act(async () => result.current.play('plain', 'audio/mpeg', { onStatus: plain }));
  act(() => lost());
  expect(plain).not.toHaveBeenCalledWith('interrupted', expect.anything());
});

test('the Windows communications output is never offered, and a saved one falls back to default', async () => {
  const communications = { kind: 'audiooutput', deviceId: 'communications', label: '통신 - 헤드셋' };
  const handsFree = { kind: 'audiooutput', deviceId: 'hf', groupId: 'bt', label: '헤드셋 (WH-1000XM5 Hands-Free AG Audio)' };
  localStorage.setItem(storageKey, 'communications');
  media.enumerateDevices.mockResolvedValue([{ kind: 'audiooutput', deviceId: 'default', label: '기본값' }, communications, headset, handsFree]);
  const { result } = renderHook(useAudioOutput);
  await waitFor(() => expect(result.current.devices).toHaveLength(2));
  expect(result.current.devices.map((device) => device.deviceId)).toEqual(['headset', 'hf']);
  expect(result.current.devices[1]).toEqual({ kind: 'audiooutput', deviceId: 'hf', groupId: 'bt',
    label: '헤드셋 (WH-1000XM5 Hands-Free AG Audio) · 통화용 연결(게임 소리도 바뀔 수 있음)' });
  expect(result.current.outputId).toBe('');
  expect(result.current.message).toContain('통신');
  expect(localStorage.getItem(`${storageKey}.iracing`)).toBe('');
  expect(localStorage.getItem(storageKey)).toBe('communications');
  await act(async () => result.current.play('call'));
  expect(players[0].setSinkId.mock.calls).toEqual([['']]);
});
