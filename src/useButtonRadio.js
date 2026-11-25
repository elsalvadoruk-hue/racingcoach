import { useEffect, useRef } from 'react';

export function buttonSocket(apiBase, capture = false) {
  const url = new URL('/api/live-coach/buttons/input', apiBase);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  if (capture) url.searchParams.set('capture', 'true');
  return new WebSocket(url);
}

export async function buttonRequest(apiBase, path = '', body, signal) {
  const options = body === undefined ? { signal } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  };
  const response = await fetch(`${apiBase}/api/live-coach/buttons${path}`, options);
  if (response.status === 404) throw new Error('앱과 실행창을 닫고 실행.bat로 다시 실행하세요.');
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.detail || '무전 버튼 요청을 처리하지 못했습니다.'), { status: response.status });
  return data;
}

export function useButtonRadio(apiBase, enabled, audio, status, phase, onError, recordDelivery) {
  const latest = useRef(null);
  latest.current = { audio, status, phase, onError, recordDelivery };
  const owner = !audio.standby;
  useEffect(() => {
    if (!enabled || !owner) return undefined;
    let socket, reconnect, pending, closed = false;
    async function answer(event) {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      const until = Date.now() + 5000;
      const delivery = { event_id: 'button:' + event.ticket, run_id: event.run, key: 'radio_button', engineer: 'coach' };
      let reported = false;
      const report = (state, reason = '') => {
        reported = true;
        latest.current.recordDelivery(delivery, state, reason);
      };
      const valid = () => !closed && !controller.signal.aborted && latest.current.phase === 'idle'
        && latest.current.status?.connected !== false && !latest.current.audio.standby;
      try {
        while (valid() && latest.current.audio.isPriorityBlocked?.() && Date.now() < until) {
          await new Promise(resolve => setTimeout(resolve, 75));
        }
        if (!valid()) { report('cancelled', 'state_changed'); return; }
        if (Date.now() >= until) { report('expired', 'start_deadline'); return; }
        const playback = latest.current.audio.applied;
        if (playback?.muted || playback?.volume === 0) { report('muted', 'volume_or_mute'); return; }
        const requested = Date.now();
        const result = await buttonRequest(apiBase, '/play', { ticket: event.ticket }, controller.signal);
        const deadline = Math.min(until, requested + result.remaining_ms);
        const sameRun = () => valid() && (!latest.current.status?.snapshot?.run_id
          || latest.current.status.snapshot.run_id === result.run);
        if (!sameRun()) { report('cancelled', 'state_changed'); return; }
        if (Date.now() >= deadline) { report('expired', 'start_deadline'); return; }
        if (!result.parts.length) {
          report('cancelled', 'audio_unavailable');
          latest.current.onError('지금 읽을 주행 정보나 준비된 음성이 없습니다.');
          return;
        }
        delivery.text = result.spoken.join(' ').slice(0, 2000);
        delivery.dropped_parts = result.missing.length;
        const started = await latest.current.audio.play(result.parts, 'audio/mpeg', {
          priority: 1, valid: sameRun, whilePlaying: sameRun, startDeadline: deadline, signal: controller.signal,
          onStatus: report, confirmAudible: true,
        });
        if (!started && !reported) report('cancelled', 'playback_declined');
      } catch (failure) {
        if (failure.status === 410) { report('expired', 'request_no_longer_current'); return; }
        if (!valid()) { report('cancelled', 'state_changed'); return; }
        report('failed', 'button_request_failed');
        latest.current.onError(failure.message);
      }
    }
    function connect() {
      socket = buttonSocket(apiBase);
      socket.onmessage = ({ data }) => {
        const event = JSON.parse(data);
        if (event.ticket) answer(event);
        else if (event.error) latest.current.onError(event.error);
      };
      socket.onclose = () => { if (!closed) reconnect = setTimeout(connect, 2000); };
    }
    connect();
    return () => { closed = true; pending?.abort(); clearTimeout(reconnect); socket.close(); };
  }, [apiBase, enabled, owner]);
}
