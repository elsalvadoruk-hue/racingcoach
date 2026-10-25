import { useCallback, useEffect, useRef, useState } from 'react';
import { readGameItem, writeGameItem } from './gameStorage';
import useVoicePackInstall from './voicePackInstall';

export const VOICE_ROLES = { spotter: '스포터', coach: '주행 코치', race: '레이스 엔지니어', strategy: '전략 엔지니어' };
const PACK = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}@[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
const removed = new Set();
export function voiceSelections(settings = {}) {
  const value = settings.voice_packs || (settings.custom_pack ? { spotter: settings.custom_pack } : {});
  return Object.fromEntries(Object.entries(value).filter(([role, key]) => role in VOICE_ROLES && typeof key === 'string' && PACK.test(key) && !removed.has(key)));
}
export function cleanVoiceSettings(settings) {
  if (!settings.voice_packs && !removed.has(settings.custom_pack)) return settings;
  const voice_packs = voiceSelections(settings);
  return { ...settings, voice_packs, custom_pack: voice_packs.spotter || null };
}
function forget(key) {
  removed.add(key);
  for (const game of ['iracing', 'lmu']) {
    try {
      const saved = JSON.parse(readGameItem('iracing.spotter.settings', game));
      if (saved) writeGameItem('iracing.spotter.settings', game, JSON.stringify(cleanVoiceSettings(saved)));
    } catch { /* Missing saved settings use defaults. */ }
  }
  window.dispatchEvent(new Event('voice-library-changed'));
}
export default function useVoiceLibrary(apiBase) {
  const currentApi = useRef(apiBase); currentApi.current = apiBase;
  const [view, setView] = useState(null), [error, setError] = useState(''), [revision, refresh] = useState(0);
  const reload = useCallback(async signal => {
    const r = await fetch(`${apiBase}/api/live-coach/custom-voice-packs`, { signal });
    if (!r.ok) throw Error('설치한 음성팩을 읽지 못했습니다. 다시 불러오세요.');
    const value = await r.json(); if (!signal?.aborted && currentApi.current === apiBase) { setView(value); setError(''); } return value;
  }, [apiBase]);
  useEffect(() => { const c = new AbortController(); setView(null); reload(c.signal).catch(e => { if (e.name !== 'AbortError') setError(e.message); }); return () => c.abort(); }, [reload]);
  useEffect(() => { const update = () => refresh(n => n + 1); window.addEventListener('voice-library-changed', update); return () => window.removeEventListener('voice-library-changed', update); }, []);
  const post = async (path, body, upload) => {
    const r = await fetch(`${apiBase}/api/live-coach/custom-voice-packs/${path}`, { method: 'POST', headers: { 'Content-Type': upload ? 'application/zip' : 'application/json' }, body: upload || JSON.stringify(body) });
    const v = await r.json(); if (!r.ok) throw Error(typeof v.detail === 'string' ? v.detail : '음성팩 요청을 완료하지 못했습니다.'); return v;
  };
  const installation = useVoicePackInstall(apiBase, reload, post);
  return { view, error, revision, reload, operation: installation.operation, trackSelection: installation.trackSelection,
    install: async (path, file) => { const r = await installation.install(path, file); removed.delete(r.imported.key); await reload(); return r.imported; },
    preview: key => post('preview', { key }), updates: key => post('updates', { key }),
    remove: async (key, deleteFiles) => { await post('remove', { key, delete: deleteFiles }); forget(key); await reload(); },
  };
}
