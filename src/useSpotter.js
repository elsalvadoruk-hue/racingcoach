import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useGame, useGameSettings } from './lib/gameContext';
import { readGameItem, writeGameItem } from './lib/gameStorage';
import { RESUMABLE, MAX_RESUMES, DEFAULT_RATE } from './voiceAudio';
import { eventSound, playBeep } from './drillBeeps';
import useVoiceLibrary, { cleanVoiceSettings, voiceSelections } from './lib/voiceLibrary';
import { readAlerts, voiceEventAllowed } from './lib/voiceAlerts';
import { banterObservation } from './banterObservation';

const SETTINGS_KEY = 'iracing.spotter.settings';
export const RADIO_RENDER_TIMEOUT_MS = 8000;
const MAX_PLAYBACK_ATTEMPTS = 3;
export const SPOTTER_VOICES = [['Fenrir', '남성 · 기본'], ['Algieba', '남성'], ['Schedar', '남성'], ['Aoede', '여성']];
const DEFAULT_SETTINGS = { voice: 'Fenrir', formation_calls: 5, repeat_seconds: 3, clear_delay: .15, traffic_enabled: true, pit_box_enabled: false };
const savedSettings = game => {
  try {
    const saved = JSON.parse(readGameItem(SETTINGS_KEY, game));
    return { alerts: readAlerts('spotter', game, saved || {}), voice: SPOTTER_VOICES.some(([name]) => name === saved?.voice) ? saved.voice : 'Fenrir',
      voice_packs: saved?.voice_packs && typeof saved.voice_packs === 'object' && !Array.isArray(saved.voice_packs) ? voiceSelections(saved) : undefined,
      custom_pack: typeof saved?.custom_pack === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}@[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(saved.custom_pack) ? saved.custom_pack : null,
      formation_calls: Number.isInteger(saved?.formation_calls) && saved.formation_calls >= 0 && saved.formation_calls <= 8 ? saved.formation_calls : 5,
      repeat_seconds: [2, 3, 5].includes(saved?.repeat_seconds) ? saved.repeat_seconds : 3,
      clear_delay: [.15, .3, .5].includes(saved?.clear_delay) ? saved.clear_delay : .15,
      traffic_enabled: saved?.traffic_enabled !== false, pit_box_enabled: saved?.pit_box_enabled === true };
  } catch { return { ...DEFAULT_SETTINGS, alerts: readAlerts('spotter', game) }; }
};

const storeSettings = (game, value) => writeGameItem(SETTINGS_KEY, game, JSON.stringify(value));

const occupied = (frame, alerts) => frame.occupied && alerts?.overlap !== false;
// Optional oval clips never trigger a fetch or synthesis. Older packs keep their side calls.
const overlapClip = (clips, key, audioKey, variant) => {
  const clip = (audioKey && clips?.[audioKey]?.audio_base64 ? clips[audioKey] : clips?.[key]);
  return clip?.variants?.length ? clip.variants[(variant ?? 0) % clip.variants.length] : clip;
};
// The existing bumper threshold is .7s. Include the car ahead as well as behind.
const closeBattle = frame => Boolean(frame.occupied) || (frame.radio?.traffic?.nearby || []).some(car => car.close === true
  || (typeof car.gap_estimate_s === 'number' && car.gap_estimate_s >= 0 && car.gap_estimate_s <= .7));
