import { useCallback, useEffect, useRef } from 'react';

// Batched independently from the audio path; no recordings or credentials.
export default function useRadioDiagnostics(apiBase) {
  const queue = useRef([]);
  const record = useCallback((event, status, reason = '') => {
    queue.current.push({ ...event, status, reason, at: Date.now() / 1000 });
    if (queue.current.length > 512) queue.current.shift();
  }, []);
  useEffect(() => {
    let running = false;
    const flush = async (keepalive = false) => {
      if (running || !queue.current.length) return;
      running = true;
      const events = queue.current.splice(0, 64);
      try {
        const response = await fetch(`${apiBase}/api/live-coach/radio-delivery`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events }), keepalive,
        });
        if (!response.ok) throw new Error('log unavailable');
      } catch { queue.current.unshift(...events); queue.current = queue.current.slice(-512); }
      finally { running = false; }
    };
    const timer = setInterval(() => flush(), 1000);
    const leaving = () => flush(true);
    window.addEventListener('pagehide', leaving);
    return () => { clearInterval(timer); window.removeEventListener('pagehide', leaving); flush(true); };
  }, [apiBase]);
  return record;
}
