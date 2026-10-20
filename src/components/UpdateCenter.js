import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog';

const SKIP_KEY = 'coach.update.skippedVersion';
const megabytes = (bytes) => `${(bytes / 1e6).toLocaleString(undefined, { maximumFractionDigits: 0 })} MB`;
const readSkipped = () => { try { return window.localStorage.getItem(SKIP_KEY) || ''; } catch { return ''; } };
const busy = (status) => Boolean(status && (status.app.checking || status.app.download?.state === 'RUNNING'
  || status.voice.scanning || status.voice.job?.state === 'RUNNING'));

// Release notes are GitHub Markdown; the few forms they use (headings, lists, bold, links, code) are drawn as such.
const INLINE = /\*\*(.+?)\*\*|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|`([^`]+)`/g;
function inline(text) {
  const parts = [];
  let last = 0;
  for (const found of text.matchAll(INLINE)) {
    if (found.index > last) parts.push(text.slice(last, found.index));
    const key = parts.length;
    if (found[1] !== undefined) parts.push(<strong key={key}>{inline(found[1])}</strong>);
    else if (found[2] !== undefined) parts.push(<a key={key} className="text-primary underline" href={found[3]} target="_blank" rel="noreferrer">{found[2]}</a>);
    else parts.push(<code key={key} className="rounded bg-muted px-1">{found[4]}</code>);
    last = found.index + found[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function ReleaseNotes({ text }) {
  const blocks = [];
  let list = null;
  text.replace(/\r\n/g, '\n').split('\n').forEach((line) => {
    const item = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)$/);
    if (item) {
      const ordered = Boolean(item[1]);
      if (!list || list.ordered !== ordered) { list = { ordered, items: [] }; blocks.push(list); }
      list.items.push(item[2]);
      return;
    }
    list = null;
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) blocks.push({ heading: heading[1] });
    else if (line.trim()) blocks.push({ text: line.trim() });
  });
  return <div className="flex flex-col gap-1.5">{blocks.map((block, index) => block.items
    ? React.createElement(block.ordered ? 'ol' : 'ul', { key: index, className: `${block.ordered ? 'list-decimal' : 'list-disc'} flex flex-col gap-1 pl-5` },
      block.items.map((entry, position) => <li key={position}>{inline(entry)}</li>))
    : block.heading !== undefined ? <p key={index} className="mt-1 font-semibold">{inline(block.heading)}</p>
      : <p key={index}>{inline(block.text)}</p>)}</div>;
}

// The update state from the local app (iRacing_Backend/update_routes.py), polled quickly only while something runs.
export function useUpdateStatus(apiBase, fetcher = fetch) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [restart, setRestart] = useState(null);   // { from, sawDown, since } while the launcher restarts the app
  const call = useCallback(async (path = '', method = 'GET') => {
    try {
      const response = await fetcher(`${apiBase}/api/update${path}`, { method });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '업데이트 상태를 확인하지 못했습니다.');
      if (data.app) setStatus(data);
      setError('');
      return data;
    } catch (failure) { setError(failure.message); return null; }
  }, [apiBase, fetcher]);
  useEffect(() => { call(); }, [call]);
  useEffect(() => {
    if (restart) return undefined;
    const timer = setInterval(() => call(), busy(status) ? 1500 : 600000);
    return () => clearInterval(timer);
  }, [status, restart, call]);
  useEffect(() => {
    if (!restart) return undefined;
    const timer = setInterval(async () => {
      try {
        const response = await fetcher(`${apiBase}/api/update`);
        const data = await response.json();
        if (data.app?.current && data.app.current !== restart.from) { window.location.reload(); return; }
        if (restart.sawDown) {
          setRestart(null); setStatus(data);
          setError('다시 시작했지만 업데이트가 적용되지 않았습니다. 실행.bat 창의 메시지를 확인하세요.');
        } else if (Date.now() - restart.since > 30000) {
          setRestart(null); setError('앱이 다시 시작되지 않았습니다. 실행.bat 창을 닫고 다시 실행하세요.');
        }
      } catch { setRestart((current) => current && { ...current, sawDown: true }); }
    }, 1500);
    return () => clearInterval(timer);
  }, [restart, apiBase, fetcher]);
  const startRestart = useCallback(async () => {
    const from = status?.app.current;
    if (await call('/app/restart', 'POST')) setRestart({ from, sawDown: false, since: Date.now() });
  }, [status, call]);
  return { status, error, call, restarting: Boolean(restart), startRestart };
}

function ProgramSection({ app, call, restarting, startRestart }) {
  const download = app.download || {};
  const latest = app.latest;
  return <section className="flex flex-col gap-2" aria-label="프로그램 업데이트">
    <h3 className="text-sm font-semibold text-foreground">프로그램 · 지금 v{app.current}</h3>
    {app.checking && <p className="text-sm text-muted-foreground">새 버전 확인 중…</p>}
    {app.error && <p className="text-sm text-warning">{app.error}</p>}
    {app.available && latest ? <>
      <p className="text-sm text-foreground">새 버전 <b>v{latest.version}</b> · {latest.title}</p>
      {latest.notes && <div className="max-h-56 overflow-y-auto rounded-md border border-border bg-muted/30 p-3 text-xs leading-relaxed text-foreground [overflow-wrap:anywhere]" data-testid="release-notes"><ReleaseNotes text={latest.notes} /></div>}
      {latest.url && <a className="text-xs text-primary underline" href={latest.url} target="_blank" rel="noreferrer">GitHub에서 보기</a>}
      {app.mode === 'git'
        ? <p className="text-sm text-muted-foreground">Git으로 받은 폴더입니다. <code>git pull --ff-only</code> 후 설치.bat을 실행하세요.</p>
        : app.staged ? <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-foreground">v{app.staged} 준비 완료.</span>
          {app.restart_supported
            ? <Button type="button" disabled={restarting} onClick={startRestart}>{restarting ? '다시 시작하는 중…' : '지금 다시 시작해서 적용'}</Button>
            : <span className="text-sm text-muted-foreground">실행.bat 창을 닫고 다시 실행하면 적용됩니다.</span>}
        </div>
        : download.state === 'RUNNING' ? <div className="flex flex-col gap-1">
          <progress className="h-2 w-full" max={download.total || undefined} value={download.total ? download.done : undefined} aria-label="업데이트 받는 중" />
          <span className="text-xs text-muted-foreground">{megabytes(download.done)}{download.total ? ` / ${megabytes(download.total)}` : ''} 받는 중</span>
        </div>
        : <div><Button type="button" onClick={() => call('/app/download', 'POST')}>업데이트 받기</Button></div>}
      {download.state === 'FAILED' && <p className="text-sm text-warning">{download.message}</p>}
      {app.mode !== 'git' && !app.staged && <p className="text-xs leading-relaxed text-muted-foreground">받는 동안 앱을 계속 써도 됩니다. 적용할 때 데이터 폴더(기록·설정·키·토큰·음성)는 그대로 둡니다.{latest.prebuilt ? '' : ' 이 버전은 화면을 새로 만들어야 해서 적용할 때 몇 분 걸릴 수 있습니다(Node.js 필요).'}</p>}
    </> : !app.checking && !app.error && <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-foreground">최신 버전입니다.</span>
      <Button type="button" variant="outline" size="sm" onClick={() => call('/check', 'POST')}>다시 확인</Button>
    </div>}
  </section>;
}

function VoiceSection({ voice, call }) {
  const job = voice.job || {};
  const running = job.state === 'RUNNING';
  return <section className="flex flex-col gap-2" aria-label="음성 업데이트">
    <h3 className="text-sm font-semibold text-foreground">음성{voice.version ? ` · ${voice.version}` : ''}</h3>
    {voice.scanning && !running && <p className="text-sm text-muted-foreground">받아 둔 음성 확인 중…</p>}
    {voice.error && <p className="text-sm text-warning">{voice.error}</p>}
    {!voice.scanning && voice.version && (voice.missing > 0
      ? <p className="text-sm text-foreground">음성 {voice.total.toLocaleString()}개 중 <b>{voice.missing.toLocaleString()}개</b>가 없습니다.</p>
      : <p className="text-sm text-foreground">음성 {voice.total.toLocaleString()}개가 모두 있습니다.</p>)}
    {running ? <div className="flex flex-col gap-1">
      <progress className="h-2 w-full" max={job.total_bytes || undefined} value={job.total_bytes ? job.done_bytes : undefined} aria-label="음성 받는 중" />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{job.message}{job.total_bytes ? ` · ${megabytes(job.done_bytes)} / ${megabytes(job.total_bytes)}` : ''}</span>
        <Button type="button" variant="outline" size="sm" onClick={() => call('/voice/stop', 'POST')}>멈추기</Button>
      </div>
    </div> : <>
      {job.message && <p className={job.state === 'FAILED' ? 'text-sm text-warning' : 'text-sm text-foreground'}>{job.message}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {voice.missing > 0 && <Button type="button" onClick={() => call('/voice/download', 'POST')}>빠진 음성 받기</Button>}
        {voice.obsolete_files > 0 && <Button type="button" variant="outline" onClick={() => call('/voice/cleanup', 'POST')}>
          이전 버전 음성 정리 ({voice.obsolete_files.toLocaleString()}개 · {megabytes(voice.obsolete_bytes)})</Button>}
      </div>
    </>}
    <p className="text-xs leading-relaxed text-muted-foreground">GitHub에서 없는 음성만 골라 받습니다. 받은 음성은 데이터 폴더의 radio_audio·spotter_audio에 바로 들어갑니다.
      {voice.drive_url && <> 직접 받으려면 <a className="text-primary underline" href={voice.drive_url} target="_blank" rel="noreferrer">Google Drive 음성 폴더</a>를 쓰세요.</>}</p>
  </section>;
}

export function UpdatePanel({ updates }) {
  const { status, error, call, restarting, startRestart } = updates;
  if (!status) return error ? <p role="alert" className="text-sm text-warning">{error}</p> : null;
  return <div className="flex min-w-0 flex-col gap-5 [overflow-wrap:anywhere]">
    <ProgramSection app={status.app} call={call} restarting={restarting} startRestart={startRestart} />
    <VoiceSection voice={status.voice} call={call} />
    {restarting && <p role="status" className="text-sm text-foreground">업데이트를 적용하고 다시 시작하는 중입니다. 잠시 후 화면이 새로 고쳐집니다.</p>}
    {error && <p role="alert" className="text-sm text-warning">{error}</p>}
  </div>;
}

// The settings tab's copy: always there, to check again or fetch the voice later.
export function UpdateSettings({ apiBase, fetcher }) {
  return <UpdatePanel updates={useUpdateStatus(apiBase, fetcher)} />;
}

// Opens by itself only when the app starts: if the first check after the screen opened finds a new version
// (unless that version was skipped) or missing clips. Anything found later, while the app runs and perhaps
// during a race, only shows in the settings tab. Once open it stays until closed, through download and restart.
export default function UpdateCenter({ apiBase, fetcher }) {
  const updates = useUpdateStatus(apiBase, fetcher);
  const [closed, setClosed] = useState(false);
  const [skipped, setSkipped] = useState(readSkipped);
  const decided = useRef(null);
  const { status, restarting } = updates;
  const app = status?.app;
  const newVersion = Boolean(app?.available && app.latest && app.latest.version !== skipped);
  const missingVoice = Boolean(status && !status.voice.scanning && status.voice.missing > 0);
  const checked = Boolean(app?.checked_at && !app.checking && !status.voice.scanning && 'missing' in status.voice);
  if (decided.current === null && checked) decided.current = newVersion || missingVoice;
  const open = (!closed && decided.current === true) || restarting;
  if (!open) return null;
  const skip = () => {
    try { window.localStorage.setItem(SKIP_KEY, app.latest.version); } catch { /* convenience only */ }
    setSkipped(app.latest.version); setClosed(true);
  };
  return <Dialog open onOpenChange={(next) => { if (!next && !restarting) setClosed(true); }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto overflow-x-hidden">
      <DialogHeader>
        <DialogTitle>{newVersion ? `새 버전 v${app.latest.version}` : '업데이트'}</DialogTitle>
        <DialogDescription>{newVersion ? '업데이트를 받고 다시 시작하면 적용됩니다.' : '이 버전에 필요한 음성 중 빠진 것이 있습니다.'}</DialogDescription>
      </DialogHeader>
      <UpdatePanel updates={updates} />
      <DialogFooter>
        {newVersion && !app.staged && <Button type="button" variant="ghost" disabled={restarting} onClick={skip}>이 버전 건너뛰기</Button>}
        <Button type="button" variant="outline" disabled={restarting} onClick={() => setClosed(true)}>나중에</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
