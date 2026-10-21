import { useCallback, useEffect, useRef, useState } from 'react';
import { useGame, useGameSettings } from './lib/gameContext';
import { readGameItem, writeGameItem } from './lib/gameStorage';
import * as radioEffect from './radioEffect';
import { DEFAULT_RATE, MULTIPART_DEFAULT_RATE, RATE_MIN, RATE_MAX } from './voiceAudio';

const STORAGE_KEY = 'iracing.voice.outputDevice';
// Chrome on Windows also lists "communications", the output Windows keeps for calls. Playing there counts as a
// call: Windows turns the other sounds (the game) down, and a Bluetooth headset switches to its hands-free mode.
const COMMUNICATIONS = 'communications';
const storedOutput = game => readGameItem(STORAGE_KEY, game) || '';
const savedOutput = game => { const id = storedOutput(game); return id === COMMUNICATIONS ? '' : id; };
const storeOutput = (game, id) => writeGameItem(STORAGE_KEY, game, id);
const COMMUNICATIONS_MESSAGE = '“통신” 출력은 Windows가 통화로 처리해 게임 소리를 줄이거나 블루투스 헤드셋을 통화 모드로 바꿀 수 있어 Windows 기본 장치로 바꿨습니다. 필요하면 실제 장치를 직접 선택하세요.';
const HANDS_FREE = /hands[- ]?free/i;
// Only real outputs: the browser's "default" is the first choice of the list, and "communications" is never offered.
// A Bluetooth headset's hands-free output is offered with a warning: using it switches the whole headset to call mode.
const listedOutputs = (all) => all.filter((device) => device.kind === 'audiooutput' && device.deviceId
  && device.deviceId !== 'default' && device.deviceId !== COMMUNICATIONS)
  .map((device) => (HANDS_FREE.test(device.label || '') ? { deviceId: device.deviceId, groupId: device.groupId, kind: device.kind,
    label: `${device.label} · 통화용 연결(게임 소리도 바뀔 수 있음)` } : device));
// Windows can drop an output for a moment while a game reopens it (a session loading). Only a device that is
// still gone this long after it disappeared stops what is being said.
export const OUTPUT_GRACE_MS = 2000;
const MISSING_OUTPUT_MESSAGE = '선택한 출력장치를 사용할 수 없어 임시로 Windows 기본 장치로 재생합니다. 저장된 선택은 유지하며 같은 장치가 연결되면 다음 안내부터 돌아갑니다.';
const PLAYBACK_KEY = 'iracing.voice.playback';
const clampVolume = (value) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1.3, value)) : 1;
export { DEFAULT_RATE, MULTIPART_DEFAULT_RATE, RATE_MIN, RATE_MAX };
const clampRate = (value, fallback = DEFAULT_RATE) => typeof value === 'number' && Number.isFinite(value)
  ? Math.round(Math.max(RATE_MIN, Math.min(RATE_MAX, value)) * 100) / 100 : fallback;
const savedPlayback = game => {
  try {
    const saved = JSON.parse(readGameItem(PLAYBACK_KEY, game));
    return { volume: clampVolume(saved?.volume), muted: saved?.muted === true, rate: clampRate(saved?.rate, MULTIPART_DEFAULT_RATE),
      spotterRate: clampRate(saved?.spotterRate), pitwallRate: clampRate(saved?.pitwallRate) };
  } catch { return { volume: 1, muted: false, rate: MULTIPART_DEFAULT_RATE, spotterRate: DEFAULT_RATE, pitwallRate: DEFAULT_RATE }; }
};

const storePlayback = (game, value) => writeGameItem(PLAYBACK_KEY, game, JSON.stringify(value));
const storeEffect = (game, value) => radioEffect.storeSettings(value, game);

// The answer to a question the driver asked over the radio is what he most wants to hear:
// nothing interrupts it, not even a side-by-side call, which is said after it if still true.
export const ANSWER_PRIORITY = 4;
// Seconds of a sentence's silent tail to skip before the next sentence of the same call starts.
// Chosen by ear on artifacts/voice-pilot/joined.html (scripts/voice_pilot_joined.py).
export const PART_OVERLAP_S = 0;

