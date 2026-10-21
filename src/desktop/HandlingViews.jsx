import React from 'react';
import { Badge } from '../components/ui/badge';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';

const phaseNames = { braking: '제동', rotation: '회전', drive: '가속' };
const kindNames = { understeer: '평소보다 회전 적음', oversteer: '평소보다 회전 많음', countersteer: '반대 조향 관측' };
const withNames = { braking: '제동 중', lift: '스로틀을 뗀 직후', power: '가속 중' };

function costText(value) {
  if (!Number.isFinite(value)) return '시간 차이 확인 필요';
  if (value === 0) return '시간 차이 0.00초';
  return `나온 랩이 ${Math.abs(value).toFixed(2)}초 ${value > 0 ? '느림' : '빠름'}`;
}

export function HandlingBadges({ items = [] }) {
  return <span className="handling-badges">{[...new Set(orderHandling(items).map(item => item.kind))].map(kind =>
    <Badge key={kind} variant={kind === 'countersteer' ? 'outline' : kind === 'understeer' ? 'handling-under' : 'handling-over'}>{kind === 'countersteer' ? '반대 조향' : kindNames[kind]}</Badge>)}</span>;
}

function orderHandling(items) {
  return [...items].sort((a, b) => Number(Number.isFinite(b.time_cost_s)) - Number(Number.isFinite(a.time_cost_s)) ||
    (Number.isFinite(a.time_cost_s) && Number.isFinite(b.time_cost_s) ? b.time_cost_s - a.time_cost_s : 0) || b.laps - a.laps);
}

function HandlingObservation({ item }) {
  return <><span>{phaseNames[item.phase]} 중 {kindNames[item.kind]}{withNames[item.with] && ` (${withNames[item.with]})`} · {item.of_laps}랩 중 {item.laps}랩 · {costText(item.time_cost_s)}</span>
    {item.observed_laps != null && item.observed_laps < item.of_laps && <small>{item.kind === 'countersteer' ? '반대 조향 관측 가능' : '전 구간 비교 가능'} {item.observed_laps}랩 · 일부 구간의 관측도 포함합니다.</small>}
    {item.approximate && <small>참고용(LMU는 회전량을 GPS로 추정)</small>}</>;
}

export function HandlingDetail({ items = [], coverage = [] }) {
  if (!items.length && !coverage.length) return null;
  const known = coverage.reduce((sum, item) => sum + item.classifiable, 0);
  const unknown = coverage.reduce((sum, item) => sum + item.unknown, 0);
  return <section className="handling-detail" aria-label="차 거동"><h4>차 거동</h4><small>문턱은 검증 전 초기값입니다. 개인 기준과의 차이이며 절대적인 언더·오버 판정이 아닙니다.</small>
    {coverage.length > 0 && <small>{known + unknown > 0 ? `개인 기준 전 구간 비교 ${known}/${known + unknown}회${unknown > 0 ? ` · ${unknown}회 확인 필요` : ''}` : '비교 대상 단계가 없습니다.'}</small>}
    {orderHandling(items).map((item, i) => <p key={`${item.kind}-${item.phase}-${i}`}><HandlingObservation item={item} />
      {item.kind === 'countersteer' && <small>개인 기준과 별개로 관측했습니다.</small>}</p>)}</section>;
}

export function HandlingRepeats({ items = [] }) {
  const repeated = orderHandling(items.filter(item => item.files >= 2)).slice(0, 3);
  if (!repeated.length) return null;
  return <Card className="handling-repeat-card"><CardHeader><CardTitle>반복되는 거동</CardTitle><CardDescription>같은 차량·트랙의 여러 주행에서 관측했습니다.</CardDescription></CardHeader>
    <CardContent><ul>{repeated.map((item, i) => <li key={`${item.corner_id}-${item.kind}-${item.phase}-${i}`}><strong>{item.label} · 지난 {item.files}개 주행에서 반복</strong><HandlingObservation item={item} /></li>)}</ul></CardContent>
    <CardFooter><small>문턱은 검증 전 초기값입니다. 시간 차이는 관측된 차이이며 원인이나 셋업 효과를 확정하지 않습니다.</small></CardFooter></Card>;
}
