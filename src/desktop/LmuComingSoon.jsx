import React from 'react';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '../components/ui/card';

export default function LmuComingSoon({ onNavigate }) {
  return <section className="wide-workspace" aria-label="LMU 셋업 실험 추후 공개">
    <Card><CardHeader><CardTitle>셋업 실험 · 추후 공개</CardTitle>
      <CardDescription>LMU 셋업 실험은 준비 중입니다. 다음 업데이트에서 공개합니다.</CardDescription></CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p>지금은 주행 요약·코너 비교·랩 분석·음성 코치를 iRacing과 같은 방식으로 쓸 수 있습니다.</p>
        <Button className="self-start" onClick={() => onNavigate('summary')}>주행 요약으로 가기</Button>
      </CardContent></Card>
  </section>;
}
