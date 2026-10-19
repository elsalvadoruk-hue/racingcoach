import { useCallback, useEffect, useRef, useState } from 'react';
import { RESUMABLE, MAX_RESUMES, DEFAULT_RATE } from './voiceAudio';
import { eventSound, playBeep } from './drillBeeps';

const SETTINGS_KEY = 'iracing.spotter.settings';
export const SPOTTER_VOICES = [['Fenrir', '남성 · 기본'], ['Algieba', '남성'], ['Schedar', '남성'], ['Aoede', '여성']];
const DEFAULT_SETTINGS = { voice: 'Fenrir', formation_calls: 5, repeat_seconds: 3, clear_delay: .15, traffic_enabled: true, pit_box_enabled: false };
const savedSettings = () => {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    return { voice: SPOTTER_VOICES.some(([name]) => name === saved?.voice) ? saved.voice : 'Fenrir',
      formation_calls: Number.isInteger(saved?.formation_calls) && saved.formation_calls >= 0 && saved.formation_calls <= 8 ? saved.formation_calls : 5,
      repeat_seconds: [2, 3, 5].includes(saved?.repeat_seconds) ? saved.repeat_seconds : 3,
      clear_delay: [.15, .3, .5].includes(saved?.clear_delay) ? saved.clear_delay : .15,
      traffic_enabled: saved?.traffic_enabled !== false, pit_box_enabled: saved?.pit_box_enabled === true };
  } catch { return DEFAULT_SETTINGS; }
};

