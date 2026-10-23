import React, { useEffect, useRef, useState } from 'react';
import { StickyNote } from 'lucide-react';
import { Button } from '../components/ui/button';
import { requestJson } from '../analysis/analysisClient';

const bucket = pct => Number.isFinite(pct) && pct >= 0 && pct <= 1 ? Math.floor(pct * 200 + .5) : null;
export const noteAt = (notes, pct) => bucket(pct) == null ? undefined : notes.find(note => bucket(note.pct) === bucket(pct));

export function NoteIcon({ notes = [], pct }) {
  return noteAt(notes, pct)?.text ? <StickyNote size={14} role="img" aria-label="내 메모 있음" /> : null;
}

export function useCornerNotes(session, game, enabled = true) {
  const trackId = session?.track_platform_id ? String(session.track_platform_id).trim() : '';
  const trackConfig = session?.track_config || '';
  const scope = JSON.stringify([game, trackId, trackConfig]);
  const generation = useRef(0);
  const [state, setState] = useState({ scope: '', notes: [], ready: false, error: '' });
  useEffect(() => {
    const token = ++generation.current;
    const controller = new AbortController();
    if (!trackId || !enabled) return () => { controller.abort(); generation.current = token + 1; };
    setState({ scope, notes: [], ready: false, error: '' });
    const query = new URLSearchParams({ game, track_id: trackId, track_config: trackConfig });
    requestJson(`/api/corner-notes?${query}`, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted && generation.current === token) setState({ scope, notes: result?.notes || [], ready: true, error: '' });
    }).catch(error => {
      if (!controller.signal.aborted && generation.current === token) setState({ scope, notes: [], ready: false, error: error.message });
    });
    return () => { controller.abort(); generation.current = token + 1; };
  }, [scope, trackId, trackConfig, game, enabled]);
  const current = state.scope === scope ? state : { notes: [], ready: false, error: '' };
  const save = async (pct, text) => {
    if (!current.ready || !enabled) throw new Error('메모를 불러온 뒤 저장할 수 있습니다.');
    const token = generation.current;
    const saved = await requestJson('/api/corner-notes', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ game, track_id: trackId, track_config: trackConfig, pct, text }) });
    if (generation.current === token) setState(previous => ({ ...previous,
      notes: [...previous.notes.filter(note => bucket(note.pct) !== bucket(saved.pct)), ...(saved.text ? [saved] : [])] }));
    return saved;
  };
  return { ...current, scope, save, available: Boolean(trackId) };
}

export default function CornerNote({ corner, store }) {
  return corner ? <NoteEditor key={`${store.scope}:${bucket(corner.apex_pct)}`} corner={corner} store={store} /> : null;
}

function NoteEditor({ corner, store }) {
  const note = noteAt(store.notes, corner.apex_pct);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note?.text || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { if (!editing) setDraft(note?.text || ''); }, [note?.text, editing]);
  const count = Array.from(draft).length;
  const save = async () => {
    setBusy(true); setError(''); setMessage('');
    try {
      await store.save(corner.apex_pct, draft);
      if (active.current) { setEditing(false); setMessage(draft ? '메모를 저장했습니다.' : '메모를 삭제했습니다.'); }
    } catch (failure) { if (active.current) setError(failure.message); }
    finally { if (active.current) setBusy(false); }
  };
  const available = store.available && bucket(corner.apex_pct) != null;
  return <section className="corner-note" aria-label="내 코너 메모"><h3>내 메모</h3>
    {!available ? <p>트랙과 코너 위치를 확인할 수 없어 메모를 저장할 수 없습니다.</p> : <>
      {store.error && <p role="alert">메모를 불러오지 못했습니다. {store.error}</p>}
      {!store.ready && !store.error && <p role="status">메모를 불러오는 중입니다.</p>}
      {editing ? <><label htmlFor="corner-note-text">이 코너의 메모</label>
        <textarea id="corner-note-text" value={draft} disabled={busy} onChange={event => setDraft(event.target.value)} rows={3} aria-describedby="corner-note-count" />
        <small id="corner-note-count">{count}/300자 · 비우고 저장하면 삭제합니다.</small>
        <div className="corner-note-actions"><Button size="sm" disabled={busy || !store.ready || count > 300} onClick={save}>메모 저장</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setEditing(false); setError(''); }}>취소</Button></div>
      </> : <><p className="corner-note-text">{note?.text || (store.ready ? '메모가 없습니다.' : '')}</p>
        <Button size="sm" variant="outline" disabled={!store.ready} onClick={() => { setEditing(true); setMessage(''); }}>{note?.text ? '메모 편집' : '메모 쓰기'}</Button></>}
      {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    </>}
    <small>같은 게임·트랙·레이아웃에서 차량과 관계없이 보관합니다. 음성으로 읽지 않습니다.</small>
  </section>;
}
