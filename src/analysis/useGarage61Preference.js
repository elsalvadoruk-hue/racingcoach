import { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson } from './analysisClient';

const endpoint = '/api/garage61/collector/preferences';

export function useGarage61Preference(onChanged, enabled = true) {
  const [mode, setMode] = useState('MATCHED');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const mounted = useRef(false);
  const busy = useRef(false);
  const request = useRef(null);
  const reload = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const payload = await requestJson(endpoint, { signal: controller.signal });
      if (mounted.current && !controller.signal.aborted) { setMode(payload.selection_mode); setError(''); }
    } catch (failure) {
      if (mounted.current && !controller.signal.aborted) setError(`수집 설정을 불러오지 못했습니다. ${failure.message}`);
    } finally {
      if (mounted.current && !controller.signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    if (enabled) void reload();
    return () => { mounted.current = false; request.current?.abort(); };
  }, [reload, enabled]);
  const change = useCallback(async next => {
    if (!enabled || !next || next === mode || busy.current || loading) return;
    busy.current = true;
    setSaving(true);
    try {
      const payload = await requestJson(endpoint, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selection_mode: next }),
      });
      if (!mounted.current) return;
      setMode(payload.selection_mode);
      setError('');
      await changed.current?.();
    } catch (failure) {
      if (mounted.current) setError(`수집 설정을 저장하지 못했습니다. ${failure.message}`);
    } finally {
      busy.current = false;
      if (mounted.current) setSaving(false);
    }
  }, [mode, loading, enabled]);
  return { mode, loading, saving, error, reload, change };
}
