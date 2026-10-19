import { useCallback, useEffect, useRef, useState } from 'react';
import * as radioEffect from './radioEffect';

const STORAGE_KEY = 'iracing.voice.outputDevice';
const savedOutput = () => { try { return localStorage.getItem(STORAGE_KEY) || ''; } catch { return ''; } };
const PLAYBACK_KEY = 'iracing.voice.playback';
const clampVolume = (value) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 1;
const savedPlayback = () => {
  try {
    const saved = JSON.parse(localStorage.getItem(PLAYBACK_KEY));
    return { volume: clampVolume(saved?.volume), muted: saved?.muted === true };
  } catch { return { volume: 1, muted: false }; }
};

// The answer to a question the driver asked over the radio is what he most wants to hear:
// nothing interrupts it, not even a side-by-side call, which is said after it if still true.
export const ANSWER_PRIORITY = 4;
// Seconds of a sentence's silent tail to skip before the next sentence of the same call starts.
// Chosen by ear on artifacts/voice-pilot/joined.html (scripts/voice_pilot_joined.py).
export const PART_OVERLAP_S = 0;

export default function useAudioOutput() {
  const supported = typeof HTMLMediaElement.prototype.setSinkId === 'function' && !!navigator.mediaDevices?.enumerateDevices;
  const [outputId, setOutputId] = useState(savedOutput);
  const [devices, setDevices] = useState([]);
  const [message, setMessage] = useState('');
  const [playback, setPlayback] = useState(savedPlayback);
  const [isPlaying, setIsPlaying] = useState(false);
  const playbackSettings = useRef(playback);
  const [effect, setEffect] = useState(radioEffect.savedSettings);
  const effectSettings = useRef(effect);
  const selected = useRef(outputId), knownDevices = useRef([]), player = useRef(null);
  const priorityGate = useRef(false);
  const mounted = useRef(true), refreshSerial = useRef(0);
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
    const next = { ...playbackSettings.current, ...patch };
    next.volume = clampVolume(next.volume);
    playbackSettings.current = next; setPlayback(next);
    if (player.current) { player.current.volume = next.volume; player.current.muted = next.muted; }
    try { localStorage.setItem(PLAYBACK_KEY, JSON.stringify(next)); } catch { /* Controls still work without storage. */ }
  }, []);
  const changeVolume = useCallback((volume) => updatePlayback({ volume, muted: false }), [updatePlayback]);
  // The radio effect applies from the next clip on. Changing it is a click, which lets the audio context start.
  const changeEffect = useCallback((patch) => {
    const next = radioEffect.cleanSettings({ ...effectSettings.current, ...patch });
    effectSettings.current = next; setEffect(next); radioEffect.storeSettings(next);
    radioEffect.warm(next, selected.current);
  }, []);
  const toggleMute = useCallback(() => updatePlayback({ muted: !playbackSettings.current.muted }), [updatePlayback]);
  const saveOutput = useCallback((id) => {
    selected.current = id; setOutputId(id);
    try { if (id) localStorage.setItem(STORAGE_KEY, id); else localStorage.removeItem(STORAGE_KEY); } catch { /* Playback still works without storage. */ }
  }, []);
  const refresh = useCallback(async () => {
    if (!supported) return;
    const serial = ++refreshSerial.current;
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      if (!mounted.current || serial !== refreshSerial.current) return;
      const outputs = all.filter((device) => device.kind === 'audiooutput' && device.deviceId && device.deviceId !== 'default');
      // Before microphone permission, browsers can hide saved device IDs. Only
      // classify removal when a previously visible device actually disappears.
      const removed = selected.current && knownDevices.current.some((device) => device.deviceId === selected.current)
        && !outputs.some((device) => device.deviceId === selected.current);
      knownDevices.current = outputs; setDevices(outputs);
      if (removed) {
        stop('output_removed'); saveOutput('');
        setMessage('선택한 출력장치의 연결이 끊겨 Windows 기본 장치로 변경했습니다.');
      }
    } catch { if (mounted.current && serial === refreshSerial.current) setMessage('출력장치 목록을 읽지 못했습니다. 장치 연결과 브라우저 권한을 확인하고 목록을 갱신하세요.'); }
  }, [supported, stop, saveOutput]);
  useEffect(() => {
    mounted.current = true;
    refresh();
    const media = navigator.mediaDevices;
    media?.addEventListener?.('devicechange', refresh);
    return () => { mounted.current = false; refreshSerial.current += 1; media?.removeEventListener?.('devicechange', refresh); stop('page_closed'); };
  }, [refresh, stop]);
  const changeOutput = useCallback((id) => {
    stop('output_changed'); saveOutput(id); setMessage('');
    radioEffect.warm(effectSettings.current, id);
  }, [stop, saveOutput]);
  const play = useCallback(async (encoded, mime = 'audio/mpeg', { priority = 0, valid = () => true, interrupt = true, onStatus, windowSeconds, workloadValid, whilePlaying } = {}) => {
    if (!valid() || (priority < 3 && priorityGate.current)
      || (player.current && ((!interrupt) || (priority > 0 && priority <= player.current._priority) || (priority === 0 && player.current._priority > 0)))) return false;
    stop('preempted'); setMessage('');
    // A list is one call of several whole sentences: they play back to back and nothing slips in between.
    const rest = Array.isArray(encoded) ? encoded.slice(1) : [];
    const current = new Audio(`data:${mime};base64,${Array.isArray(encoded) ? encoded[0] : encoded}`);
    current._priority = priority;
    let terminal = false, monitor, metadataReady;
    current._finish = (status, reason = '') => {
      if (terminal) return;
      terminal = true; onStatus?.(status, reason);
      clearInterval(monitor); metadataReady?.();
    };
    player.current = current;
    setIsPlaying(true);
    const followed = new Set();
    const follow = (part) => {
      if (terminal || player.current !== part || followed.has(part)) return;
        followed.add(part);
        part._effect?.release(!rest.length);
        part._effect = null;
        if (!rest.length) { current._finish('completed'); stop(); return; }
        const next = new Audio(`data:${mime};base64,${rest.shift()}`);
        next._priority = priority; next._finish = current._finish;
        next.playbackRate = 1.15; next.preservesPitch = true;
        next.volume = playbackSettings.current.volume; next.muted = playbackSettings.current.muted;
        chain(next);
        player.current = next;
        const start = () => {
          if (terminal || player.current !== next) return;
          next._effect = radioEffect.attach(next, effectSettings.current, { sinkId: selected.current, ...playbackSettings.current });
          next._effect?.start(false);
          Promise.resolve(next.play()).catch(() => { current._finish('failed', 'playback_error'); if (player.current === next) stop(); });
        };
        if (supported) Promise.resolve(next.setSinkId(selected.current)).then(start, start); else start();
    };
    const chain = (part) => {
      part.onended = () => follow(part);
      part.onerror = () => { current._finish('failed', 'media_error'); if (player.current === part) { setMessage('음성을 재생하지 못했습니다. 출력장치를 확인하세요.'); stop(); } };
      // The silent tail of a clip can be skipped so the sentences sit closer together.
      part.onplaying = () => {
        if (PART_OVERLAP_S > 0 && rest.length && Number.isFinite(part.duration)) setTimeout(() => follow(part),
          Math.max(0, (part.duration - part.currentTime - PART_OVERLAP_S) / part.playbackRate * 1000));
      };
    };
    chain(current);
    current.playbackRate = 1.15; current.preservesPitch = true;
    current.volume = playbackSettings.current.volume; current.muted = playbackSettings.current.muted;
    try {
      if (supported) {
        try { await current.setSinkId(selected.current); }
        catch (failure) {
          if (!mounted.current || player.current !== current) return;
          if (failure.name === 'NotFoundError') {
            saveOutput('');
            await current.setSinkId('');
            if (!mounted.current || player.current !== current) return;
            setMessage('저장된 출력장치를 찾지 못해 Windows 기본 장치로 재생합니다. 원하는 장치를 다시 선택하세요.');
          } else {
            setMessage(failure.name === 'NotAllowedError'
              ? '선택한 출력장치의 사용 권한이 없습니다. 마이크 준비로 권한을 허용한 뒤 장치를 다시 선택하세요.'
              : '선택한 출력장치로 연결하지 못했습니다. 장치 연결을 확인하거나 Windows 기본 장치를 선택하세요.');
            current._finish('failed', 'output_routing'); stop(); return false;
          }
        }
      }
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
      radioEffect.warm(effectSettings.current, selected.current);
      current._effect = radioEffect.attach(current, effectSettings.current, { sinkId: selected.current, ...playbackSettings.current });
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
  }, [supported, stop, saveOutput]);
  return { outputId, devices, supported, message, refresh, changeOutput, play, stop, stopNormal,
    setPriorityGate, cancelPriority, isPriorityBlocked, audioBusy, isPlaying,
    volume: playback.volume, muted: playback.muted, changeVolume, toggleMute, effect, changeEffect };
}
