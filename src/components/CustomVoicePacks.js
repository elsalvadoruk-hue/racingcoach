import React, { useRef, useState } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Field, FieldGroup, FieldLabel } from './ui/field';
import { Alert, AlertDescription } from './ui/alert';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './ui/dialog';
import { VOICE_ROLES, voiceSelections } from '../lib/voiceLibrary';
import VoicePackProgress from './VoicePackProgress';

export default function CustomVoicePacks({ library, settings, disabled = false }) {
  const [path, setPath] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [sample, setSample] = useState(null), [notice, setNotice] = useState(''), [removing, setRemoving] = useState(null);
  const file = useRef(null), audio = useRef(null);
  const operation = library?.operation, working = busy || operation?.state === 'RUNNING';
  const run = async fn => { if (working || disabled || !library) return; setBusy(true); setError(''); setNotice(''); audio.current?.pause(); setSample(null); try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const install = upload => run(async () => { const p = await library.install(path.trim().replace(/^"|"$/g, ''), upload); setNotice(`${p.name} 설치 완료. 스포터 또는 피트월의 목소리 목록에서 선택하세요.`); });
  const downloadUpdates = pack => run(async () => {
    const value = await library.updates(pack.key);
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = `${pack.key.replace('@', '-')}.voice-update.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice('Voice Studio에서 이 팩을 만들던 작업/제작설정을 열고 추가 제작 목록을 불러오세요. 새 대본 생성·검수 후 버전을 올려 다시 설치하세요.');
  });
  const packs = library?.view?.packs || [], selected = voiceSelections(settings);
  return <details className="mt-4 rounded-lg border border-border p-3"><summary className="text-sm">음성팩 설치·관리{operation?.state === 'RUNNING' && operation.kind !== 'selection' ? ' · 설치 중' : packs.some(p => p.updates?.newCount > 0) ? ' · 추가 제작 알림' : ''}</summary>
    <p className="mt-3 text-xs text-muted-foreground">① .rcvoice.zip 설치 → ② 스포터·피트월에서 각 목소리 선택. 원하는 역할만 만들고 기본 목소리와 섞어 쓸 수 있습니다. 없는 문장은 준비된 기본 음성으로 보완하며 추가 유료 합성을 자동 요청하지 않습니다.</p>
    <FieldGroup className="mt-3"><Field><FieldLabel htmlFor="voice-pack-path">설치할 팩 폴더 또는 ZIP 경로</FieldLabel><Input id="voice-pack-path" value={path} onChange={e => setPath(e.target.value)} disabled={disabled || working} placeholder=".rcvoice.zip 또는 팩 폴더" /></Field></FieldGroup>
    <div className="mt-3 flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={disabled || working || !path.trim()} onClick={() => install()}>경로에서 설치</Button><Button variant="outline" size="sm" disabled={disabled || working} onClick={() => file.current?.click()}>ZIP 파일 설치</Button><Button variant="ghost" size="sm" disabled={disabled || working} onClick={() => run(() => library.reload())}>목록 새로고침</Button></div>
    <input ref={file} type="file" accept=".zip" className="hidden" aria-label="커스텀 음성팩 ZIP" onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) install(f); }} />
    <VoicePackProgress operation={operation?.kind !== 'selection' ? operation : null} />
    {busy && !operation && <p role="status" className="mt-3 text-sm">음성팩 요청 처리 중… 완료될 때까지 기다려 주세요.</p>}
    {notice && !(operation?.state === 'COMPLETE' && notice.includes('설치 완료')) && <p role="status" className="mt-3 text-sm">{notice}</p>}
    {!library?.view && <p className="mt-3 text-sm">설치 목록 확인 중…</p>}
    {library?.view && !packs.length && !working && <p className="mt-3 text-sm text-muted-foreground">설치된 음성팩이 없습니다. ZIP 파일을 설치하세요.</p>}
    <ul className="mt-4 flex flex-col gap-3">{packs.map(p => <li key={p.key} className="min-w-0 rounded-md border border-border p-3">
      <p className="break-words text-sm font-medium">{p.name} · {p.version} · {p.audioFormat}</p>
      <p className="mt-1 text-xs text-muted-foreground">{Object.entries(p.roles || {}).filter(([, n]) => n > 0).map(([r, n]) => `${VOICE_ROLES[r]} ${n.toLocaleString()}개`).join(' · ')}</p>
      <p className="mt-1 text-xs">사용 중: {Object.entries(selected).filter(([, k]) => k === p.key).map(([r]) => VOICE_ROLES[r]).join(', ') || '선택 안 됨'}</p>
      {p.updates?.newCount > 0 && <Alert className="mt-3"><AlertDescription>앱 업데이트로 {p.updates.newCount.toLocaleString()}개 문장이 추가됐습니다. 제작 전까지 준비된 기본 음성을 사용합니다.</AlertDescription></Alert>}
      {!p.baselineKnown && <p className="mt-2 text-xs text-muted-foreground">제작 당시 전체 목록이 없는 팩입니다. 업데이트 신규 문장을 자동 구분하지 않습니다. Voice Studio에서 최신 앱 목록을 읽어 빠진 대본을 추가하세요.</p>}
      <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={disabled || working} onClick={() => run(async () => setSample(await library.preview(p.key)))}>미리 듣기</Button><Button size="sm" variant="outline" disabled={disabled || working || !p.baselineKnown} onClick={() => downloadUpdates(p)}>추가 제작 목록 저장</Button><Button size="sm" variant="ghost" disabled={disabled || working} onClick={() => setRemoving(p)}>적용 해제·삭제</Button></div>
    </li>)}</ul>
    {sample && <div className="mt-3"><p className="text-xs">{sample.text}</p><audio ref={audio} controls src={sample.audio_base64} className="mt-2 w-full" /></div>}
    {(error || library?.error) && <Alert variant="destructive" className="mt-3"><AlertDescription>{error || library.error}</AlertDescription></Alert>}
    {disabled && <p className="mt-2 text-xs text-muted-foreground">음성팩을 바꾸려면 스포터·자동 보고를 중지하세요.</p>}
    <Dialog open={Boolean(removing)} onOpenChange={open => !open && !working && setRemoving(null)}><DialogContent><DialogHeader><DialogTitle>음성팩 적용 해제 또는 삭제</DialogTitle><DialogDescription>{removing?.name}의 모든 게임·역할 선택을 해제합니다. 설치 파일은 보관하거나 삭제할 수 있습니다. 직접 받은 원본 ZIP·폴더는 삭제하지 않습니다.</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={working || disabled} onClick={() => run(async () => { await library.remove(removing.key, false); setRemoving(null); setNotice('기본 목소리로 돌아갔습니다. 설치 파일은 보관했습니다.'); })}>적용만 해제 · 파일 보관</Button><Button variant="destructive" disabled={working || disabled} onClick={() => run(async () => { await library.remove(removing.key, true); setRemoving(null); setNotice('설치한 로컬 파일까지 삭제했습니다. 원본 ZIP은 보존했습니다.'); })}>설치 파일까지 삭제</Button></DialogFooter></DialogContent></Dialog>
  </details>;
}
