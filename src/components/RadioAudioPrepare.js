import React, { useCallback, useEffect, useState } from 'react';
import { Button } from './ui/button';

const STATE_TEXT = { IDLE: '준비 전', RUNNING: '받는 중', COMPLETE: '모두 준비됨', FAILED: '중단됨', STOPPED: '멈춤' };

// Fetches every engineer wording and number clip once, so nothing is synthesized while driving.
export default function RadioAudioPrepare({ apiBase }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const call = useCallback(async (path = '', method = 'GET') => {
    try {
      const response = await fetch(`${apiBase}/api/live-coach/radio-audio${path}`, { method });
      const data = await response.json();
      if (!response.ok) throw new Error('엔지니어 음성 준비 상태를 확인하지 못했습니다.');
      setStatus(data); setError('');
    } catch (failure) { setError(failure.message); }
  }, [apiBase]);
  useEffect(() => { call(); }, [call]);
  useEffect(() => {
    if (status?.state !== 'RUNNING') return undefined;
    const timer = setInterval(() => call(), 1500);
    return () => clearInterval(timer);
  }, [status?.state, call]);
  if (!status) return error ? <p role="alert" className="mt-3 text-sm text-warning">{error}</p> : null;
  const running = status.state === 'RUNNING';
  return <div className="mt-4 rounded-lg border border-border p-4" aria-label="엔지니어·스포터 음성 미리 합성">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm font-semibold text-foreground">엔지니어·스포터 음성 미리 합성 · {STATE_TEXT[status.state] || status.state}</p>
      <Button type="button" variant={running ? 'outline' : 'default'} disabled={status.state === 'COMPLETE'} onClick={() => call(running ? '/stop' : '/prepare', 'POST')}>
        {running ? '멈추기' : status.state === 'COMPLETE' ? '준비 완료' : status.ready ? '빠진 음성 합성' : '전부 합성해 두기'}
      </Button>
    </div>
    <progress className="mt-3 h-2 w-full" max={status.total} value={status.ready} aria-label="준비된 음성" />
    <p role="status" className="mt-2 text-sm text-foreground">{status.ready} / {status.total}개 준비됨{status.missing ? ` · 남은 ${status.missing}개, 약 ${status.characters_missing}자` : ''}</p>
    {status.spotter_voice && <p className="mt-1 text-xs text-muted-foreground">엔지니어 세 명과 지금 고른 스포터 목소리({status.spotter_voice})의 음성 수입니다. 다른 스포터 목소리를 고르면 그 목소리 기준으로 다시 셉니다.</p>}
    {status.message && <p className="mt-1 text-sm text-warning">{status.message}</p>}
    {error && <p role="alert" className="mt-1 text-sm text-warning">{error}</p>}
    <p className="mt-2 text-xs leading-relaxed text-muted-foreground">보통은 이 버튼이 필요 없습니다. 앱이 쓰는 음성은 설정 → 업데이트의 “빠진 음성 받기”로 GitHub에서 무료로 받습니다. 여기서는 Google 키로 음성을 직접 합성해 저장합니다. 모든 문구 변형과 숫자 문장(간격·순위·랩타임 등)을 한 번 받아 저장하면 주행 중에는 합성하지 않습니다. Google 음성 합성 요청이 발생하며 처음에는 수십 분 걸릴 수 있습니다. 받는 동안 다른 화면을 써도 되고, 중간에 멈춰도 받은 만큼 사용합니다. 준비 범위를 벗어난 숫자는 처음 쓰일 때 합성합니다.</p>
  </div>;
}
