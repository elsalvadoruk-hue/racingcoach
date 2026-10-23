import React, { useCallback, useEffect, useState } from 'react';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog';
import { Textarea } from './ui/textarea';
import SessionLogAutoSend from './SessionLogAutoSend';

const when = (seconds) => new Date(seconds * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

// One driven session as a file for whoever improves the app: what was said, what was lost and why.
// It can be saved, or (when the maker set up a receiving address) sent straight to him.
export default function SessionLogExport({ apiBase, visible = true }) {
  const [sessions, setSessions] = useState(null);
  const [canSend, setCanSend] = useState(false);
  const [questions, setQuestions] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [sending, setSending] = useState(null);    // the session whose send window is open
  const [sendError, setSendError] = useState('');

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${apiBase}/api/session-logs?limit=12`);
      const data = await response.json();
      if (!response.ok) throw new Error('세션 기록을 불러오지 못했습니다.');
      setSessions(data.sessions || []); setCanSend(Boolean(data.send_available)); setError('');
    } catch (failure) { setError(failure.message); }
  }, [apiBase]);
  useEffect(() => { if (visible) load(); }, [visible, load]);

  const save = async (session) => {
    setBusy(session.run_id); setNotice(''); setError('');
    try {
      const response = await fetch(`${apiBase}/api/session-logs/${session.run_id}/export?questions=${questions ? 1 : 0}`);
      if (!response.ok) throw new Error('로그 파일을 만들지 못했습니다.');
      const name = response.headers.get('X-Log-File-Name') || `coach-log-${session.run_id.slice(0, 8)}.zip`;
      const url = URL.createObjectURL(await response.blob());
      const link = Object.assign(document.createElement('a'), { href: url, download: name });
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(`${name} 파일을 내려받았습니다. 브라우저의 다운로드 폴더에 있습니다.`);
    } catch (failure) { setError(failure.message); } finally { setBusy(''); }
  };

  // "보내기" on a session only opens a window for the note; the log goes when "보내기" is pressed there.
  const open = (session) => { setSending(session); setSendError(''); setNotice(''); setError(''); };
  const close = () => { if (!busy) { setSending(null); setSendError(''); } };
  const send = async () => {
    const session = sending;
    setBusy(session.run_id); setSendError('');
    try {
      const response = await fetch(`${apiBase}/api/session-logs/${session.run_id}/send`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ questions, note }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '로그를 보내지 못했습니다.');
      setNotice(`${when(session.started_at)} 주행의 로그를 보냈습니다. 고맙습니다.`); setNote(''); setSending(null);
    } catch (failure) { setSendError(failure.message); } finally { setBusy(''); }
  };

  if (!sessions) return error ? <p role="alert" className="text-sm text-warning">{error}</p> : null;
  return <div aria-label="세션 로그 보내기">
    {canSend && <SessionLogAutoSend apiBase={apiBase} visible={visible} />}
    <p className="text-sm leading-relaxed text-foreground">주행 한 번의 음성 안내 기록을 파일 하나로 만듭니다. 어떤 안내가 나왔고, 끝까지 들렸는지, 끊기거나 못 나온 이유가 무엇인지와 그때 엔지니어가 본 측정값이 들어 있습니다. 개선을 부탁할 때 이 파일을 보내 주세요.</p>
    <ul className="mt-2 list-disc pl-5 text-xs leading-relaxed text-muted-foreground">
      <li>함께 들어가는 것: 내 주행 측정값(랩타임, 코너별 제동·가속 위치와 속도, 회전 반응 등 계산된 값). 레이스면 주변 차 기록(앞뒤 200m 안 차의 거리·순위·클래스, 이름 없음)도 들어갑니다. “보내기”는 크기 한도를 넘으면 주변 차 기록부터 뺍니다.</li>
      <li>들어가지 않는 것: 음성, 주행 파일(IBT) 원본, 셋업 파일 이름, Google 키, Garage61 토큰, 폴더 경로.</li>
      <li>드라이버 이름은 “Driver 번호”로 바뀌고 레이팅과 세션 번호는 빠집니다. 트랙, 차량, 랩타임, 순위, 연료 같은 숫자는 그대로 들어갑니다.</li>
      {canSend && <li>“보내기”를 누르면 이 파일이 인터넷으로 앱을 만든 사람의 Google Drive에 전달됩니다. “세션 로그 자동 보내기”를 켜지 않으면 누를 때만 보냅니다.</li>}
    </ul>
    <label className="mt-3 flex items-center gap-2 text-sm text-foreground">
      <input type="checkbox" checked={questions} onChange={(event) => setQuestions(event.target.checked)} />
      마이크로 한 질문과 답변도 포함
    </label>
    {sessions.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">아직 기록된 주행이 없습니다. 스포터를 켜고 주행하면 여기에 나옵니다.</p>
      : <ul className="mt-3 flex flex-col gap-2">
        {sessions.map((session) => <li key={session.run_id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3">
          <div className="min-w-0 text-sm">
            <p className="font-semibold text-foreground">{when(session.started_at)} · {session.track || '트랙 미확인'}</p>
            <p className="text-muted-foreground">{[session.car, session.session_type, `${session.minutes}분`, `안내 기록 ${session.calls}건`,
              `보고 ${session.reports}건`, session.questions ? `질문 ${session.questions}건` : null].filter(Boolean).join(' · ')}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {canSend && <Button type="button" disabled={Boolean(busy)} onClick={() => open(session)}
              aria-label={`${when(session.started_at)} 주행 로그 보내기`}>{busy === session.run_id ? '처리 중' : '보내기'}</Button>}
            <Button type="button" variant="outline" disabled={Boolean(busy)} onClick={() => save(session)}
              aria-label={`${when(session.started_at)} 주행 로그 파일 받기`}>로그 파일 받기</Button>
          </div>
        </li>)}
      </ul>}
    {notice && <p role="status" className="mt-3 text-sm text-foreground">{notice}</p>}
    {error && <p role="alert" className="mt-3 text-sm text-warning">{error}</p>}
    {sending && <Dialog open onOpenChange={(isOpen) => { if (!isOpen) close(); }}>
      <DialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }} onInteractOutside={(event) => { if (busy) event.preventDefault(); }}>
        <DialogHeader>
          <DialogTitle>주행 로그 보내기</DialogTitle>
          <DialogDescription>{when(sending.started_at)} · {sending.track || '트랙 미확인'}{sending.car ? ` · ${sending.car}` : ''}</DialogDescription>
        </DialogHeader>
        <label htmlFor="log-note" className="text-sm font-semibold text-foreground">함께 보낼 메모</label>
        <Textarea id="log-note" value={note} maxLength={500} rows={4} autoFocus disabled={Boolean(busy)}
          onChange={(event) => setNote(event.target.value)} placeholder="어느 랩쯤 무엇이 이상했는지 적어 주세요. (예: 12랩쯤 연료 안내가 실제와 달랐음)" />
        <p className="text-xs leading-relaxed text-muted-foreground">
          {questions ? '마이크로 한 질문과 답변이 포함됩니다.' : '마이크로 한 질문과 답변은 포함되지 않습니다.'} 메모는 비워 둬도 됩니다.
        </p>
        {sendError && <p role="alert" className="text-sm text-warning">{sendError}</p>}
        <DialogFooter className="gap-2">
          <Button type="button" variant="outline" onClick={close} disabled={Boolean(busy)}>취소</Button>
          <Button type="button" onClick={send} disabled={Boolean(busy)}>{busy ? '보내는 중' : '보내기'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>}
  </div>;
}