// Chrome and Edge list the output devices only once the page may use the microphone. When the driver
// presses "목록 갱신" and nothing is listed, the browser is asked once; the microphone stream is closed
// straight away and nothing is recorded.
const askDevicePermission = async () => {
  try {
    const state = await navigator.permissions?.query?.({ name: 'microphone' });
    if (state?.state === 'denied') return 'denied';
  } catch { /* not every browser can query this */ }
  if (!navigator.mediaDevices?.getUserMedia) return 'unavailable';
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
    return 'granted';
  } catch (error) {
    return error?.name === 'NotAllowedError' ? 'denied' : 'unavailable';
  }
};

// A clip joined on the server from cut pieces comes as a whole data URL (a WAV between MP3 sentences).
const clipSource = (clip, mime) => (String(clip).startsWith('data:') ? clip : `data:${mime};base64,${clip}`);

export default function useAudioOutput() {
  const { selectedGame, activeGame } = useGame();
  const supported = typeof HTMLMediaElement.prototype.setSinkId === 'function' && !!navigator.mediaDevices?.enumerateDevices;
  const [outputId, setOutputId, appliedOutput] = useGameSettings(savedOutput, storeOutput);
  const [refreshing, setRefreshing] = useState(false);
  const [devices, setDevices] = useState([]);
  const [message, setMessage] = useState(() => (storedOutput(selectedGame) === COMMUNICATIONS ? COMMUNICATIONS_MESSAGE : ''));
  const [playback, setPlayback, appliedPlayback] = useGameSettings(savedPlayback, storePlayback);
  const [isPlaying, setIsPlaying] = useState(false);
  const playbackSettings = useRef(appliedPlayback); playbackSettings.current = appliedPlayback;
  const [effect, setEffect, appliedEffect] = useGameSettings(radioEffect.savedSettings, storeEffect);
  const effectSettings = useRef(appliedEffect); effectSettings.current = appliedEffect;
  const selected = useRef(appliedOutput), knownDevices = useRef([]), player = useRef(null);
  selected.current = appliedOutput;
  const unavailableOutput = useRef(''), removalTimer = useRef(null);
  const priorityGate = useRef(false);
  const mounted = useRef(true), refreshSerial = useRef(0);
  const permissionFlight = useRef(null), refreshQueued = useRef(false);
  const stop = useCallback((reason = 'stopped') => {
    if (typeof reason !== 'string') reason = 'driver_or_radio_pause';
    const current = player.current;
    if (current) { current.pause(); current._finish?.('interrupted', reason); current._effect?.release(false); }
    player.current = null; if (mounted.current) setIsPlaying(false);
  }, []);
  // The driver's own stop (a new question, the stop button) ends routine speech and his answer,
  // but never a warning that is being spoken.
  const stopNormal = useCallback((reason = 'driver_or_radio_pause') => {
    const priority = player.current?._priority || 0;
    if (priority < 2 || priority >= ANSWER_PRIORITY) stop(reason);
  }, [stop]);
  const setPriorityGate = useCallback((blocked) => {
    priorityGate.current = blocked;
    if (blocked && (player.current?._priority || 0) < 2) stop('nearby_car');
  }, [stop]);
  const cancelPriority = useCallback((minimum = 1) => {
    if (minimum <= 1) priorityGate.current = false;
    // An answer to the driver's question is not a realtime call and outlives the state it was asked in.
    if (player.current && player.current._priority >= minimum && player.current._priority < ANSWER_PRIORITY) stop('realtime_state_changed');
  }, [stop]);
  const isPriorityBlocked = useCallback(() => priorityGate.current || !!player.current?._priority, []);
  const audioBusy = useCallback(() => !!player.current, []);
  const updatePlayback = useCallback((patch) => {
    const next = setPlayback(current => {
      const value = { ...current, ...patch };
      value.volume = clampVolume(value.volume); value.rate = clampRate(value.rate, MULTIPART_DEFAULT_RATE);
      value.spotterRate = clampRate(value.spotterRate); value.pitwallRate = clampRate(value.pitwallRate);
      return value;
    });
    if (selectedGame !== activeGame) return;
    playbackSettings.current = next;
    if (player.current) {
      player.current.volume = Math.min(1, next.volume); player.current.muted = next.muted;
      player.current._effect?.update?.(next);
      if (next.volume > 1 && !player.current._effect?.boosted) setMessage('현재 음성은 100%로 재생됩니다. 증폭은 다음 안내부터 적용되며 최신 Chrome 또는 Edge가 필요합니다.');
    }
  }, [setPlayback, selectedGame, activeGame]);
  const changeVolume = useCallback((volume) => {
    radioEffect.warm(effectSettings.current, selected.current);
    updatePlayback({ volume, muted: false });
  }, [updatePlayback]);
  // From the next clip on; a call already being spoken keeps its speed.
  const changeRate = useCallback((rate) => updatePlayback({ rate }), [updatePlayback]);
  const changeSpotterRate = useCallback((spotterRate) => updatePlayback({ spotterRate }), [updatePlayback]);
  const changePitwallRate = useCallback((pitwallRate) => updatePlayback({ pitwallRate }), [updatePlayback]);
  // The radio effect applies from the next clip on. Changing it is a click, which lets the audio context start.
  const changeEffect = useCallback((patch) => {
    const next = radioEffect.cleanSettings({ ...effect, ...patch });
    setEffect(next);
    if (selectedGame === activeGame) { effectSettings.current = next; radioEffect.warm(next, selected.current); }
  }, [effect, setEffect, selectedGame, activeGame]);
  const toggleMute = useCallback(() => updatePlayback({ muted: !playback.muted }), [updatePlayback, playback.muted]);
  const saveOutput = useCallback((id) => {
    setOutputId(id);
    if (selectedGame === activeGame) {
      unavailableOutput.current = ''; clearTimeout(removalTimer.current); removalTimer.current = null;
      selected.current = id;
    }
  }, [setOutputId, selectedGame, activeGame]);
  // The chosen device left the list OUTPUT_GRACE_MS ago: if it is still gone, stop the call on it. Calls cut off
  // this way are said again once the radio is free (RESUMABLE), on Windows default while the device stays away.
  const confirmRemoval = useCallback(async (id) => {
    removalTimer.current = null;
    try {
      const outputs = listedOutputs(await navigator.mediaDevices.enumerateDevices());
      if (!mounted.current || selected.current !== id || !outputs.length || outputs.some((device) => device.deviceId === id)) return;
      unavailableOutput.current = id;
      stop('output_removed');
      setMessage('선택한 출력장치의 연결이 끊겼습니다. 저장된 선택은 유지하며 사용할 수 없는 동안에는 임시로 Windows 기본 장치로 재생합니다.');
    } catch { /* The next device change checks again. */ }
  }, [stop]);
  const refresh = useCallback(({ ask = false } = {}) => {
    if (!supported) return;
    // Permission can itself trigger devicechange/focus. Finish the user's request first,
    // then recheck any connection change without opening another permission prompt.
    if (permissionFlight.current) {
      if (!ask) refreshQueued.current = true;
      return permissionFlight.current;
    }
    const serial = ++refreshSerial.current;
    if (ask) setRefreshing(true);
    const run = async () => {
      try {
        let all = await navigator.mediaDevices.enumerateDevices();
        if (!mounted.current || serial !== refreshSerial.current) return;
        if (ask && !listedOutputs(all).length) {
          const permission = await askDevicePermission();
          if (!mounted.current || serial !== refreshSerial.current) return;
          if (permission === 'denied') {
            setMessage('브라우저가 이 화면의 마이크 사용을 막고 있어 출력장치 목록을 읽을 수 없습니다. 주소창 왼쪽의 사이트 정보에서 마이크를 허용한 뒤 목록을 다시 갱신하세요.');
            return;
          }
          if (permission === 'granted') all = await navigator.mediaDevices.enumerateDevices();
          if (!mounted.current || serial !== refreshSerial.current) return;
        }
        const outputs = listedOutputs(all);
        if (ask) setMessage(outputs.length ? '' : '이 브라우저가 알려 준 출력장치가 Windows 기본 장치뿐입니다. 장치를 연결했는지 확인하세요.');
        // Before microphone permission, browsers can hide saved device IDs. Only
        // classify removal when a previously visible device actually disappears.
        const removed = selected.current && outputs.length > 0 && knownDevices.current.some((device) => device.deviceId === selected.current)
          && !outputs.some((device) => device.deviceId === selected.current);
        knownDevices.current = outputs; setDevices(outputs);
        if (removed) {
          // Bluetooth endpoints may disappear briefly. Only an explicit user choice changes storage.
          const missing = selected.current;
          clearTimeout(removalTimer.current);
          removalTimer.current = setTimeout(() => confirmRemoval(missing), OUTPUT_GRACE_MS);
        } else if (unavailableOutput.current === selected.current && outputs.some((device) => device.deviceId === selected.current)) {
          unavailableOutput.current = '';
          setMessage('선택한 출력장치가 다시 연결되었습니다. 다음 안내부터 이 장치로 재생합니다.');
        }
      } catch { if (mounted.current && serial === refreshSerial.current) setMessage('출력장치 목록을 읽지 못했습니다. 장치 연결과 브라우저 권한을 확인하고 목록을 갱신하세요.'); }
    };
    const flight = run();
    if (ask) {
      permissionFlight.current = flight;
      flight.finally(() => {
        if (permissionFlight.current !== flight) return;
        permissionFlight.current = null;
        if (!mounted.current) return;
        setRefreshing(false);
        if (refreshQueued.current) { refreshQueued.current = false; refresh(); }
      });
    }
    return flight;
  }, [supported, confirmRemoval]);
  useEffect(() => {
    mounted.current = true;
    refresh();
    // The radio effect's audio context lost its device: what it was carrying is said again from the start.
    const unsubscribe = radioEffect.onLost(() => {
      if (!player.current?._effect) return;
      setMessage('출력장치가 잠시 끊겨 안내를 멈췄습니다. 장치가 돌아오면 처음부터 다시 말합니다.');
      stop('output_error');
    });
    const media = navigator.mediaDevices;
    const visible = () => { if (document.visibilityState === 'visible') refresh(); };
    media?.addEventListener?.('devicechange', refresh);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', visible);
    return () => {
      mounted.current = false; refreshSerial.current += 1; refreshQueued.current = false;
      clearTimeout(removalTimer.current); removalTimer.current = null; unsubscribe();
      media?.removeEventListener?.('devicechange', refresh);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', visible);
      stop('page_closed');
    };
  }, [refresh, stop]);
  useEffect(() => {
    if (storedOutput(selectedGame) === COMMUNICATIONS) storeOutput(selectedGame, '');
  }, [selectedGame]);
  useEffect(() => {
    unavailableOutput.current = ''; clearTimeout(removalTimer.current); removalTimer.current = null;
  }, [activeGame]);
  const changeOutput = useCallback((id) => {
    saveOutput(id); setMessage('');
    if (selectedGame === activeGame) { stop('output_changed'); radioEffect.warm(effectSettings.current, id); }
  }, [stop, saveOutput, selectedGame, activeGame]);
  const play = useCallback(async (encoded, mime = 'audio/mpeg', { priority = 0, valid = () => true, interrupt = true, onStatus, windowSeconds, workloadValid, whilePlaying, rate, channel = 'pitwall' } = {}) => {
    // Multipart calls use their own absolute rate, never a multiplier of the role's rate.
    // Capture it once so every sentence keeps the same speed until this call ends.
    const multipart = Array.isArray(encoded) && encoded.length > 1;
    const speed = clampRate(rate ?? (multipart ? playbackSettings.current.rate
      : channel === 'spotter' ? playbackSettings.current.spotterRate : playbackSettings.current.pitwallRate));
    if (!valid() || (priority < 3 && priorityGate.current)
      || (player.current && ((!interrupt) || (priority > 0 && priority <= player.current._priority) || (priority === 0 && player.current._priority > 0)))) return false;
    stop('preempted'); setMessage('');
    // A list is one call of several whole sentences: they play back to back and nothing slips in between.
    const rest = Array.isArray(encoded) ? encoded.slice(1) : [];
    const current = new Audio(clipSource(Array.isArray(encoded) ? encoded[0] : encoded, mime));
    current._priority = priority;
    let terminal = false, monitor, metadataReady;
    current._finish = (status, reason = '') => {
      if (terminal) return;
      terminal = true; onStatus?.(status, reason);
      clearInterval(monitor); metadataReady?.();
    };
    player.current = current;
    setIsPlaying(true);
    // Keep every sentence and its Web Audio effect on the same actual output for this call.
    // Try the saved device again on each new call, even if devicechange was missed.
    let sinkId = selected.current;
    const active = (part) => mounted.current && !terminal && player.current === part;
    const routingFailed = (part, failure) => {
      if (!active(part)) return false;
      setMessage(failure?.name === 'NotAllowedError'
        ? '선택한 출력장치의 사용 권한이 없습니다. 마이크 준비로 권한을 허용한 뒤 장치를 다시 선택하세요.'
        : '선택한 출력장치로 연결하지 못했습니다. 장치 연결을 확인하거나 Windows 기본 장치를 선택하세요.');
      current._finish('failed', 'output_routing'); stop(); return false;
    };
    const route = async (part) => {
      if (!supported) return active(part);
      try {
        await part.setSinkId(sinkId);
        if (active(part) && sinkId && unavailableOutput.current === sinkId) unavailableOutput.current = '';
      } catch (failure) {
        if (!active(part)) return false;
        if (failure?.name !== 'NotFoundError' || !sinkId) return routingFailed(part, failure);
        unavailableOutput.current = sinkId;
        sinkId = '';
        try { await part.setSinkId(sinkId); }
        catch (fallbackFailure) { return routingFailed(part, fallbackFailure); }
        if (!active(part)) return false;
        setMessage(MISSING_OUTPUT_MESSAGE);
      }
      return active(part);
    };
    const followed = new Set();
    const follow = (part) => {
      if (terminal || player.current !== part || followed.has(part)) return;
        followed.add(part);
        part._effect?.release(!rest.length);
        part._effect = null;
        if (!rest.length) { current._finish('completed'); stop(); return; }
        const next = new Audio(clipSource(rest.shift(), mime));
        next._priority = priority; next._finish = current._finish;
        next.playbackRate = speed; next.preservesPitch = true;
        next.volume = Math.min(1, playbackSettings.current.volume); next.muted = playbackSettings.current.muted;
        chain(next);
        player.current = next;
        const start = async () => {
          try {
            if (!await route(next)) return;
            await radioEffect.warm(effectSettings.current, sinkId);
            if (!active(next)) return;
            next._effect = radioEffect.attach(next, effectSettings.current, { sinkId, ...playbackSettings.current });
            next.volume = Math.min(1, playbackSettings.current.volume); next.muted = playbackSettings.current.muted;
            next._effect?.start(false);
            await next.play();
          } catch {
            if (active(next)) { current._finish('failed', 'playback_error'); stop(); }
          }
        };
        start();
    };
    const chain = (part) => {
      part.onended = () => follow(part);
      part.onerror = () => {
        // Failing after it was heard is the output going away under it (a device taken or reset), not the clip:
        // the call is said again from the start once the radio is free.
        if (part._heard && player.current === part) {
          setMessage('출력장치가 잠시 끊겨 안내를 멈췄습니다. 장치가 돌아오면 처음부터 다시 말합니다.');
          stop('output_error'); return;
        }
        current._finish('failed', 'media_error'); if (player.current === part) { setMessage('음성을 재생하지 못했습니다. 출력장치를 확인하세요.'); stop(); }
      };
      // The silent tail of a clip can be skipped so the sentences sit closer together.
      part.onplaying = () => {
        part._heard = true;
        if (PART_OVERLAP_S > 0 && rest.length && Number.isFinite(part.duration)) setTimeout(() => follow(part),
          Math.max(0, (part.duration - part.currentTime - PART_OVERLAP_S) / part.playbackRate * 1000));
      };
    };
    chain(current);
    current.playbackRate = speed; current.preservesPitch = true;
    current.volume = Math.min(1, playbackSettings.current.volume); current.muted = playbackSettings.current.muted;
    try {
      if (!await route(current)) return false;
      // A later reply, output change, stop, or unmount cancels pending routing.
      if (!mounted.current || player.current !== current) return false;
      if (!valid() || (priority < 3 && priorityGate.current)) { stop(); return false; }
      if (windowSeconds?.() != null) {
        if (!Number.isFinite(current.duration)) {
          await new Promise((resolve) => {
            const timer = setTimeout(resolve, 1500);
            metadataReady = () => { clearTimeout(timer); resolve(); };
            current.onloadedmetadata = metadataReady;
          });
        }
        if (!mounted.current || player.current !== current || terminal) return false;
        const budget = windowSeconds();
        if (!valid() || budget == null || !Number.isFinite(current.duration)
          || current.duration / current.playbackRate + .5 > budget) {
          current._finish('cancelled', 'speech_window_too_short'); stop(); return false;
        }
      }
      if (workloadValid && !workloadValid()) { current._finish('cancelled', 'driving_workload'); stop(); return false; }
      // Routed through the radio effect only when that is certain to be heard on the chosen device.
      await radioEffect.warm(effectSettings.current, sinkId);
      if (!mounted.current || player.current !== current || terminal) return false;
      if (!valid() || (priority < 3 && priorityGate.current) || (workloadValid && !workloadValid())) { stop(); return false; }
      current._effect = radioEffect.attach(current, effectSettings.current, { sinkId, ...playbackSettings.current });
      current.volume = Math.min(1, playbackSettings.current.volume); current.muted = playbackSettings.current.muted;
      if (playbackSettings.current.volume > 1 && !current._effect?.boosted) setMessage('이 브라우저 또는 출력장치에서는 증폭을 사용할 수 없어 100%로 재생합니다. 최신 Chrome 또는 Edge와 출력장치 설정을 확인하세요.');
      current._effect?.start(true);
      await current.play();
      if (!mounted.current || player.current !== current || terminal) return false;
      onStatus?.('started', '');
      // Braking or steering never cuts a call that has started: a half sentence distracts more
      // than a whole one. Only a call whose situation is over (whilePlaying) is stopped.
      if (whilePlaying) monitor = setInterval(() => {
        if (player.current === current && !whilePlaying()) stop('condition_changed');
      }, 100);
      return true;
    } catch (failure) {
      if (!mounted.current || player.current !== current) return;
      setMessage(failure.name === 'NotAllowedError'
        ? '음성 자동 재생이 차단되었습니다. “답변 다시 듣기” 또는 “테스트 소리”를 눌러주세요.'
        : '소리를 재생하지 못했습니다. 출력장치 연결을 확인하고 다시 시도하세요.');
      current._finish('failed', failure.name || 'playback_error'); stop();
    }
  }, [supported, stop]);
  return { applied: { ...appliedPlayback, outputId: appliedOutput }, outputId, devices, supported, message, refresh, refreshing, changeOutput, play, stop, stopNormal,
    setPriorityGate, cancelPriority, isPriorityBlocked, audioBusy, isPlaying,
    volume: playback.volume, muted: playback.muted, changeVolume, toggleMute, rate: playback.rate, changeRate,
    spotterRate: playback.spotterRate, changeSpotterRate, pitwallRate: playback.pitwallRate, changePitwallRate, effect, changeEffect };
}