export default function useSpotter(apiBase, audioOutput, { busy = false, specialists = true, level = 'all', wallAlerts: requestedWallAlerts, recordDelivery } = {}) {
  const wallAlerts = useMemo(() => !requestedWallAlerts || specialists ? requestedWallAlerts
    : Object.fromEntries(Object.keys(requestedWallAlerts).map(key => [key, false])), [requestedWallAlerts, specialists]);
  const { selectedGame, activeGame } = useGame();
  const { play, setPriorityGate, cancelPriority, audioBusy } = audioOutput;
  const { muted, volume, outputId } = audioOutput.applied || audioOutput;
  // The drill beeps follow the radio's output device and volume without reconnecting the socket.
  const outputRef = useRef(outputId); outputRef.current = outputId;
  const volumeRef = useRef(volume); volumeRef.current = volume ?? 1;
  const mutedRef = useRef(muted); mutedRef.current = Boolean(muted);
  const [enabled, setEnabled] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [view, setView] = useState(null);
  const [error, setError] = useState('');
  const [storedSettings, setSettings, storedApplied] = useGameSettings(savedSettings, storeSettings);
  const library = useVoiceLibrary(apiBase);
  const trackSelection = library.trackSelection;
  const settings = cleanVoiceSettings(storedSettings), appliedSettings = cleanVoiceSettings(storedApplied);
  const applied = useRef(appliedSettings); applied.current = appliedSettings;
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [readyGame, setReadyGame] = useState(activeGame);
  const previousGame = useRef(activeGame);
  const [voice, setVoice] = useState(settings.custom_pack || settings.voice);
  const overlapPending = useRef(null);
  const clips = useRef(null), epoch = useRef(0), mounted = useRef(true);
  useEffect(() => { clips.current = null; }, [library.revision]);
  useEffect(() => {
    const choices = voiceSelections(applied.current);
    if (!Object.keys(choices).length) return undefined;
    const controller = new AbortController(); setSettingsBusy(true);
    trackSelection(() => fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...applied.current, voice_packs: choices }) })
      .then(async response => { const v = await response.json(); if (!response.ok) throw Error(typeof v.detail === 'string' ? v.detail : '저장한 목소리를 적용하지 못했습니다.'); })
    )
      .catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setSettingsBusy(false); });
    return () => controller.abort();
  }, [apiBase, trackSelection]);
  const state = useRef({ active: false }), lastFrame = useRef(0), release = useRef(null);
  const standby = Boolean(audioOutput.standby);
  const output = useRef(null);
  output.current = { play, muted, volume, busy, specialists, audioBusy, level, wallAlerts, recordDelivery, standby };
  const alertRevision = useRef(0);
  const rendered = useRef(new Map()), lastView = useRef(0);
  const logEvent = useCallback((event, status, reason = '', droppedParts) => {
    const radio = state.current.radio || {};
    const omitted = droppedParts ?? rendered.current.get(event.id)?.droppedParts;
    output.current.recordDelivery?.({ event_id: event.id, run_id: event.run_id || '', key: event.key,
      backend_version: event.backend_version || null,
      engineer: event.engineer || 'spotter', text: event.spoken_text || event.text || '', session_time: radio.session_time,
      lap_number: radio.lap_number, distance_pct: radio.distance_pct,
      ...(Number.isInteger(omitted) && omitted >= 0 ? { dropped_parts: omitted } : {}) }, status, reason);
  }, []);
  const radioQueue = useRef(new Map()), heard = useRef(new Set()), rendering = useRef(null), processing = useRef(new Set()), lastCall = useRef(0), resumed = useRef(new Map());
  const playbackFailures = useRef(new Map());
  const shadowSeen = useRef(new Set());
  const cancelRender = useCallback(reason => {
    rendering.current?.cancel(reason);
  }, []);
  const previousWallAlerts = useRef(wallAlerts);
  useEffect(() => {
    const wallChanged = previousWallAlerts.current !== wallAlerts;
    previousWallAlerts.current = wallAlerts;
    alertRevision.current += 1;
    cancelRender('alerts_changed');
    rendered.current.clear();
    if (overlapPending.current && !voiceEventAllowed({ key: overlapPending.current.key }, applied.current.alerts, wallAlerts)) overlapPending.current = null;
    for (const event of radioQueue.current.values()) {
      if (!voiceEventAllowed(event, applied.current.alerts, wallAlerts)) {
        heard.current.add(event.id); radioQueue.current.delete(event.id);
      }
    }
    if (applied.current.alerts?.overlap === false) { cancelPriority?.(3); setPriorityGate?.(false); }
    if (!wallChanged || !enabled || readyGame !== activeGame) return;
    let cancelled = false;
    fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...applied.current, wall_alerts: wallAlerts }) })
      .then(response => { if (!response.ok) throw new Error('음성 안내 설정을 적용하지 못했습니다.'); })
      .catch(failure => { if (!cancelled && mounted.current) setError(failure.message); });
    return () => { cancelled = true; };
    // Enabling and a game transition apply all values in their existing handlers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appliedSettings.alerts, wallAlerts]);
  const disable = useCallback(() => {
    epoch.current += 1;
    cancelRender('spotter_disabled');
    state.current = { active: false };
    overlapPending.current = null;
    for (const event of radioQueue.current.values()) if (!output.current.standby && !heard.current.has(event.id)) logEvent(event, 'cancelled', 'spotter_disabled');
    radioQueue.current.clear(); heard.current.clear(); rendered.current.clear(); playbackFailures.current.clear();
    clearTimeout(release.current);
    cancelPriority?.();
    setEnabled(false); setPreparing(false); setView(null);
  }, [cancelPriority, logEvent, cancelRender]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; epoch.current += 1; cancelRender('page_closed'); overlapPending.current = null; clearTimeout(release.current); cancelPriority?.(); };
  }, [cancelPriority, cancelRender]);
  const enable = async () => {
    if (enabled || preparing) { disable(); return; }
    const ticket = ++epoch.current;
    setError(''); setPreparing(true);
    try {
      if (!clips.current) {
        const response = await fetch(`${apiBase}/api/live-coach/spotter/prepare`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...applied.current, wall_alerts: output.current.wallAlerts }) });
        const manifest = await response.json();
        if (!response.ok) throw new Error(manifest.detail || '스포터 음성 준비 실패');
        if (!manifest.clips?.test?.audio_base64) throw new Error('스포터 음성을 확인하지 못했습니다.');
        if (!mounted.current || ticket !== epoch.current) return;
        clips.current = manifest.clips;
        setVoice((manifest.voice || 'Fenrir').replace('ko-KR-Chirp3-HD-', ''));
      } else {
        const response = await fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...applied.current, wall_alerts: output.current.wallAlerts }) });
        if (!response.ok) throw new Error('스포터 설정을 적용하지 못했습니다.');
        if (!mounted.current || ticket !== epoch.current) return;
      }
      if (mounted.current && ticket === epoch.current) { setReadyGame(activeGame); setEnabled(true); setPreparing(false); }
    } catch (failure) {
      if (mounted.current && ticket === epoch.current) { setError(failure.message); setPreparing(false); }
    }
  };
  const changeSettings = async (patch) => {
    const next = { ...settings, ...patch }, ticket = epoch.current;
    setSettings(next);
    if (selectedGame !== activeGame) return;
    applied.current = next;
    setSettingsBusy(true);
    try {
      if (('voice' in patch && patch.voice !== settings.voice) || ('custom_pack' in patch && patch.custom_pack !== settings.custom_pack) || ('voice_packs' in patch && JSON.stringify(patch.voice_packs) !== JSON.stringify(settings.voice_packs))) {
        // Another voice is another set of clips: fetched now when the spotter is on, at the next start otherwise.
        await library.trackSelection(async () => {
        if (enabled) {
          const response = await fetch(`${apiBase}/api/live-coach/spotter/prepare`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...next, wall_alerts: output.current.wallAlerts }) });
          const manifest = await response.json();
          if (!response.ok || !manifest.clips?.test?.audio_base64) throw new Error(manifest.detail || '이 목소리의 스포터 음성을 준비하지 못했습니다.');
          if (!mounted.current || ticket !== epoch.current) return;
          clips.current = manifest.clips;
        } else {
          clips.current = null;
          const response = await fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...next, wall_alerts: output.current.wallAlerts }) });
          const result = await response.json();
          if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '목소리 선택을 적용하지 못했습니다.');
        }
        });
        setVoice(next.custom_pack || next.voice);
      } else if (enabled) {
        const response = await fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...next, wall_alerts: output.current.wallAlerts }) });
        if (!response.ok) throw new Error('스포터 설정을 적용하지 못했습니다.');
      }
      if (!mounted.current || ticket !== epoch.current) return;
      setSettings(next); setError('');
    } catch (failure) {
      if (mounted.current && ticket === epoch.current) { setSettings(settings); setError(failure.message); }
    }
    finally { if (mounted.current) setSettingsBusy(false); }
  };
  useEffect(() => {
    if (previousGame.current === activeGame) return;
    previousGame.current = activeGame;
    cancelRender('game_changed');
    const ticket = ++epoch.current;
    setReadyGame(null); clips.current = null; state.current = { active: false };
    overlapPending.current = null; radioQueue.current.clear(); heard.current.clear(); rendered.current.clear(); playbackFailures.current.clear();
    clearTimeout(release.current); cancelPriority?.(); setView(null);
    const apply = async () => {
      setSettingsBusy(true);
      try {
        const response = await library.trackSelection(() => fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...applied.current, wall_alerts: output.current.wallAlerts }) }));
        if (!response.ok) throw new Error('스포터 설정을 적용하지 못했습니다.');
        if (!mounted.current || ticket !== epoch.current) return;
        if (enabled) {
          const prepared = await fetch(`${apiBase}/api/live-coach/spotter/prepare`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...applied.current, wall_alerts: output.current.wallAlerts }) });
          const manifest = await prepared.json();
          if (!prepared.ok || !manifest.clips?.test?.audio_base64) throw new Error('스포터 음성을 확인하지 못했습니다.');
          if (!mounted.current || ticket !== epoch.current) return;
          clips.current = manifest.clips;
        }
        if (mounted.current && ticket === epoch.current) { setVoice(applied.current.custom_pack || applied.current.voice); setReadyGame(activeGame); setError(''); }
      } catch (failure) {
        if (mounted.current && ticket === epoch.current) { setError(failure.message); setEnabled(false); }
      } finally { if (mounted.current && ticket === epoch.current) { setSettingsBusy(false); setPreparing(false); } }
    };
    apply();
    // Enabling and edits use their own explicit handlers; this effect resends only on a game transition.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeGame, apiBase, cancelPriority]);
  const announce = useCallback(async (key, valid, variant = null, onStatus, audioKey = null) => {
    if (output.current.standby) return false;
    if (key !== 'test' && !voiceEventAllowed({ key }, applied.current.alerts)) return false;
    const selected = overlapClip(clips.current, key, audioKey, variant);
    const audio = selected?.audio_base64;
    const settings = output.current;
    const event = { id: `${state.current.run_id || 'local'}:overlap:${Date.now()}:${key}`, run_id: state.current.run_id,
      backend_version: state.current.backend_version, key, text: selected?.text };
    const status = (value, reason) => { logEvent(event, value, reason); onStatus?.(value, reason); };
    if (audio && !settings.muted && settings.volume > 0) {
      // Connection status is informational: skip it if any sentence is playing.
      // No delayed notice survives a reconnect; overlap warnings retain priority 3.
      try { return await settings.play(audio, 'audio/mpeg', { priority: key === 'unavailable' ? 1 : 3,
        interrupt: key !== 'unavailable', whilePlaying: key === 'unavailable' ? valid : undefined,
        channel: 'spotter', valid, onStatus: status }); }
      catch { status('failed', 'playback_error'); return false; }
    }
    status(audio ? 'muted' : 'failed', audio ? 'volume_or_mute' : 'missing_clip');
    return false;
  }, [logEvent]);
  const pumpOverlap = useCallback(() => {
    if (output.current.standby) return;
    const pending = overlapPending.current;
    if (!pending || pending.inFlight || pending.delivered || !pending.valid() || Date.now() < pending.retryAt) return;
    if (output.current.muted || output.current.volume === 0) {
      if (!pending.muteLogged) {
        pending.muteLogged = true; announce(pending.key, pending.valid, pending.variant, undefined, pending.audioKey);
        if (mounted.current) setView((v) => ({ ...v, delivery_status: 'muted' }));
      }
      return;
    }
    pending.muteLogged = false; pending.inFlight = true; pending.status = null; pending.attempts += 1;
    if (mounted.current) setView((v) => ({ ...v, delivery_status: 'pending' }));
    const retry = () => {
      pending.delivered = false;
      pending.retryAt = Date.now() + Math.min(2000, 250 * 2 ** Math.min(3, pending.attempts - 1));
    };
    announce(pending.key, pending.valid, pending.variant, (status) => {
      if (overlapPending.current !== pending || !mounted.current) return;
      pending.status = status;
      if (status === 'completed' || status === 'started') pending.delivered = true;
      else if (['interrupted', 'failed', 'cancelled'].includes(status)) retry();
      setView((v) => ({ ...v, delivery_status: status }));
    }, pending.audioKey).then((started) => {
      if (overlapPending.current !== pending || !mounted.current) return;
      pending.inFlight = false;
      if (started && !['failed', 'cancelled', 'interrupted'].includes(pending.status)) {
        pending.delivered = true;
        if (!pending.status) setView((v) => ({ ...v, delivery_status: 'started' }));
      } else retry();
    });
  }, [announce]);
  const pumpRadio = useCallback(async () => {
    const settings = output.current, current = state.current;
    // A standby tab neither renders nor acknowledges: the speaking tab owns every call.
    if (settings.standby) return;
    if (!current.radio?.connected || occupied(current, applied.current.alerts) || Date.now() - lastFrame.current > 600) return;
    const events = [...radioQueue.current.values()].filter((event) => !heard.current.has(event.id)
      && !processing.current.has(event.id) && Date.now() < event.local_expires
      && Date.now() >= (playbackFailures.current.get(event.id)?.retryAt || 0)
      && voiceEventAllowed(event, applied.current.alerts, settings.wallAlerts)
      && (!event.immediate || !closeBattle(current))
      // The corner drill's warm-up voice is part of a drill the driver started: always played.
      && (settings.wallAlerts || event.key?.startsWith('drill_') || settings.level === 'all' || !['pace_detail', 'incident_coaching', 'corner_focus', 'straight_brief'].includes(event.key))
      && (settings.wallAlerts || settings.specialists || event.engineer === 'spotter' || event.key?.startsWith('drill_')))
      .sort((a, b) => b.priority - a.priority || Number(Boolean(b.immediate)) - Number(Boolean(a.immediate)) || a.serial - b.serial);
    const workloadFor = (e) => e.brief ? (state.current.radio?.brief_quiet ?? state.current.radio?.quiet) : state.current.radio?.quiet;
    // The server applies the driver's hold (no start while braking/steering or shortly before it).
    const quietFor = (e) => e.immediate || workloadFor(e);
    // Prepare a short call before its driving window; cloud latency must not
    // consume the only quiet moment after crossing the line.
    const event = events.find((e) => e.priority >= 2 || (!settings.busy && !settings.audioBusy?.()
      && ((!e.cached && !rendered.current.has(e.id)) || quietFor(e)) && (e.immediate || Date.now() - lastCall.current > 1200)));
    if (!event || (!event.cached && rendering.current)) return;
    const ticket = epoch.current;
    const revision = alertRevision.current;
    const valid = () => mounted.current && !output.current.standby && ticket === epoch.current && state.current.radio?.connected
      && revision === alertRevision.current && voiceEventAllowed(event, applied.current.alerts, output.current.wallAlerts)
      && state.current.radio.run_id === event.run_id && !occupied(state.current, applied.current.alerts)
      && (!event.immediate || !closeBattle(state.current))
      && (output.current.wallAlerts || output.current.specialists || event.engineer === 'spotter' || event.key?.startsWith('drill_'))
      && (event.priority >= 2 || !output.current.busy)
      && radioQueue.current.has(event.id) && Date.now() < event.local_expires && Date.now() - lastFrame.current < 600;
    processing.current.add(event.id);
    let flight;
    try {
      let audio, droppedParts, mime = 'audio/mpeg', text = event.text;
      if (event.cached) {
        const clip = clips.current?.[event.key];
        const variant = clip?.variants?.[event.variant] || clip;
        audio = variant?.audio_base64; text = variant?.text || text;
      } else if (rendered.current.has(event.id)) {
        ({ audio, mime, droppedParts } = rendered.current.get(event.id));
      } else if (!settings.muted && settings.volume > 0) {
        const controller = new AbortController();
        let timer, cancel;
        const cancelled = new Promise(resolve => { cancel = reason => resolve({ cancelled: reason }); });
        flight = { event, cancel: reason => {
          clearTimeout(timer); controller.abort(); cancel(reason);
          if (rendering.current === flight) rendering.current = null;
        } };
        rendering.current = flight;
        timer = setTimeout(() => flight.cancel('timeout'), Math.max(0, Math.min(RADIO_RENDER_TIMEOUT_MS, event.local_expires - Date.now())));
        const request = async () => {
          const response = await fetch(`${apiBase}/api/live-coach/spotter/radio/${encodeURIComponent(event.id)}/render`, { method: 'POST', signal: controller.signal });
          return { response, result: await response.json() };
        };
        let renderedResponse;
        try { renderedResponse = await Promise.race([request(), cancelled]); }
        finally { clearTimeout(timer); }
        if (renderedResponse.cancelled) {
          if (renderedResponse.cancelled === 'timeout' && valid()) {
            logEvent(event, 'failed', 'audio_preparation_timeout'); heard.current.add(event.id);
            setError('음성 준비 응답이 지연되어 이번 안내를 건너뛰었습니다. 다음 안내는 다시 시도합니다.');
          }
          return;
        }
        if (!valid()) return;
        const { response, result } = renderedResponse;
        if (!response.ok) throw new Error(result.detail || '경기 안내 음성 준비 실패');
        if (result.missing_prepared_audio) {
          logEvent(event, 'cancelled', 'missing_prepared_audio'); heard.current.add(event.id); return;
        }
        if (result.busy || !result.event) return;
        // Only retained whole sentences count toward the multipart playback rate.
        audio = result.audio_parts?.length ? result.audio_parts : result.audio_base64; mime = result.audio_mime || 'audio/mpeg';
        droppedParts = result.dropped_parts;
        if (audio) rendered.current.set(event.id, { audio, mime, droppedParts });
      }
      if (!valid()) return;
      if (event.priority < 2 && (output.current.busy || !quietFor(event))) return;
      const silent = output.current.muted || output.current.volume === 0;
      if (!silent && !audio) return;
      const acknowledge = () => fetch(`${apiBase}/api/live-coach/spotter/radio/${encodeURIComponent(event.id)}/ack`, { method: 'POST' }).catch(() => {});
      let began = false;
      const started = silent || await output.current.play(audio, mime, {
        channel: event.engineer === 'spotter' ? 'spotter' : 'pitwall',
        // Keep the formation briefing at the pace chosen by listening.
        rate: event.key === 'formation_brief' ? DEFAULT_RATE : undefined,
        priority: event.priority, interrupt: event.priority >= 2,
        valid: () => valid() && (event.priority >= 2 || quietFor(event)),
        startDeadline: event.immediate ? event.local_expires : undefined,
        workloadValid: event.immediate ? undefined : event.key.startsWith('pit_box_') || event.key.startsWith('traffic_') ? valid : event.priority < 2 ? () => workloadFor(event) && !occupied(state.current, applied.current.alerts)
          && Date.now() - lastFrame.current < 600 : undefined,
        // Nothing here is cut once it has started: pit-box distances and traffic calls live for a
        // second or two, less than their own sentence, and were never heard to the end.
        onStatus: (status, reason) => {
          logEvent(event, status, reason, droppedParts);
          if (status === 'started') { began = true; playbackFailures.current.delete(event.id); }
          if (!began && status === 'failed' && ticket === epoch.current && radioQueue.current.has(event.id)) {
            const attempts = (playbackFailures.current.get(event.id)?.attempts || 0) + 1;
            // Refresh failed media, then retry on fresh frames after 0.5/1 seconds.
            // Each event owns its budget; other calls and safety warnings stay free.
            rendered.current.delete(event.id);
            playbackFailures.current.set(event.id, { attempts,
              retryAt: attempts >= MAX_PLAYBACK_ATTEMPTS ? Infinity : Date.now() + 500 * 2 ** (attempts - 1) });
            if (attempts >= MAX_PLAYBACK_ATTEMPTS) {
              heard.current.add(event.id);
              if (heard.current.size > 256) heard.current.delete(heard.current.values().next().value);
              acknowledge();
            }
          }
          if (!began || !['completed', 'interrupted', 'failed'].includes(status)) return;
          const resumes = resumed.current.get(event.id) || 0;
          if (!event.immediate && status === 'interrupted' && RESUMABLE.includes(reason) && resumes < MAX_RESUMES) {
            // Cut off by a more urgent call or by the driving: keep it pending on the server
            // and say it again from the start, if its condition still holds by then.
            resumed.current.set(event.id, resumes + 1);
            heard.current.delete(event.id);
            if (resumed.current.size > 64) resumed.current.delete(resumed.current.keys().next().value);
          } else acknowledge();
        },
      });
      if (!started || !valid()) return;
      heard.current.add(event.id);
      if (silent) logEvent(event, 'muted', 'volume_or_mute', droppedParts);
      if (heard.current.size > 256) heard.current.delete(heard.current.values().next().value);
      lastCall.current = Date.now();
      setView((v) => ({ ...v, last_text: text, last_spoken_text: event.spoken_text || text, last_engineer: event.engineer }));
      if (silent) acknowledge();
    } catch (failure) {
      if (valid()) {
        logEvent(event, 'failed', 'audio_preparation');
        setError(failure.message); heard.current.add(event.id);
        setView((v) => ({ ...v, last_text: event.text, last_spoken_text: null, last_engineer: event.engineer }));
      }
    } finally { processing.current.delete(event.id); if (flight && rendering.current === flight) rendering.current = null; }
  }, [apiBase, logEvent]);
  useEffect(() => {
    if (!enabled || readyGame !== activeGame) return undefined;
    let socket, retry, stopped = false;
    const ticket = epoch.current;
    let serverOffset = null;
    let lastSerial = -1;
    let drillSerial = null;                 // the corner drill's beeps (live_drill): only those newer than the first frame
    const lost = () => {
      cancelRender('connection_lost');
      const wasActive = state.current.active || state.current.radio?.connected;
      state.current = { active: false };
      overlapPending.current = null;
      for (const event of radioQueue.current.values()) if (!output.current.standby && !heard.current.has(event.id)) logEvent(event, 'cancelled', 'connection_lost');
      radioQueue.current.clear(); rendered.current.clear();
      // Side-by-side calls stop with the data; a sentence already being spoken (the race result
      // as the driver leaves the car) is allowed to finish.
      clearTimeout(release.current); cancelPriority?.(3); setPriorityGate?.(false);
      if (!stopped) {
        setView({ active: false, state_label: '스포터 연결 대기' });
        if (wasActive) announce('unavailable', () => !stopped && ticket === epoch.current && !state.current.active);
      }
    };
    const connect = () => {
      const url = new URL(`${apiBase}/api/live-coach/spotter/events`);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url.href); lastFrame.current = Date.now(); serverOffset = null; lastSerial = -1;
      socket.onmessage = ({ data }) => {
        if (stopped || ticket !== epoch.current) return;
        let frame;
        try { frame = JSON.parse(data); } catch { return; }
        if (!['ready', 'spotter'].includes(frame.type)) return;
        const now = Date.now();
        if (typeof frame.sent_at === 'number') {
          const offset = now - frame.sent_at * 1000;
          serverOffset = serverOffset == null ? offset : Math.min(serverOffset, offset);
          if (offset - serverOffset > 500) { lost(); return; }
        }
        lastFrame.current = now;
        const previous = state.current;
        if (rendering.current && rendering.current.event.run_id !== frame.radio?.run_id) cancelRender('session_changed');
        // A brief raw 'clear' candidate must not cut off a confirmed overlap call.
        const replaced = (previous.active && !frame.active) || previous.run_id !== frame.run_id || previous.state !== frame.state
          || previous.profile !== frame.profile;
        if (replaced || (frame.raw_state > 1 && frame.raw_state !== frame.state && previous.raw_state !== frame.raw_state)) cancelPriority?.(3);
        if (replaced) overlapPending.current = null;
        if (previous.run_id !== frame.run_id && previous.run_id && !frame.radio?.connected) { cancelPriority?.(3); setPriorityGate?.(false); }
        state.current = frame;
        // Shadow diagnostics have no render, play, acknowledgement or priority-gate path.
        if (!output.current.standby && output.current.recordDelivery?.observeBanter) {
          for (const candidate of frame.banter_shadow || []) {
            if (candidate.run_id !== frame.radio?.run_id || shadowSeen.current.has(candidate.candidate_id)) continue;
            shadowSeen.current.add(candidate.candidate_id);
            if (shadowSeen.current.size > 128) shadowSeen.current.delete(shadowSeen.current.values().next().value);
            try {
              output.current.recordDelivery.observeBanter(banterObservation(candidate, frame, output.current,
                rendering.current || overlapPending.current || radioQueue.current.size));
            } catch { /* Optional diagnostics cannot interrupt existing radio delivery. */ }
          }
        }
        // Calls run from state.current; the panel only needs changes and a slow refresh.
        if (!output.current.standby && output.current.recordDelivery?.observeOval) {
          for (const candidate of frame.oval_shadow || []) {
            if (candidate.run_id !== frame.radio?.run_id || shadowSeen.current.has(candidate.candidate_id)) continue;
            shadowSeen.current.add(candidate.candidate_id);
            if (shadowSeen.current.size > 128) shadowSeen.current.delete(shadowSeen.current.values().next().value);
            try {
              output.current.recordDelivery.observeOval(banterObservation(candidate, frame, output.current,
                rendering.current || overlapPending.current || radioQueue.current.size));
            } catch { /* Diagnostics cannot interrupt radio delivery. */ }
          }
        }
        const changed = replaced || frame.type === 'ready' || previous.raw_state !== frame.raw_state || previous.occupied !== frame.occupied
          || frame.events?.length > 0 || previous.radio?.connected !== frame.radio?.connected
          || (previous.radio?.events?.length || 0) !== (frame.radio?.events?.length || 0);
        if (changed || now - lastView.current >= 250) {
          lastView.current = now;
          setView((current) => ({ ...frame, last_text: current?.last_text, last_spoken_text: current?.last_spoken_text, last_engineer: current?.last_engineer,
            delivery_status: replaced ? null : current?.delivery_status }));
        }
        const nextQueue = new Map((frame.radio?.events || []).map((event) => [event.id, { ...event,
          backend_version: event.backend_version || frame.backend_version || null,
          local_expires: Math.min(radioQueue.current.get(event.id)?.local_expires ?? Infinity,
            now + Math.min(120000, event.expires_in_ms) - (typeof frame.sent_at === 'number' ? Math.max(0, now - frame.sent_at * 1000 - serverOffset) : 0)) }]));
        if (rendering.current && !nextQueue.has(rendering.current.event.id)) cancelRender('condition_or_session_changed');
        for (const event of radioQueue.current.values()) {
          if (!output.current.standby && !nextQueue.has(event.id) && !heard.current.has(event.id)) {
            const expired = now >= event.local_expires;
            logEvent(event, expired ? 'expired' : 'cancelled', expired ? 'no_playback_window' : 'condition_or_session_changed');
          }
        }
        radioQueue.current = nextQueue;
        for (const id of rendered.current.keys()) if (!radioQueue.current.has(id)) rendered.current.delete(id);
        for (const id of playbackFailures.current.keys()) if (!radioQueue.current.has(id)) playbackFailures.current.delete(id);
        clearTimeout(release.current);
        if (occupied(frame, applied.current.alerts)) setPriorityGate?.(true);
        else if (occupied(previous, applied.current.alerts) || frame.events?.length) {
          release.current = setTimeout(() => { if (!occupied(state.current, applied.current.alerts)) setPriorityGate?.(false); }, 700);
        } else setPriorityGate?.(false);
        pumpRadio();
        if (frame.drill) {
          if (drillSerial != null && frame.drill.serial > drillSerial) {
            for (const event of frame.drill.events || []) {
              const sound = event.serial > drillSerial ? eventSound(event) : null;
              if (sound) playBeep(sound, { outputId: outputRef.current, volume: volumeRef.current, muted: mutedRef.current });
            }
          }
          drillSerial = frame.drill.serial ?? drillSerial;
        }
        if (!frame.active) {
          if (previous.active && !frame.radio?.connected) announce('unavailable', () => !stopped && !state.current.active && ticket === epoch.current);
          return;
        }
        const events = frame.type === 'ready' && frame.current_key
          ? [{ key: frame.current_key, audio_key: frame.current_audio_key, state: frame.state, run_id: frame.run_id, expires_in_ms: 600 }]
          : (frame.events || []).filter((event) => event.serial > lastSerial);
        lastSerial = Math.max(lastSerial, frame.serial ?? -1);
        // The newest authoritative state replaces pending/playing old calls.
        const event = events.at(-1);
        if (!event || !voiceEventAllowed(event, applied.current.alerts) || event.state !== frame.raw_state || event.state !== frame.state
          || event.run_id !== frame.run_id || !(event.expires_in_ms > 0)) { pumpOverlap(); return; }
        const expires = now + Math.min(600, event.expires_in_ms);
        setView((current) => ({ ...current, last_text: overlapClip(clips.current, event.key, event.audio_key, event.variant)?.text || event.text, last_spoken_text: null, last_engineer: 'spotter' }));
        const pending = { key: event.key, audioKey: event.audio_key, variant: event.variant, delivered: false, inFlight: false,
          retryAt: 0, attempts: 0, valid: () => overlapPending.current === pending && !stopped && ticket === epoch.current && state.current.active
            && voiceEventAllowed(event, applied.current.alerts)
            && state.current.state === event.state && state.current.run_id === event.run_id
            && state.current.profile === frame.profile
            && (state.current.raw_state === event.state || (event.state > 1 && state.current.raw_state === 1))
            && (event.state > 1 || Date.now() < expires) && Date.now() - lastFrame.current < 600 };
        overlapPending.current = pending;
        pumpOverlap();
      };
      socket.onclose = () => { if (!stopped) { lost(); retry = setTimeout(connect, 1000); } };
      socket.onerror = () => socket.close();
    };
    connect();
    const watchdog = setInterval(() => {
      if (Date.now() - lastFrame.current > 600) { lost(); socket?.close(); }
      else pumpOverlap();
    }, 100);
    return () => { stopped = true; cancelRender('connection_closed'); overlapPending.current = null; clearTimeout(retry); clearTimeout(release.current); clearInterval(watchdog); socket?.close(); cancelPriority?.(); };
  }, [enabled, readyGame, activeGame, apiBase, announce, cancelPriority, setPriorityGate, pumpRadio, pumpOverlap, logEvent, cancelRender]);
  const test = () => {
    if (!state.current.occupied) announce('test', () => !state.current.occupied);
  };
  // The spotter speaks from this tab: closing or reloading it while on asks first (the browser shows its own wording).
  useEffect(() => {
    if (!enabled || readyGame !== activeGame) return undefined;
    const ask = (event) => { event.preventDefault(); event.returnValue = ''; return ''; };
    window.addEventListener('beforeunload', ask);
    return () => window.removeEventListener('beforeunload', ask);
  }, [enabled, readyGame, activeGame]);

  return { enabled, preparing, view, error, voice, settings, settingsBusy, library, changeSettings, toggle: enable, test };
}
