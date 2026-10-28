import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog';
import { Switch } from './ui/switch';

const when = (seconds) => new Date(seconds * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const CHANGED = 'coach-session-log-auto';

// One switch, kept by the server, for iRacing and LMU alike. It only turns on after the driver
// has read what is sent and agreed; the version of that notice is sent with the agreement.
// The header switch and the settings panel share it: a change in one is announced to the other.
export function useSessionLogAuto(apiBase, visible = true, autoPrompt = false) {
  const [state, setState] = useState(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const revision = useRef(0);
  const prompted = useRef(null);
  const load = useCallback(async () => {
    const expected = revision.current;
    try {
      const response = await fetch(`${apiBase}/api/session-logs/auto`);
      if (!response.ok) throw new Error('자동 보내기 상태를 불러오지 못했습니다.');
      const data = await response.json();
      if (expected === revision.current) setState(data);
    } catch (failure) { setError(failure.message); }
  }, [apiBase]);
  useEffect(() => { if (visible) load(); }, [visible, load]);
  useEffect(() => {
    const changed = event => { revision.current += 1; setState(event.detail); setAsking(false); };
    window.addEventListener(CHANGED, changed);
    return () => window.removeEventListener(CHANGED, changed);
  }, []);
  const needsRenewal = state?.reconsent_required === true;
  const noticeVersion = state?.consent_version;
  const available = state?.available === true;
  useEffect(() => {
    // The always-visible header owns the automatic prompt; other switches open it on click.
    if (!visible || !autoPrompt || !available || !needsRenewal || !noticeVersion || prompted.current === noticeVersion) return;
    prompted.current = noticeVersion;
    const key = `coach.log-consent-prompt.v1:${apiBase}:${noticeVersion}`;
    try {
      if (sessionStorage.getItem(key)) return;
      sessionStorage.setItem(key, 'shown');
    } catch { /* The in-memory ref still prevents repeated prompts during this mount. */ }
    setAsking(true);
  }, [visible, autoPrompt, available, needsRenewal, noticeVersion, apiBase]);
  const change = async (enabled) => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${apiBase}/api/session-logs/auto`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(enabled ? { enabled, consent_version: state.consent_version } : { enabled }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 422) await load();
        throw new Error(typeof data.detail === 'string' ? data.detail : '설정을 바꾸지 못했습니다.');
      }
      setAsking(false);
      window.dispatchEvent(new CustomEvent(CHANGED, { detail: data }));
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  // Turning on always goes through the notice; turning off needs no confirmation.
  const toggle = checked => { setError(''); if (checked) setAsking(true); else change(false); };
  return { state, asking, busy, error, toggle, agree: () => change(true), decline: () => change(false), cancel: () => setAsking(false) };
}

export function ConsentDialog({ auto }) {
  return auto.asking ? <ConsentContents key={auto.state?.consent_version} auto={auto} /> : null;
}

function ConsentContents({ auto }) {
  const [agreed, setAgreed] = useState(false);
  const renewal = auto.state?.reconsent_required === true;
  const close = () => { if (!auto.busy) { setAgreed(false); auto.cancel(); } };
  return <Dialog open onOpenChange={(isOpen) => { if (!isOpen) close(); }}>
    <DialogContent className="max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>{renewal ? '로그 전송 동의 내용이 변경되었습니다' : '세션 로그를 자동으로 보낼까요?'}</DialogTitle>
        <DialogDescription>{renewal
          ? '업데이트로 전송하는 기록과 동의 내용이 바뀌어 재동의가 필요합니다. 현재 자동 보내기는 중지되어 있습니다. 변경 내용을 확인하고 다시 동의해야 켜집니다. 동의하지 않아도 앱을 계속 쓸 수 있습니다.'
          : '켜면 주행이 끝날 때마다 아래 기록이 앱을 만든 사람에게 자동으로 전달됩니다. iRacing과 LMU에 함께 적용됩니다. 읽어 보고 동의할 때만 켜집니다.'}</DialogDescription>
      </DialogHeader>
      {renewal && <section aria-label="변경된 내용" className="rounded-md border border-border bg-muted p-3 text-sm">
        <p className="font-semibold">이번 동의문에서 달라진 내용</p>
        <ul className="mt-1 list-disc space-y-1 pl-5">{(auto.state.consent_changes || []).map((text, index) => <li key={index}>{text}</li>)}</ul>
        <p className="mt-2">아래 전체 내용을 확인해 주세요. 다시 동의한 뒤 시작하는 주행부터 보냅니다.</p>
      </section>}
      <dl className="flex flex-col gap-3 text-sm leading-relaxed text-foreground">
        <div><dt className="font-semibold">왜 받나요</dt><dd>스포터·엔지니어 안내가 제때 나오고 끝까지 들렸는지, 연료·에너지 계산이 맞았는지 확인하고, 코너 분석·차 거동 판정과 레이스 중 주변 차와의 상황 분석을 더 정확하게 만들어 앱을 고치기 위해서입니다. 다른 목적으로 쓰지 않습니다.</dd></div>
        <div><dt className="font-semibold">보내는 것</dt><dd>안내마다 나온 시각·종류·끝까지 들렸는지와 끊긴 이유, 엔지니어 보고 문장과 그때의 측정값(랩, 연료, 간격, 순위, 트랙, 차량, 세션 종류, 게임), 앱 버전과 연결 상태 진단. iRacing은 트랙 종류·레이아웃·회전 방향·경기 옵션으로 오벌 여부도 구분합니다. 포장 오벌 시험 기능은 코션·재시작·본인 피트 허용·대열·배정 신호의 후보·보류·취소, 위트 무전의 무음 평가 이유·당시 주행 및 음성 점유 상태·가상 선택 문구도 보냅니다. 실제로 재생한 무전과 구분하며 새 음성은 재생하지 않습니다.</dd></div>
        <div><dt className="font-semibold">함께 보내는 것 · 내 주행 측정값</dt><dd>모든 세션에서, 앱이 주행 파일을 분석해 계산한 측정값 요약을 보냅니다. 랩타임과 유효 여부, 코너마다 제동·가속을 시작한 위치, 속도, ABS 개입, 차의 회전 반응, 그 랩의 날씨·노면·연료 조건입니다. 주행 파일 원본은 보내지 않습니다.</dd></div>
        <div><dt className="font-semibold">함께 보내는 것 · 주변 차 기록</dt><dd>레이스와 확인된 iRacing 포장 오벌 연습에서 내 앞뒤 200m 안에 있던 차들의 거리·순위·클래스·피트 여부(최대 초당 10번), 레이스에서는 모든 차의 순위와 랩 위치(초당 1번)도 넣습니다. 포장 오벌은 본인의 피트 허용·출발 대열 신호, 세션 상태·깃발·남은 시간·랩도 함께 기록합니다. 차는 번호로만 표시되고 이름은 들어가지 않습니다.</dd></div>
        <div><dt className="font-semibold">파일이 클 때</dt><dd>보낼 수 있는 크기를 넘으면 주변 차 기록을 먼저 빼고, 그래도 크면 내 주행 측정값도 뺍니다. 포장 오벌은 내 주행 측정값 요약부터 빼고, 주변 차 기록은 최대 6구간·1,200행의 일부 기록으로 줄여 남깁니다. 빠진 구간을 표시하며 전체 주행 기록으로 취급하지 않습니다. 그래도 공간이 부족하면 추가 자료를 뺍니다. 오벌 분류와 안내 기록은 기본 로그에 남습니다.</dd></div>
        <div><dt className="font-semibold">남는 용량에 함께 보내는 것 · 코너 거동 측정값</dt><dd>주변 차 기록이 없는 파일에는 같은 코너의 여러 유효 랩(최소 4개, 최대 8개)에서 기록된 시각·랩 위치·속도·핸들·브레이크·스로틀·회전율·횡가속도·종가속도·ABS 개입을 일부 넣습니다. 표시가 없는 구간도 포함하며, 기록 간격과 결측을 보존합니다. 기본 기록과 측정값 요약을 유지하고 전체 압축 파일이 1.4MB 이하일 때만 추가합니다. 공간이 부족하면 코너 묶음을 생략합니다.</dd></div>
        <div><dt className="font-semibold">보내지 않는 것</dt><dd>내 이름과 다른 드라이버 이름(“Driver 번호”로 바뀜), 레이팅, 세션 번호, 마이크로 한 질문과 답변, 음성, 주행 파일(IBT·DuckDB) 원본, 셋업 파일 이름, Google 키, Garage61 토큰, PC의 폴더 경로.</dd></div>
        <div><dt className="font-semibold">언제</dt><dd>이 설정을 켠 뒤 시작한 주행만, 마지막 기록 뒤 10분 이상 지나고 현재 세션이 아닐 때 주행 하나당 한 번 보냅니다. 앱을 바로 닫았다면 다음 실행 때 확인합니다. 내 주행 측정값은 계산에 시간이 걸려 차에서 내린 뒤에 만들어 보냅니다. 2분 미만이나 안내 10건 미만은 보내지 않지만, 실제 주행이 확인된 포장 오벌은 2분 이상이면 안내가 없어도 보냅니다.</dd></div>
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
        <Button type="button" variant="outline" disabled={auto.busy} onClick={close}>{renewal ? '나중에' : '켜지 않기'}</Button>
        {renewal && <Button type="button" variant="outline" disabled={auto.busy} onClick={auto.decline}>자동 보내기 끄기</Button>}
        <Button type="button" disabled={!agreed || auto.busy} onClick={auto.agree}>{auto.busy ? '켜는 중' : renewal ? '다시 동의하고 켜기' : '동의하고 켜기'}</Button>
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
    {state.reconsent_required && <p role="status" className="mt-2 text-sm text-warning">동의 내용 변경 · 재동의 필요 · 자동 보내기 중지. <Button type="button" variant="outline" size="sm" onClick={() => auto.toggle(true)}>변경 내용 확인</Button></p>}
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
