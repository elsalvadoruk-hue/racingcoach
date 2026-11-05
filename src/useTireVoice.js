import { useEffect, useRef } from 'react';
import { createRadioAttempt } from './lib/radioDiagnostics';

// Shares the persistent audio owner with questions, spotter and pit wall. The
// native tire window and the pit-wall page do not have to be visible/enabled.
export default function useTireVoice(apiBase, game, output, { busy = false, recordDelivery } = {}, fetcher = fetch) {
  const live = useRef({}); live.current = { output, busy, recordDelivery };
  useEffect(() => {
    let active = true, timer, flight, generation = 0, view = null, observed = 0;
    const handled = new Set();
    const url = path => `${apiBase}/api/tire-overlay${path}?game=${game}`;
    const valid = event => active && !live.current.output.standby && !live.current.busy
      && view?.voice_enabled && view?.running && view?.voice_event?.id === event.id && view.voice_event.quiet
      && Date.now() - observed < 1800 && Date.now() < event.localExpires
      && !live.current.output.muted && live.current.output.volume > 0
      && !live.current.output.isPriorityBlocked?.();
    const refresh = async () => {
      if (!active) return;
      const controller = new AbortController(); flight = controller;
      const deadline = setTimeout(() => controller.abort(), 4000);
      try {
        const response = await fetcher(url(''), { signal: controller.signal });
        const result = await response.json();
        if (!active || !response.ok || result.version !== 1) return;
        view = result; observed = Date.now();
        if (handled.size > 128) handled.clear();
        const event = view.voice_event && { ...view.voice_event,
          localExpires: Date.now() + Math.max(0, view.voice_event.remaining_s || 0) * 1000 };
        const audio = live.current.output;
        if (!event || handled.has(event.id) || !valid(event) || audio.audioBusy?.() || audio.isPlaying) return;
        const rendered = await fetcher(url(`/voice/${encodeURIComponent(event.id)}/render`), { signal: controller.signal });
        const clip = await rendered.json();
        if (!active || !rendered.ok || !valid(event)) return;
        if (clip.missing_prepared_audio) { handled.add(event.id); return; }
        if (clip.event?.id !== event.id || !clip.event.quiet || !clip.audio_base64) return;
        // Recheck at the server after preparation; a new context or recovered
        // tire state invalidates an event before any sound starts.
        const checked = await fetcher(url(''), { signal: controller.signal });
        const current = await checked.json();
        if (!active || !checked.ok) return;
        view = current; observed = Date.now();
        if (!valid(event) || live.current.output.audioBusy?.()) return;
        handled.add(event.id);
        const diagnostics = createRadioAttempt(game, ++generation);
        await live.current.output.play(clip.audio_base64, clip.audio_mime || 'audio/mpeg', {
          signal: controller.signal, channel: 'pitwall', priority: 0, interrupt: false,
          diagnostics, valid: () => valid(event), workloadValid: () => valid(event),
          onStatus: (status, reason, playback) => {
            if (status === 'started' && active) fetcher(url(`/voice/${encodeURIComponent(event.id)}/ack`), { method: 'POST' }).catch(() => {});
            live.current.recordDelivery?.({ event_id: event.id, run_id: clip.event.run_id || `tire-${game}`, key: `tire_${event.key}`,
              engineer: 'coach', text: event.text, ...(playback ? { playback } : {}) }, status, reason);
          },
        });
      } catch { /* A missing/unavailable tire service must never stall other radio. */ }
      finally {
        clearTimeout(deadline);
        if (flight === controller) flight = null;
        if (active) timer = setTimeout(refresh, 1000);
      }
    };
    refresh();
    return () => { active = false; clearTimeout(timer); flight?.abort(); };
  }, [apiBase, game, fetcher]);
}
