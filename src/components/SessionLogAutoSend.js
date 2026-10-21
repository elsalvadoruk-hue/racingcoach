import React, { useCallback, useEffect, useState } from 'react';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog';
import { Switch } from './ui/switch';

const when = (seconds) => new Date(seconds * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const CHANGED = 'coach-session-log-auto';

// One switch, kept by the server, for iRacing and LMU alike. It only turns on after the driver
// has read what is sent and agreed; the version of that notice is sent with the agreement.
// The header switch and the settings panel share it: a change in one is announced to the other.
export function useSessionLogAuto(apiBase, visible = true) {
  const [state, setState] = useState(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      const response = await fetch(`${apiBase}/api/session-logs/auto`);
      if (!response.ok) throw new Error('자동 보내기 상태를 불러오지 못했습니다.');
      setState(await response.json());
    } catch (failure) { setError(failure.message); }
  }, [apiBase]);
  useEffect(() => { if (visible) load(); }, [visible, load]);
  useEffect(() => {
    const changed = event => setState(event.detail);
    window.addEventListener(CHANGED, changed);
    return () => window.removeEventListener(CHANGED, changed);
  }, []);
  const change = async (enabled) => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${apiBase}/api/session-logs/auto`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(enabled ? { enabled, consent_version: state.consent_version } : { enabled }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '설정을 바꾸지 못했습니다.');
      setAsking(false);
      window.dispatchEvent(new CustomEvent(CHANGED, { detail: data }));
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  // Turning on always goes through the notice; turning off needs no confirmation.
  const toggle = checked => { setError(''); if (checked) setAsking(true); else change(false); };
  return { state, asking, busy, error, toggle, agree: () => change(true), cancel: () => setAsking(false) };
}

export function ConsentDialog({ auto }) {
  const [agreed, setAgreed] = useState(false);
  if (!auto.asking) return null;
  const close = () => { if (!auto.busy) { setAgreed(false); auto.cancel(); } };
  return <Dialog open onOpenChange={(isOpen) => { if (!isOpen) close(); }}>
    <DialogContent className="max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>세션 로그를 자동으로 보낼까요?</DialogTitle>
        <DialogDescription>켜면 주행이 끝날 때마다 아래 기록이 앱을 만든 사람에게 자동으로 전달됩니다. iRacing과 LMU에 함께 적용됩니다. 읽어 보고 동의할 때만 켜집니다.</DialogDescription>
      </DialogHeader>
      <dl className="flex flex-col gap-3 text-sm leading-relaxed text-foreground">
        <div><dt className="font-semibold">왜 받나요</dt><dd>스포터·엔지니어 안내가 제때 나오고 끝까지 들렸는지, 연료·에너지 계산이 맞았는지 확인해 앱을 고치기 위해서입니다. 다른 목적으로 쓰지 않습니다.</dd></div>
        <div><dt className="font-semibold">보내는 것</dt><dd>안내마다 나온 시각·종류·끝까지 들렸는지와 끊긴 이유, 엔지니어 보고 문장과 그때의 측정값(랩, 연료, 간격, 순위, 트랙, 차량, 세션 종류, 게임), 앱 버전.</dd></div>
        <div><dt className="font-semibold">보내지 않는 것</dt><dd>내 이름과 다른 드라이버 이름(“Driver 번호”로 바뀜), 레이팅, 세션 번호, 마이크로 한 질문과 답변, 음성, 주행 파일(IBT·DuckDB), Google 키, Garage61 토큰, PC의 폴더 경로.</dd></div>
        <div><dt className="font-semibold">언제</dt><dd>이 설정을 켠 뒤의 주행만, 주행이 끝나고 10분쯤 뒤에 주행 하나당 한 번 보냅니다. 켜기 전의 주행은 보내지 않습니다. 짧은 주행(2분 미만이나 안내 10건 미만)은 보내지 않습니다.</dd></div>
        <div><dt className="font-semibold">어디로, 어떻게</dt><dd>암호화된 연결(HTTPS)로 Google Apps Script를 거쳐 앱 제작자의 Google Drive에 저장됩니다. Google의 국내외 데이터센터에 보관될 수 있고, Google이 저장할 때 암호화합니다.</dd></div>
        <div><dt className="font-semibold">얼마나 보관하나요</dt><dd>앱 개선에 쓰는 동안 보관하고, 받은 날로부터 1년이 지나면 지웁니다.</dd></div>
        <div><dt className="font-semibold">동의하지 않거나 끄고 싶을 때</dt><dd>동의하지 않아도 앱의 모든 기능을 그대로 쓸 수 있습니다. 언제든 스위치를 끄면 바로 보내기를 멈춥니다. 이미 보낸 기록을 지우고 싶으면 앱을 받은 곳에서 제작자에게 요청해 주세요.</dd></div>
      </dl>
      <label className="mt-2 flex items-start gap-2 text-sm font-semibold text-foreground">
        <input type="checkbox" className="mt-1" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} disabled={auto.busy} />
        위 내용을 읽었고, 주행 기록을 자동으로 보내는 데 동의합니다.
      </label>
      {auto.error && <p role="alert" className="text-sm text-warning">{auto.error}</p>}
      <DialogFooter className="gap-2">
        <Button type="button" variant="outline" disabled={auto.busy} onClick={close}>켜지 않기</Button>
        <Button type="button" disabled={!agreed || auto.busy} onClick={auto.agree}>{auto.busy ? '켜는 중' : '동의하고 켜기'}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}

export default function SessionLogAutoSend({ apiBase, visible = true }) {
  const auto = useSessionLogAuto(apiBase, visible);
  const { state, error } = auto;
  if (!state) return error ? <p role="alert" className="text-sm text-warning">{error}</p> : null;
  if (!state.available) return null;
  return <div aria-label="세션 로그 자동 보내기" className="mb-4 rounded-lg border border-border p-3">
    <div className="flex items-center justify-between gap-3">
      <div>
        <p className="text-sm font-semibold text-foreground">세션 로그 자동 보내기</p>
        <p className="text-xs text-muted-foreground">iRacing과 LMU 모두에 적용됩니다. 한 번만 켜면 됩니다. 상단의 “로그 보내기”와 같은 스위치입니다.</p>
      </div>
      <Switch aria-label="세션 로그 자동 보내기" checked={state.enabled} disabled={auto.busy} onCheckedChange={auto.toggle} />
    </div>
    {state.enabled && <p role="status" className="mt-2 text-sm text-foreground">
      켜짐 · {when(state.consented_at)}부터의 주행을 보냅니다 · 보낸 주행 {state.sent}개</p>}
    {state.enabled && state.recent?.length > 0 && <ul className="mt-2 flex flex-col gap-1 text-xs text-muted-foreground">
      {state.recent.slice(0, 5).map(run => <li key={run.run_id}>
        {when(run.ended_at)} · {run.track || '트랙 미확인'} · {run.sent_at ? '보냄' : run.attempts >= 3 ? `보내지 못함 · ${run.error || ''} 직접 보내 주세요.` : `다시 시도 예정 · ${run.error || ''}`}
      </li>)}
    </ul>}
    {error && !auto.asking && <p role="alert" className="mt-2 text-sm text-warning">{error}</p>}
    <ConsentDialog auto={auto} />
  </div>;
}
