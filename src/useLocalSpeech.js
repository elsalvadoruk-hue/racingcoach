import { useCallback, useEffect, useRef, useState } from 'react';

export async function localSpeechRequest(apiBase, path = '', body, signal) {
  const response = await fetch(`${apiBase}/api/live-coach/local-speech${path}`, body === undefined ? { signal } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
    ...(path === '/deactivate' ? {keepalive:true} : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '로컬 인식기 요청을 완료하지 못했습니다.');
  return data;
}

export default function useLocalSpeech(apiBase, game, standby, onError) {
  const [model, setModelValue] = useState(() => {
    try { return localStorage.getItem('iracing.speech.model.v2') === 'base' ? 'base' : 'sensevoice'; } catch { return 'sensevoice'; }
  });
  const [owner] = useState(() => window.crypto?.randomUUID?.() || `speech-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const [active, setActive] = useState(false);
  const epoch = useRef(0), mounted = useRef(false), claimed = useRef(false), errors = useRef(onError);
  errors.current = onError;
  const stop = useCallback(() => {
    epoch.current += 1;
    if (mounted.current) setActive(false);
    if (!claimed.current) return Promise.resolve();
    claimed.current = false;
    return localSpeechRequest(apiBase, '/deactivate', { owner }).catch(() => {});
  }, [apiBase, owner]);
  useEffect(() => {
    mounted.current = true;
    const leave = () => {
      if (!claimed.current) return;
      claimed.current=false;epoch.current+=1;
      // Release the backend lease without changing the saved on/off preference.
      localSpeechRequest(apiBase,'/deactivate',{owner}).catch(()=>{});
    };
    window.addEventListener('pagehide',leave);
    return () => { window.removeEventListener('pagehide',leave);mounted.current = false; stop(); };
  }, [stop,apiBase,owner]);
  useEffect(() => { stop(); }, [game, standby, stop]);
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => {
      localSpeechRequest(apiBase, '/heartbeat', { owner }).catch(error => {
        stop(); errors.current?.(error.message);
      });
    }, 15000);
    return () => clearInterval(timer);
  }, [apiBase, active, owner, stop]);
  const activate = useCallback(async () => {
    const ticket = ++epoch.current;
    claimed.current = true;
    await localSpeechRequest(apiBase, '/activate', { owner, model });
    if (!mounted.current || ticket !== epoch.current) {
      await localSpeechRequest(apiBase, '/deactivate', { owner });
      throw new Error('마이크 준비를 취소했습니다.');
    }
    setActive(true);
  }, [apiBase, owner, model]);
  const setModel = value => {
    if (!['base', 'sensevoice'].includes(value)) return;
    stop(); setModelValue(value);
    try { localStorage.setItem('iracing.speech.model.v2', value); } catch { /* Optional browser preference. */ }
  };
  return { mode: 'local', model, owner, active, activate, stop, setModel };
}
