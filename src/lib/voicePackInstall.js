import { useCallback, useEffect, useRef, useState } from 'react';

function uploadFile(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', url);
    request.setRequestHeader('Content-Type', 'application/zip');
    request.upload.onprogress = event => onProgress({ stage: 'uploading', done: event.loaded, total: event.lengthComputable ? event.total : null });
    request.upload.onload = () => onProgress({ stage: 'checking', done: 0, total: null });
    request.onerror = () => reject(Error('음성팩 전송 연결이 끊겼습니다. 서버의 작업 상태를 확인해 주세요.'));
    request.onabort = () => reject(Error('음성팩 파일 전송이 중단됐습니다.'));
    request.onload = () => {
      let result;
      try { result = JSON.parse(request.responseText); } catch { reject(Error('설치 응답을 읽지 못했습니다. 작업 상태를 확인해 주세요.')); return; }
      if (request.status < 200 || request.status >= 300) reject(Error(typeof result.detail === 'string' ? result.detail : '음성팩을 설치하지 못했습니다.'));
      else resolve(result);
    };
    request.send(file);
  });
}

export default function useVoicePackInstall(apiBase, reload, post) {
  const [operation, setOperation] = useState(null), [revision, refresh] = useState(0);
  const current = useRef(apiBase); current.current = apiBase;
  const pending = useRef(null), latest = useRef(null), completed = useRef(null);
  const mounted = useRef(true);
  const previousApi = useRef(apiBase);
  const update = useCallback(value => {
    if (mounted.current) setOperation(previous => {
      const next = typeof value === 'function' ? value(previous) : value;
      latest.current = next; return next;
    });
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (previousApi.current !== apiBase) {
      pending.current = null; latest.current = null; completed.current = null;
      setOperation(null); previousApi.current = apiBase;
    }
  }, [apiBase]);
  useEffect(() => {
    let stopped = false, timer;
    const controller = new AbortController();
    const poll = async () => {
      let again = Boolean(pending.current);
      const request = new AbortController(), timeout = setTimeout(() => request.abort(), 5000);
      const abort = () => request.abort(); controller.signal.addEventListener('abort', abort);
      try {
        const response = await fetch(`${apiBase}/api/live-coach/custom-voice-packs/install-status`, { signal: request.signal });
        if (response.status === 404) {
          again = false;
          if (!stopped && current.current === apiBase && pending.current) update(previous => previous ? { ...previous, legacy: true } : previous);
          return;
        }
        if (!response.ok) throw Error('작업 상태 연결 지연');
        const value = await response.json();
        if (stopped || current.current !== apiBase) return;
        const active = pending.current;
        // A fast local action can finish without creating a new server operation.
        // Preserve its result instead of restoring the previous installation again.
        if (!active && value.state !== 'RUNNING' && latest.current && latest.current.state !== 'RUNNING') return;
        if (value.state && value.state !== 'IDLE' && (!active ||
          (value.kind === active.kind && (value.state === 'RUNNING' || value.id === active.serverId)))) {
          if (active && value.state === 'RUNNING') active.serverId = value.id;
          update({ ...value, observedAt: Date.now(), connectionLost: false });
          again = value.state === 'RUNNING' || Boolean(active);
          if (value.state === 'COMPLETE' && value.kind !== 'selection' && value.id !== completed.current) {
            completed.current = value.id; await reload(controller.signal);
          }
        }
      } catch {
        if (!stopped && current.current === apiBase && (pending.current || latest.current?.state === 'RUNNING')) {
          again = true;
          update(previous => previous ? { ...previous, connectionLost: true } : previous);
        }
      } finally {
        clearTimeout(timeout); controller.signal.removeEventListener('abort', abort);
        if (!stopped && again) timer = setTimeout(poll, 1000);
      }
    };
    poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [apiBase, revision, reload, update]);

  const run = useCallback(async (kind, action, stage) => {
    if (pending.current || latest.current?.state === 'RUNNING') throw Error('진행 중인 음성팩 작업이 끝날 때까지 기다려 주세요.');
    const ticket = { kind, base: apiBase }, started = Date.now(); pending.current = ticket;
    update({ kind, state: 'RUNNING', stage, done: 0, total: null, elapsed_s: 0, observedAt: started });
    refresh(value => value + 1);
    try {
      const result = await action(progress => {
        if (pending.current === ticket && current.current === apiBase) update(previous => ({ ...previous, ...progress }));
      });
      if (pending.current === ticket && current.current === apiBase) {
        update(previous => ({ ...previous, state: 'COMPLETE', elapsed_s: (Date.now()-started)/1000,
          ...(kind === 'install' ? { imported: result.imported } : {}), connectionLost: false }));
      }
      return result;
    } catch (error) {
      if (pending.current === ticket && current.current === apiBase) {
        // A network error does not prove that the server stopped. Keep checking a known running job.
        update(previous => ({ ...previous, state: previous?.id && previous.state === 'RUNNING' ? 'RUNNING' : 'FAILED',
          error: error.message, elapsed_s: (Date.now()-started)/1000 }));
      }
      throw error;
    } finally {
      if (pending.current === ticket) { pending.current = null; refresh(value => value + 1); }
    }
  }, [apiBase, update]);
  const trackSelection = useCallback(action => run('selection', action, 'applying'), [run]);
  return { operation,
    trackSelection,
    install: (path, file) => run('install', async progress => {
      if (file?.size > 2 * 1024 ** 3) throw Error('팩은 최대 2GiB입니다.');
      const id = window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      return file ? uploadFile(`${apiBase}/api/live-coach/custom-voice-packs/upload?operation_id=${encodeURIComponent(id)}`, file, progress)
        : post('import', { path });
    }, file ? 'uploading' : 'checking'),
  };
}
