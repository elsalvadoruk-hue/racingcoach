import { useCallback, useEffect, useRef } from 'react';
import packageInfo from '../package.json';

// Batched independently from the audio path; no recordings or credentials.
export default function useRadioDiagnostics(apiBase) {
  const queue = useRef([]);
  const shadowQueue = useRef([]);
  const ovalQueue = useRef([]);
  const notifyShadow = useRef(null);
  const notifyStarted = useRef(null);
  const record = useCallback((event, status, reason = '') => {
    queue.current.push({ ...event, frontend_version: packageInfo.version, status, reason, at: Date.now() / 1000 });
    if (queue.current.length > 512) queue.current.shift();
    if (status === 'started' && event.key?.startsWith('control_')) notifyStarted.current?.();
  }, []);
  const observeBanter = useCallback((observation) => {
    shadowQueue.current.push(observation);
    if (shadowQueue.current.length > 64) shadowQueue.current.shift();
    notifyShadow.current?.();
  }, []);
  record.observeBanter = observeBanter;
  record.observeOval = useCallback((observation) => {
    ovalQueue.current.push(observation);
    if (ovalQueue.current.length > 64) ovalQueue.current.shift();
    notifyShadow.current?.();
  }, []);
  useEffect(() => {
    let running = false;
    let urgent = false;
    const flush = async (keepalive = false) => {
      if (running || (!queue.current.length && !shadowQueue.current.length && !ovalQueue.current.length)) return;
      running = true;
      const events = queue.current.splice(0, 64);
      const banter = shadowQueue.current.splice(0, 16);
      const oval = ovalQueue.current.splice(0, 16);
      if (events.some(event => event.status === 'started' && event.key?.startsWith('control_'))) urgent = false;
      let succeeded = false;
      try {
        const response = await fetch(`${apiBase}/api/live-coach/radio-delivery`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events, ...(banter.length ? { banter } : {}), ...(oval.length ? { oval } : {}) }), keepalive,
        });
        if (!response.ok) throw new Error('log unavailable');
        succeeded = true;
      } catch {
        queue.current.unshift(...events); queue.current = queue.current.slice(-512);
        shadowQueue.current.unshift(...banter); shadowQueue.current = shadowQueue.current.slice(-64);
        ovalQueue.current.unshift(...oval); ovalQueue.current = ovalQueue.current.slice(-64);
      }
      finally {
        running = false;
        if (succeeded && (urgent || shadowQueue.current.length || ovalQueue.current.length)) flush();
      }
    };
    notifyStarted.current = () => { urgent = true; flush(); };
    notifyShadow.current = () => flush();
    const timer = setInterval(() => flush(), 1000);
    const leaving = () => flush(true);
    window.addEventListener('pagehide', leaving);
    return () => { notifyStarted.current = null; notifyShadow.current = null; clearInterval(timer); window.removeEventListener('pagehide', leaving); flush(true); };
  }, [apiBase]);
  return record;
}
