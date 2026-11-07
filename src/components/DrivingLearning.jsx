import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from './ui/card';
import { Badge } from './ui/badge';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from './ui/empty';

const groups = [['technique','제동·가속'],['settings','BB·ABS·TC'],['chains','연속 코너'],['impacts','연석·충격']];
const reasons = {INCOMPLETE_WINDOW:'구간 전체 미기록',SAMPLE_GAP:'기록 간격·진행 방향 불일치',
  UNSAFE_OR_UNCONFIRMED_STATE:'안전 상태 미확인',INCIDENT:'구간 내 인시던트',CONTROL_CHANGED:'구간 도중 조작 설정 변경',
  CONDITION_CHANGED:'노면·컴파운드 변경',INPUT_MISSING:'필수 채널 없음',SESSION_ID_MISSING:'세션 식별 정보 없음',CHANNELS_UNSUPPORTED:'지원 채널 부족'};

export default function DrivingLearning({ learning, connected = true }) {
  const result = connected ? learning : null;
  const items = groups.flatMap(([key,label]) => (result?.[key] || []).map(item => ({...item,group:label})));
  const exclusions = Object.entries(result?.rejections || result?.last_rejections || {}).filter(([,n])=>n>0);
  return <Card asChild><section aria-label="개인 주행 학습">
    <CardHeader><div className="flex flex-wrap items-center gap-2"><CardTitle>개인 주행 학습</CardTitle>
      <Badge variant="secondary">{!result ? '주행 대기' : result.state === 'UNSUPPORTED' ? '채널 확인 필요' : result.state === 'CONFIRMED' ? '반복 이득 확인' : '학습 중'}</Badge></div>
      <CardDescription>비슷한 조건의 내 통과를 비교합니다. 코너와 연결 구간의 이득은 합산하지 않습니다.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      {result && <p>저장 구간 {result.observations || 0}회 · 주변 차량까지 확인된 구간 {result.eligible || 0}회</p>}
      {items.length ? <ul className="flex flex-col gap-3">{items.map((item,i)=><li key={`${item.group}-${item.window}-${i}`}>
        <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{item.group}</Badge><strong>{(item.members || []).join(' → ') || '측정 구간'}</strong></div>
        <p>{item.text}</p><p className="text-muted-foreground">{Number.isFinite(item.gain_s) ? `관측 이득 ${item.gain_s.toFixed(2)}초 · ` : ''}{item.pairs}쌍 비교{item.validation_pairs ? ` · 후반 ${item.validation_pairs}쌍 재확인` : ''}
          {item.kerb_verified === false ? ' · 연석 여부 미확인' : ''}</p>
        {item.values && item.deltas_high_minus_low && <p className="text-muted-foreground">{item.control?.toUpperCase()} {item.values[0]} → {item.values[1]}
          {Number.isFinite(item.deltas_high_minus_low.time_s) ? ` · 구간 시간 ${item.deltas_high_minus_low.time_s>=0?'+':''}${item.deltas_high_minus_low.time_s.toFixed(2)}초` : ''}
          {Number.isFinite(item.deltas_high_minus_low.end_speed) ? ` · 마지막 속도 ${(item.deltas_high_minus_low.end_speed*3.6).toFixed(1)}km/h 차이` : ''}</p>}
      </li>)}</ul> : <Empty><EmptyHeader><EmptyTitle>{result?.state==='UNSUPPORTED' ? '이 기록은 필요한 채널이 부족합니다' : '반복 비교를 모으고 있습니다'}</EmptyTitle>
        <EmptyDescription>최소 4쌍의 서로 다른 통과와 이후 기록의 재확인이 필요합니다. 주변 차량이나 조건을 모르면 추천을 보류합니다.</EmptyDescription></EmptyHeader></Empty>}
      {exclusions.length>0 && <details><summary>최근 제외 사유</summary><ul>{exclusions.map(([key,n])=><li key={key}>{reasons[key] || '필수 관측 조건 부족'} · {n}회</li>)}</ul></details>}
      {result?.failure && <p role="status">학습 기록을 읽거나 저장하지 못했습니다. 기존 주행 기능은 계속 사용할 수 있습니다.</p>}
      {result?.dropped_windows>0 && <p>학습 처리 대기로 제외한 구간 {result.dropped_windows}회</p>}
    </CardContent>
    <CardFooter><p className="text-muted-foreground">반복 관측 결과이며 최적값이나 변경 효과의 인과관계를 확정하지 않습니다. 현재 조건이 맞으면 선택한 학습 음성으로 다음 코너 전에 안내합니다.</p></CardFooter>
  </section></Card>;
}
