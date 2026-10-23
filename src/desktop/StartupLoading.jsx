import React from 'react';
import { Activity, AlertCircle } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { AnalysisProgressBar } from './AnalysisProgress';

export default function StartupLoading({ game, busy, progress, error, onRetry, onChoose, onSettings }) {
  const failed = !busy && Boolean(error);
  return <Card className="startup-loading" aria-label="시작 준비" aria-busy={busy}>
    <CardHeader>
      <div className="startup-symbol">{failed ? <AlertCircle aria-hidden="true" /> : <Activity aria-hidden="true" />}</div>
      <CardTitle role="heading" aria-level={2}>{failed ? '주행을 불러오지 못했습니다' : '주행 분석을 준비하고 있습니다'}</CardTitle>
      <CardDescription>{game === 'lmu' ? 'LMU' : 'iRacing'} · 최근 주행과 비교 기준을 확인합니다.</CardDescription>
    </CardHeader>
    <CardContent>
      {failed ? <p className="startup-error" role="alert">{error}</p> : <>
        <AnalysisProgressBar view={progress || { percent: null, label: '주행 파일·비교 기준 확인 중' }} />
        <p className="startup-description">기록이 길면 준비에 시간이 걸릴 수 있습니다. 분석이 끝나면 주행 화면이 자동으로 열립니다.</p>
      </>}
    </CardContent>
    <CardFooter>
      {failed && <Button onClick={onRetry}>다시 시도</Button>}
      <Button variant="outline" onClick={onChoose}>다른 주행 선택</Button>
      <Button variant="ghost" onClick={onSettings}>연결 상태 확인</Button>
    </CardFooter>
  </Card>;
}
