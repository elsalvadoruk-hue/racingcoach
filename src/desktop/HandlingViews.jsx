import React from 'react';
import { Badge } from '../components/ui/badge';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';

const phaseNames = { braking: '제동', rotation: '회전', drive: '가속' };
const shortNames = { understeer: '덜 돌아감', oversteer: '더 돌아감', countersteer: '반대 조향' };
const meanings = {
  understeer: '비슷한 속도·조향·페달 조건에서 내 다른 랩보다 차의 회전 반응이 작았습니다.',
  oversteer: '비슷한 속도·조향·페달 조건에서 내 다른 랩보다 차의 회전 반응이 컸습니다.',
  countersteer: '차가 도는 방향과 반대로 핸들을 돌린 상태가 관측됐습니다. 개인 기준과 별개입니다.',
};
const withNames = { braking: '제동 중', lift: '스로틀을 뗀 직후', power: '가속 중' };

function costText(value) {
  if (!Number.isFinite(value)) return '시간 차이 확인 필요';
  if (value === 0) return '시간 차이 0.00초';
  return `나온 랩이 ${Math.abs(value).toFixed(2)}초 ${value > 0 ? '느림' : '빠름'}`;
}

export function HandlingBadges({ items = [] }) {
  const kinds = ['understeer', 'oversteer', 'countersteer'].filter(kind => items.some(item => item.kind === kind));
  if (!kinds.length) return null;
  return <span className="handling-badges" aria-label="내 다른 랩 대비 차 거동">{kinds.map(kind =>
    <Badge key={kind} data-kind={kind} title={meanings[kind]} variant={kind === 'countersteer' ? 'outline' : kind === 'understeer' ? 'handling-under' : 'handling-over'}>{shortNames[kind]}</Badge>)}</span>;
}

function orderHandling(items) {
  return [...items].sort((a, b) => Number(Number.isFinite(b.time_cost_s)) - Number(Number.isFinite(a.time_cost_s)) ||
    (Number.isFinite(a.time_cost_s) && Number.isFinite(b.time_cost_s) ? b.time_cost_s - a.time_cost_s : 0) || b.laps - a.laps);
}

function HandlingObservation({ item }) {
  return <article className="handling-observation" data-handling-item aria-label={`${phaseNames[item.phase]} · ${shortNames[item.kind]}`}>
    <div className="handling-observation-heading"><Badge variant={item.kind === 'countersteer' ? 'outline' : item.kind === 'understeer' ? 'handling-under' : 'handling-over'}>{shortNames[item.kind]}</Badge><strong>{phaseNames[item.phase]} 구간</strong></div>
    <dl className="handling-facts"><div><dt>관측된 랩</dt><dd>{item.of_laps}랩 중 {item.laps}랩</dd></div><div><dt>코너 시간 차이</dt><dd>{costText(item.time_cost_s)}</dd></div></dl>
    {withNames[item.with] && <small>함께 관측된 조작: {withNames[item.with]}</small>}
    {item.observed_laps != null && item.observed_laps < item.of_laps && <small>{item.kind === 'countersteer' ? '반대 조향 관측 가능' : '전 구간 비교 가능'} {item.observed_laps}랩 · 부분 구간에서 관측한 결과도 포함합니다.</small>}
    {item.kind === 'countersteer' && <small>개인 기준과 별개로 관측했습니다.</small>}
    {item.approximate && <small>참고용(LMU는 회전량을 GPS로 추정)</small>}</article>;
}

export function HandlingDetail({ items = [], coverage = [] }) {
  if (!items.length && !coverage.length) return null;
  const known = coverage.reduce((sum, item) => sum + item.classifiable, 0);
  const unknown = coverage.reduce((sum, item) => sum + item.unknown, 0);
  return <section className="handling-detail" aria-label="차 거동"><h4>차 거동 · 내 다른 랩과 비교</h4>
    <p className="handling-basis">핸들을 꺾은 정도에 비해 차가 덜 또는 더 돌아간 랩을 표시합니다. 같은 기록의 다른 유효 랩과 비교합니다.</p>
    <small>위에서 선택한 기준 랩·Garage61 비교와는 별개입니다.</small>
    <small>검증 전 초기값 · 체감 대조 전입니다.</small>
    {coverage.length > 0 && <p className="handling-coverage">{known + unknown > 0 ? `개인 기준 전 구간 비교 ${known}/${known + unknown}회${unknown > 0 ? ` · ${unknown}회 확인 필요` : ''}` : '비교 대상 단계가 없습니다.'}</p>}
    <details className="handling-help"><summary>표시 뜻과 비교 범위</summary>
      <dl>{['understeer', 'oversteer', 'countersteer'].map(kind => <div key={kind}><dt>{shortNames[kind]}</dt><dd>{meanings[kind]}</dd></div>)}</dl>
      <p>같은 코너·같은 단계에서 비교합니다. 서로 다른 랩이나 단계에서 두 표시가 함께 나올 수 있습니다.</p>
      <p>비교 횟수는 랩별 제동·회전·가속 단계를 각각 셉니다. 자료가 부족한 구간은 확인 필요로 남깁니다.</p>
      <p>문턱은 검증 전 초기값입니다. 절대적인 언더·오버 판정이나 원인·셋업 효과를 확정하지 않습니다.</p>
    </details>
    {orderHandling(items).map((item, i) => <HandlingObservation key={`${item.kind}-${item.phase}-${i}`} item={item} />)}
  </section>;
}

export function repeatedHandling(items = []) {
  return orderHandling((items || []).filter(item => item.files >= 2)).slice(0, 3);
}

export function HandlingRepeats({ items = [] }) {
  const repeated = repeatedHandling(items);
  if (!repeated.length) return null;
  return <Card className="handling-repeat-card"><CardHeader><CardTitle>반복되는 거동</CardTitle><CardDescription>같은 차량·트랙의 여러 주행에서 관측했습니다.</CardDescription></CardHeader>
    <CardContent><ul>{repeated.map((item, i) => <li key={`${item.corner_id}-${item.kind}-${item.phase}-${i}`}><strong>{item.label} · 지난 {item.files}개 주행에서 반복</strong><HandlingObservation item={item} /></li>)}</ul></CardContent>
    <CardFooter><small>문턱은 검증 전 초기값입니다. 시간 차이는 관측된 차이이며 원인이나 셋업 효과를 확정하지 않습니다.</small></CardFooter></Card>;
}
