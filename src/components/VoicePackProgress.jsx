import React, { useEffect, useState } from 'react';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';

const STAGES = { uploading: '파일 전송 중', checking: '음성 파일 검사 중', copying: '설치 폴더에 복사 중', verifying: '설치 파일 최종 확인 중', applying: '목소리 적용 중' };
const STEPS = [['uploading', '파일 전송'], ['checking', '음성 검사'], ['copying', '파일 복사'], ['verifying', '최종 확인']];
const bytes = value => `${(Math.max(0, value || 0)/1024/1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`;
const duration = seconds => `${Math.floor(seconds/60)}분 ${Math.floor(seconds%60)}초`;

export default function VoicePackProgress({ operation }) {
  const [, tick] = useState(0), running = operation?.state === 'RUNNING';
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => tick(value => value+1), 1000);
    return () => clearInterval(timer);
  }, [running]);
  if (!operation || operation.state === 'IDLE') return null;
  const selection = operation.kind === 'selection', failed = operation.state === 'FAILED';
  const title = failed ? (selection ? '목소리를 적용하지 못했습니다' : '음성팩 설치 실패')
    : running ? (selection ? '목소리 적용 중' : '음성팩 설치 중') : (selection ? '목소리 적용 완료' : '음성팩 설치 완료');
  const elapsed = (operation.elapsed_s || 0)+(running ? Math.max(0, (Date.now()-(operation.observedAt || Date.now()))/1000) : 0);
  const transfer = operation.stage === 'uploading', known = Number.isFinite(operation.total) && operation.total > 0;
  const done = Math.max(0, operation.done || 0), step = STEPS.findIndex(([key]) => key === operation.stage);
  return <Alert variant={failed ? 'destructive' : 'default'} role="status" aria-live="polite" className="mt-3" aria-label={selection ? '목소리 적용 현황' : '음성팩 설치 현황'}>
    <AlertTitle>{title}{operation.name ? ` · ${operation.name}` : ''}</AlertTitle>
    <AlertDescription className="flex min-w-0 flex-col gap-2">
      {!selection && running && <ol className="flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="음성팩 설치 단계">{STEPS.map(([key, label], index) => <li key={key} aria-current={key === operation.stage ? 'step' : undefined}>{index < step ? '✓ ' : `${index+1}. `}{label}{index === step ? ' · 진행 중' : ''}</li>)}</ol>}
      {running && <>
        <p>{STAGES[operation.stage] || '설치 응답 확인 중'}{operation.stage !== 'applying' && <> · {transfer ? `${bytes(done)}${known ? ` / ${bytes(operation.total)}` : ''}` : `${done.toLocaleString()}${known ? ` / ${operation.total.toLocaleString()}` : ''}개 확인`}</>}</p>
        <progress className="h-2 w-full" aria-label={selection ? '목소리 파일 확인 진행률' : '현재 설치 단계 진행률'} max={known ? operation.total : undefined} value={known ? Math.min(done, operation.total) : undefined} />
        <p className="text-xs">{transfer && known ? `파일 전송 ${Math.floor(done/operation.total*100)}% · 전송 후 음성 검사와 복사가 이어집니다.` : operation.stage === 'applying' ? '선택한 목소리를 적용하고 있습니다. 음성 파일 확인이 필요하면 처리 개수를 표시합니다.' : '음성 파일의 해시·형식·길이를 확인합니다. 큰 팩은 시간이 걸릴 수 있습니다.'}</p>
        {operation.connectionLost && <p>진행 상태 연결이 지연돼 다시 확인 중입니다. 마지막으로 확인된 수치를 표시합니다.</p>}
        {operation.legacy && <p>현재 서버는 세부 진행 상태를 지원하지 않습니다. 이 작업이 끝난 뒤 앱과 실행창을 다시 시작하면 표시됩니다.</p>}
      </>}
      {!running && !failed && <p>{selection ? '선택한 역할에 목소리를 적용했습니다.' : `${operation.imported?.name || '음성팩'} 설치 완료. 스포터 또는 피트월의 목소리 목록에서 선택하세요.`}</p>}
      {failed && <p>{operation.error || '연결 상태와 팩 파일을 확인하고 다시 시도하세요.'}</p>}
      <p className="text-xs">{running ? '경과' : '소요'} {duration(elapsed)}</p>
    </AlertDescription>
  </Alert>;
}