export default function useSpotter(apiBase, { play, setPriorityGate, cancelPriority, audioBusy, muted, volume, outputId }, { busy = false, specialists = true, level = 'all', recordDelivery } = {}) {
  // The drill beeps follow the radio's output device and volume without reconnecting the socket.
  const outputRef = useRef(outputId); outputRef.current = outputId;
  const volumeRef = useRef(volume); volumeRef.current = volume ?? 1;
  const mutedRef = useRef(muted); mutedRef.current = Boolean(muted);
  const [enabled, setEnabled] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [view, setView] = useState(null);
  const [error, setError] = useState('');
  const [settings, setSettings] = useState(savedSettings), [settingsBusy, setSettingsBusy] = useState(false);
  const [voice, setVoice] = useState(settings.voice);
  const overlapPending = useRef(null);
  const clips = useRef(null), epoch = useRef(0), mounted = useRef(true);
  const state = useRef({ active: false }), lastFrame = useRef(0), release = useRef(null);
  const output = useRef({ play, muted, volume, busy, specialists, audioBusy, level, recordDelivery });
  output.current = { play, muted, volume, busy, specialists, audioBusy, level, recordDelivery };
  const logEvent = useCallback((event, status, reason = '') => {
    const radio = state.current.radio || {};
    output.current.recordDelivery?.({ event_id: event.id, run_id: event.run_id || '', key: event.key,
      engineer: event.engineer || 'spotter', text: event.spoken_text || event.text || '', session_time: radio.session_time,
      lap_number: radio.lap_number, distance_pct: radio.distance_pct }, status, reason);
  }, []);
  const radioQueue = useRef(new Map()), heard = useRef(new Set()), rendering = useRef(false), processing = useRef(new Set()), lastCall = useRef(0), resumed = useRef(new Map());
  const rendered = useRef(new Map()), lastView = useRef(0);
  const disable = useCallback(() => {
    epoch.current += 1;
    state.current = { active: false };
    overlapPending.current = null;
    for (const event of radioQueue.current.values()) if (!heard.current.has(event.id)) logEvent(event, 'cancelled', 'spotter_disabled');
    radioQueue.current.clear(); heard.current.clear(); rendered.current.clear();
    clearTimeout(release.current);
    cancelPriority?.();
    setEnabled(false); setPreparing(false); setView(null);
  }, [cancelPriority, logEvent]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; epoch.current += 1; overlapPending.current = null; clearTimeout(release.current); cancelPriority?.(); };
  }, [cancelPriority]);
  const enable = async () => {
    if (enabled || preparing) { disable(); return; }
    const ticket = ++epoch.current;
    setError(''); setPreparing(true);
    try {
      if (!clips.current) {
        const response = await fetch(`${apiBase}/api/live-coach/spotter/prepare`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
        const manifest = await response.json();
        if (!response.ok) throw new Error(manifest.detail || '스포터 음성 준비 실패');
        if (!manifest.clips?.test?.audio_base64) throw new Error('스포터 음성을 확인하지 못했습니다.');
        if (!mounted.current || ticket !== epoch.current) return;
        clips.current = manifest.clips;
        setVoice((manifest.voice || 'Fenrir').replace('ko-KR-Chirp3-HD-', ''));
      } else {
        const response = await fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
        if (!response.ok) throw new Error('스포터 설정을 적용하지 못했습니다.');
      }
      if (mounted.current && ticket === epoch.current) { setEnabled(true); setPreparing(false); }
    } catch (failure) {
      if (mounted.current && ticket === epoch.current) { setError(failure.message); setPreparing(false); }
    }
  };
  const changeSettings = async (patch) => {
    const next = { ...settings, ...patch }, ticket = epoch.current;
    setSettings(next); setSettingsBusy(true);
    try {
      if ('voice' in patch && patch.voice !== settings.voice) {
        // Another voice is another set of clips: fetched now when the spotter is on, at the next start otherwise.
        if (enabled) {
          const response = await fetch(`${apiBase}/api/live-coach/spotter/prepare`, { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(next) });
          const manifest = await response.json();
          if (!response.ok || !manifest.clips?.test?.audio_base64) throw new Error(manifest.detail || '이 목소리의 스포터 음성을 준비하지 못했습니다.');
          if (!mounted.current || ticket !== epoch.current) return;
          clips.current = manifest.clips;
        } else clips.current = null;
        setVoice(next.voice);
      } else if (enabled) {
        const response = await fetch(`${apiBase}/api/live-coach/spotter/settings`, { method: 'PUT',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(next) });
        if (!response.ok) throw new Error('스포터 설정을 적용하지 못했습니다.');
      }
      if (!mounted.current || ticket !== epoch.current) return;
      setSettings(next); setError('');
      try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(next)); } catch { /* Settings still apply for this page. */ }
    } catch (failure) {
      if (mounted.current && ticket === epoch.current) { setSettings(settings); setError(failure.message); }
    }
    finally { if (mounted.current) setSettingsBusy(false); }
  };
  const announce = useCallback(async (key, valid, variant = null, onStatus) => {
    const choices = clips.current?.[key]?.variants;
    const selected = choices?.length ? choices[(variant ?? 0) % choices.length] : clips.current?.[key];
    const audio = selected?.audio_base64;
    const settings = output.current;
    const event = { id: `${state.current.run_id || 'local'}:overlap:${Date.now()}:${key}`, run_id: state.current.run_id, key, text: selected?.text };
    const status = (value, reason) => { logEvent(event, value, reason); onStatus?.(value, reason); };
    if (audio && !settings.muted && settings.volume > 0) {
      try { return await settings.play(audio, 'audio/mpeg', { priority: 3, valid, onStatus: status }); }
      catch { status('failed', 'playback_error'); return false; }
    }
    status(audio ? 'muted' : 'failed', audio ? 'volume_or_mute' : 'missing_clip');
    return false;
  }, [logEvent]);
  const pumpOverlap = useCallback(() => {
    const pending = overlapPending.current;
    if (!pending || pending.inFlight || pending.delivered || !pending.valid() || Date.now() < pending.retryAt) return;
    if (output.current.muted || output.current.volume === 0) {
      if (!pending.muteLogged) {
        pending.muteLogged = true; announce(pending.key, pending.valid, pending.variant);
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
    }).then((started) => {
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
    if (!current.radio?.connected || current.occupied || Date.now() - lastFrame.current > 600) return;
    const events = [...radioQueue.current.values()].filter((event) => !heard.current.has(event.id)
      && !processing.current.has(event.id) && Date.now() < event.local_expires
      // The corner drill's warm-up voice is part of a drill the driver started: always played.
      && (event.key?.startsWith('drill_') || settings.level === 'all' || !['pace_detail', 'incident_coaching', 'corner_focus', 'straight_brief'].includes(event.key))
      && (settings.specialists || event.engineer === 'spotter' || event.key?.startsWith('drill_')))
      .sort((a, b) => b.priority - a.priority || a.serial - b.serial);
    const workloadFor = (e) => e.brief ? (state.current.radio?.brief_quiet ?? state.current.radio?.quiet) : state.current.radio?.quiet;
    // The server applies the driver's hold (no start while braking/steering or shortly before it).
    const quietFor = (e) => workloadFor(e);
    // Prepare a short call before its driving window; cloud latency must not
    // consume the only quiet moment after crossing the line.
    const event = events.find((e) => e.priority >= 2 || (!settings.busy && !settings.audioBusy?.()
      && ((!e.cached && !rendered.current.has(e.id)) || quietFor(e)) && Date.now() - lastCall.current > 1200));
    if (!event || (!event.cached && rendering.current)) return;
    const ticket = epoch.current;
    const valid = () => mounted.current && ticket === epoch.current && state.current.radio?.connected
      && state.current.radio.run_id === event.run_id && !state.current.occupied
      && (output.current.specialists || event.engineer === 'spotter')
      && (event.priority >= 2 || !output.current.busy)
      && radioQueue.current.has(event.id) && Date.now() < event.local_expires && Date.now() - lastFrame.current < 600;
    processing.current.add(event.id);
    try {
      let audio, mime = 'audio/mpeg', text = event.text;
      if (event.cached) {
        const clip = clips.current?.[event.key];
        const variant = clip?.variants?.[event.variant] || clip;
        audio = variant?.audio_base64; text = variant?.text || text;
      } else if (rendered.current.has(event.id)) {
        ({ audio, mime } = rendered.current.get(event.id));
      } else if (!settings.muted && settings.volume > 0) {
        rendering.current = true;
        const response = await fetch(`${apiBase}/api/live-coach/spotter/radio/${encodeURIComponent(event.id)}/render`, { method: 'POST' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || '경기 안내 음성 준비 실패');
        if (result.busy || !result.event) return;
        // Several whole sentences are played back to back as one call.
        audio = result.audio_parts?.length > 1 ? result.audio_parts : result.audio_base64; mime = result.audio_mime || 'audio/mpeg';
        if (audio) rendered.current.set(event.id, { audio, mime });
      }
      if (!valid()) return;
      if (event.priority < 2 && (output.current.busy || !quietFor(event))) return;
      const silent = output.current.muted || output.current.volume === 0;
      if (!silent && !audio) return;
      const acknowledge = () => fetch(`${apiBase}/api/live-coach/spotter/radio/${encodeURIComponent(event.id)}/ack`, { method: 'POST' }).catch(() => {});
      let began = false;
      const started = silent || await output.current.play(audio, mime, {
        priority: event.priority, interrupt: event.priority >= 2,
        // The speed setting is for the race; the formation lap briefing keeps its unhurried pace.
        rate: event.key === 'formation_brief' ? DEFAULT_RATE : undefined,
        valid: () => valid() && (event.priority >= 2 || quietFor(event)),
        workloadValid: event.key.startsWith('pit_box_') || event.key.startsWith('traffic_') ? valid : event.priority < 2 ? () => workloadFor(event) && !state.current.occupied
          && Date.now() - lastFrame.current < 600 : undefined,
        // Nothing here is cut once it has started: pit-box distances and traffic calls live for a
        // second or two, less than their own sentence, and were never heard to the end.
        onStatus: (status, reason) => {
          logEvent(event, status, reason);
          if (status === 'started') began = true;
          if (!began || !['completed', 'interrupted', 'failed'].includes(status)) return;
          const resumes = resumed.current.get(event.id) || 0;
          if (status === 'interrupted' && RESUMABLE.includes(reason) && resumes < MAX_RESUMES) {
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
      if (silent) logEvent(event, 'muted', 'volume_or_mute');
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
    } finally { processing.current.delete(event.id); if (!event.cached) rendering.current = false; }
  }, [apiBase, logEvent]);
  useEffect(() => {
    if (!enabled) return undefined;
    let socket, retry, stopped = false;
    const ticket = epoch.current;
    let serverOffset = null;
    let lastSerial = -1;
    let drillSerial = null;                 // the corner drill's beeps (live_drill): only those newer than the first frame
    const lost = () => {
      const wasActive = state.current.active || state.current.radio?.connected;
      state.current = { active: false };
      overlapPending.current = null;
      for (const event of radioQueue.current.values()) if (!heard.current.has(event.id)) logEvent(event, 'cancelled', 'connection_lost');
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
        // A brief raw 'clear' candidate must not cut off a confirmed overlap call.
        const replaced = (previous.active && !frame.active) || previous.run_id !== frame.run_id || previous.state !== frame.state;
        if (replaced || (frame.raw_state > 1 && frame.raw_state !== frame.state && previous.raw_state !== frame.raw_state)) cancelPriority?.(3);
        if (replaced) overlapPending.current = null;
        if (previous.run_id !== frame.run_id && previous.run_id && !frame.radio?.connected) { cancelPriority?.(3); setPriorityGate?.(false); }
        state.current = frame;
        // Calls run from state.current; the panel only needs changes and a slow refresh.
        const changed = replaced || frame.type === 'ready' || previous.raw_state !== frame.raw_state || previous.occupied !== frame.occupied
          || frame.events?.length > 0 || previous.radio?.connected !== frame.radio?.connected
          || (previous.radio?.events?.length || 0) !== (frame.radio?.events?.length || 0);
        if (changed || now - lastView.current >= 250) {
          lastView.current = now;
          setView((current) => ({ ...frame, last_text: current?.last_text, last_spoken_text: current?.last_spoken_text, last_engineer: current?.last_engineer,
            delivery_status: replaced ? null : current?.delivery_status }));
        }
        const nextQueue = new Map((frame.radio?.events || []).map((event) => [event.id, { ...event,
          local_expires: now + Math.min(120000, event.expires_in_ms) }]));
        for (const event of radioQueue.current.values()) {
          if (!nextQueue.has(event.id) && !heard.current.has(event.id)) {
            const expired = now >= event.local_expires;
            logEvent(event, expired ? 'expired' : 'cancelled', expired ? 'no_playback_window' : 'condition_or_session_changed');
          }
        }
        radioQueue.current = nextQueue;
        for (const id of rendered.current.keys()) if (!radioQueue.current.has(id)) rendered.current.delete(id);
        clearTimeout(release.current);
        if (frame.occupied) setPriorityGate?.(true);
        else if (previous.occupied || frame.events?.length) {
          release.current = setTimeout(() => { if (!state.current.occupied) setPriorityGate?.(false); }, 700);
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
          ? [{ key: frame.current_key, state: frame.state, run_id: frame.run_id, expires_in_ms: 600 }]
          : (frame.events || []).filter((event) => event.serial > lastSerial);
        lastSerial = Math.max(lastSerial, frame.serial ?? -1);
        // The newest authoritative state replaces pending/playing old calls.
        const event = events.at(-1);
        if (!event || event.state !== frame.raw_state || event.state !== frame.state
          || event.run_id !== frame.run_id || !(event.expires_in_ms > 0)) { pumpOverlap(); return; }
        const expires = now + Math.min(600, event.expires_in_ms);
        setView((current) => ({ ...current, last_text: clips.current?.[event.key]?.variants?.[event.variant ?? 0]?.text || clips.current?.[event.key]?.text || event.text, last_spoken_text: null, last_engineer: 'spotter' }));
        overlapPending.current = { key: event.key, variant: event.variant, delivered: false, inFlight: false,
          retryAt: 0, attempts: 0, valid: () => !stopped && ticket === epoch.current && state.current.active
            && state.current.state === event.state && state.current.run_id === event.run_id
            && (state.current.raw_state === event.state || (event.state > 1 && state.current.raw_state === 1))
            && (event.state > 1 || Date.now() < expires) && Date.now() - lastFrame.current < 600 };
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
    return () => { stopped = true; overlapPending.current = null; clearTimeout(retry); clearTimeout(release.current); clearInterval(watchdog); socket?.close(); cancelPriority?.(); };
  }, [enabled, apiBase, announce, cancelPriority, setPriorityGate, pumpRadio, pumpOverlap, logEvent]);
  const test = () => {
    if (!state.current.occupied) announce('test', () => !state.current.occupied);
  };
  // The spotter speaks from this tab: closing or reloading it while on asks first (the browser shows its own wording).
  useEffect(() => {
    if (!enabled) return undefined;
    const ask = (event) => { event.preventDefault(); event.returnValue = ''; return ''; };
    window.addEventListener('beforeunload', ask);
    return () => window.removeEventListener('beforeunload', ask);
  }, [enabled]);

  return { enabled, preparing, view, error, voice, settings, settingsBusy, changeSettings, toggle: enable, test };
}
