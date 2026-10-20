import React, { useEffect, useRef, useState } from 'react';
import { requestJson } from '../analysis/analysisClient';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Field, FieldLabel } from '../components/ui/field';

export default function LmuSettingsPanel({ onChooseSource }) {
  const [view, setView] = useState(null), [folder, setFolder] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const flight = useRef(null), mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController(); flight.current = controller;
    requestJson('/api/lmu/settings', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setView(result);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (flight.current === controller) flight.current = null; });
    return () => { mounted.current = false; flight.current?.abort(); };
  }, []);
  const save = async folders => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await requestJson('/api/lmu/settings', { method: 'PUT', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ telemetry_dirs: folders }) });
      if (mounted.current && !controller.signal.aborted) {
        setView(result); setFolder(''); setNotice('LMU 기록 폴더를 저장했습니다. 주행 선택에서 새 파일 목록을 확인하세요.');
      }
    } catch (e) { if (mounted.current && !controller.signal.aborted) setError(e.message); }
    finally { if (flight.current === controller) flight.current = null; if (mounted.current) setBusy(false); }
  };
  const added = view?.settings?.telemetry_dirs || [];
  return <Card><CardHeader><CardTitle>LMU 기록 폴더</CardTitle><CardDescription>기본 Steam 폴더와 추가한 폴더에서 종료된 DuckDB 주행 기록을 찾습니다.</CardDescription></CardHeader><CardContent className="flex flex-col gap-3">
    {error && <p role="alert">{error}</p>}
    {!view && !error && <p role="status">LMU 설정 불러오는 중…</p>}
    {view?.source === 'ENVIRONMENT' && <p>LMU_TELEMETRY_DIRS 환경 변수의 폴더가 우선합니다. 저장한 폴더는 환경 변수 해제 후 사용합니다.</p>}
    <ul>{(view?.telemetry_folders || []).map(item => <li key={item.path} className="flex flex-wrap items-center justify-between gap-2"><span className="break-all">{item.path} · {item.exists ? '폴더 확인됨' : '폴더 없음'}</span></li>)}</ul>
    {!!added.length && <div><h3>추가한 폴더</h3><ul>{added.map(path => <li key={path} className="flex flex-wrap items-center justify-between gap-2"><span className="break-all">{path}</span><Button variant="outline" size="sm" disabled={busy} aria-label={`${path} 빼기`} onClick={() => save(added.filter(item => item !== path))}>빼기</Button></li>)}</ul></div>}
    <Field><FieldLabel htmlFor="lmu-recording-folder">추가할 LMU 기록 폴더</FieldLabel><Input id="lmu-recording-folder" value={folder} disabled={!view || busy} onChange={event => setFolder(event.target.value)} placeholder="LMU UserData\Telemetry 폴더의 전체 경로" /></Field>
    <div className="flex flex-wrap gap-2"><Button disabled={!view || busy || !folder.trim() || added.length >= 12} onClick={() => save([...added, folder.trim()])}>폴더 추가</Button>{onChooseSource && <Button variant="outline" onClick={onChooseSource}>주행 목록 확인</Button>}</div>
    {notice && <p role="status">{notice}</p>}
  </CardContent></Card>;
}
