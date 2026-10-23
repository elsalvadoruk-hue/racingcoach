import React, { useState } from 'react';
import { Badge } from '../components/ui/badge';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';

const phaseNames = { braking: '제동', rotation: '회전', drive: '가속' };
const shortNames = { understeer: '회전 반응 작음', oversteer: '회전 반응 큼', countersteer: '반대 방향 조향' };
const meanings = {
  understeer: '비슷한 속도·횡가속도·페달 조건에서 내 다른 랩보다 차의 회전 반응이 작았습니다.',
  oversteer: '비슷한 속도·횡가속도·페달 조건에서 내 다른 랩보다 차의 회전 반응이 컸습니다.',
  countersteer: '차가 도는 방향과 반대로 핸들을 돌린 상태가 관측됐습니다. 개인 기준과 별개입니다.',
};
const withNames = { braking: '제동 중', lift: '스로틀을 뗀 직후', power: '가속 중' };
// Mirrors handling.EVIDENCE; older saved results have no evidence field.
const evidenceByKind = { understeer: 'compared', oversteer: 'reference', countersteer: 'measured' };
const evidenceOf = item => item.evidence || evidenceByKind[item.kind];
const referenceNote = '참고 표시 · 이 종류는 아직 근거가 약해서 우연일 가능성이 큽니다.';
export const HANDLING_EXPLANATION_KEY = 'coach.handling.explanation-open.v1';

// What happened, in one plain sentence per kind.
const happened = {
  understeer: '이 구간에서 비슷한 조건으로 지난 내 다른 랩보다 차체가 방향을 바꾸는 반응이 작았습니다.',
  oversteer: '이 구간에서 비슷한 조건으로 지난 내 다른 랩보다 차체가 방향을 바꾸는 반응이 컸습니다.',
  countersteer: '차가 도는 반대쪽으로 핸들을 돌린 순간이 있었습니다. 이 관측만으로 뒤 타이어가 미끄러졌다고 확정하지 않습니다.',
};

function costText(value) {
  if (value === 0) return '평소와 같음';
  return `평소보다 ${Math.abs(value).toFixed(2)}초 ${value > 0 ? '느림' : '빠름'}`;
}

// How to read it: what the numbers can and cannot say, without judging the driving.
function readingText(item) {
  if (item.time_link === 'slower') return '이게 나온 랩은 이 코너에서 실제로 느렸습니다. 다음 주행에서 줄여 볼 만한 후보입니다.';
  if (item.laps <= 2) return '한두 랩에서만 나온 일이라 우연일 수 있습니다. 다음 주행에서도 반복되는지 지켜보세요.';
  if (!Number.isFinite(item.time_cost_s)) return '시간에 준 영향은 알 수 없습니다. 반복되는지 정도만 참고하세요.';
  if (item.time_cost_s < 0) return '이게 나온 랩이 오히려 빨랐지만, 이렇게 달리는 게 더 낫다는 뜻은 아닙니다. 그 랩이 빨랐던 다른 이유가 섞여 있을 수 있습니다.';
  if (item.time_cost_s > 0) return '이게 나온 랩이 조금 느렸지만, 이 때문인지는 확인되지 않았습니다. 다른 이유로 느렸을 수 있습니다.';
  return '시간 차이가 없었습니다. 큰 문제는 아닌 것으로 보입니다.';
}

export function HandlingBadges({ items = [] }) {
  const kinds = ['understeer', 'oversteer', 'countersteer'].filter(kind => items.some(item => item.kind === kind));
  if (!kinds.length) return null;
  return <span className="handling-badges" aria-label="내 다른 랩 대비 차 거동">{kinds.map(kind =>
    <Badge key={kind} data-kind={kind} data-evidence={evidenceByKind[kind]} title={evidenceByKind[kind] === 'reference' ? `${meanings[kind]} ${referenceNote}` : meanings[kind]}
      variant={kind === 'countersteer' ? 'outline' : kind === 'understeer' ? 'handling-under' : 'handling-reference'}>{shortNames[kind]}</Badge>)}</span>;
}

export function orderHandling(items) {
  return [...items].sort((a, b) => Number(evidenceOf(a) === 'reference') - Number(evidenceOf(b) === 'reference') ||
    Number(b.time_link === 'slower') - Number(a.time_link === 'slower') ||
    Number(Number.isFinite(b.time_cost_s)) - Number(Number.isFinite(a.time_cost_s)) ||
    (Number.isFinite(a.time_cost_s) && Number.isFinite(b.time_cost_s) ? b.time_cost_s - a.time_cost_s : 0) || b.laps - a.laps);
}

export function HandlingObservation({ item }) {
  return <article className="handling-observation" data-handling-item aria-label={`${phaseNames[item.phase]} · ${shortNames[item.kind]}`}>
    <div className="handling-observation-heading"><Badge title={evidenceOf(item) === 'reference' ? referenceNote : meanings[item.kind]} variant={item.kind === 'countersteer' ? 'outline' : item.kind === 'understeer' ? 'handling-under' : 'handling-reference'}>{shortNames[item.kind]}</Badge><strong>{phaseNames[item.phase]} 구간</strong>{item.time_link === 'slower' && <Badge variant="outline">느린 랩과 겹침</Badge>}</div>
    <p className="handling-happened">{happened[item.kind]}</p>
    <dl className="handling-facts"><div><dt>나온 랩</dt><dd>{item.of_laps}랩 중 {item.laps}랩</dd></div>
      {Number.isFinite(item.time_cost_s) && <div><dt>나온 랩의 이 코너 시간</dt><dd>{costText(item.time_cost_s)}</dd></div>}</dl>
    <p className="handling-reading" data-time-link={item.time_link || 'unknown'}>{readingText(item)}</p>
    {withNames[item.with] && <small>그때 조작: {withNames[item.with]}</small>}
    {item.observed_laps != null && item.observed_laps < item.of_laps && <small>일부 랩은 구간의 일부만 비교했습니다.</small>}
    {evidenceOf(item) === 'reference' && <small data-evidence-note>{referenceNote}</small>}
    {item.approximate && <small>참고용(LMU는 회전량을 GPS로 추정)</small>}</article>;
}

function coverageText(known, unknown) {
  const total = known + unknown;
  if (!total) return '비교할 단계가 없습니다.';
  const counted = `랩별 제동·회전·가속 ${total}번`;
  if (!known) return `이 코너는 비슷하게 지난 내 다른 랩이 부족해서 평소와 비교하지 못했습니다(${counted} 중 0번 비교).`;
  if (!unknown) return `${counted}을 모두 평소와 비교했습니다.`;
  return `${counted} 중 ${known}번을 평소와 비교했습니다. 나머지 ${unknown}번은 비슷하게 지난 랩이 부족했습니다.`;
}

export function HandlingDetail({ items = [], coverage = [] }) {
  const [explanationOpen, setExplanationOpen] = useState(() => {
    try { return localStorage.getItem(HANDLING_EXPLANATION_KEY) !== 'false'; } catch { return true; }
  });
  if (!items.length && !coverage.length) return null;
  const known = coverage.reduce((sum, item) => sum + item.classifiable, 0);
  const unknown = coverage.reduce((sum, item) => sum + item.unknown, 0);
  return <section className="handling-detail" aria-label="차 거동"><details className="handling-intro" open={explanationOpen} onToggle={event => {
    const next = event.currentTarget.open;
    setExplanationOpen(next);
    try { localStorage.setItem(HANDLING_EXPLANATION_KEY, String(next)); } catch { /* Keep this view usable when storage is unavailable. */ }
  }}><summary><h4>차 거동 · 내 다른 랩과 비교</h4><span>{explanationOpen ? '설명 접기' : '설명 보기'}</span></summary><div className="handling-intro-content">
    <p className="handling-basis">같은 기록의 내 다른 랩과 비슷한 조건에서 차체가 방향을 바꾸는 반응을 비교합니다. 회전 반응이 작으면 평소보다 덜, 크면 더 방향을 바꾼 것입니다. 회전 반경이나 뒤 타이어의 미끄러짐을 확정하는 표시는 아닙니다.</p>
    <small>잘했다·못했다를 판정하지 않습니다. 여러 랩에서 반복되고 그 랩이 느렸다면 살펴볼 만합니다.</small>
    <small>위에서 선택한 기준 랩·Garage61 비교와는 별개입니다. 아직 다듬는 중인 분석이라 참고용으로 보세요.</small>
    {coverage.length > 0 && <p className="handling-coverage">{coverageText(known, unknown)}</p>}
    {unknown > 0 && <small>랩마다 속도·페달·공략이 조금씩 달라서 흔히 생깁니다. 문제가 있다는 뜻도, 없다는 뜻도 아닙니다.</small>}
    <details className="handling-help"><summary>표시 뜻과 비교 범위</summary>
      <dl>{['understeer', 'oversteer', 'countersteer'].map(kind => <div key={kind}><dt>{shortNames[kind]}</dt><dd>{meanings[kind]}</dd></div>)}</dl>
      <p>같은 코너·같은 단계에서 비교합니다. 서로 다른 랩이나 단계에서 두 표시가 함께 나올 수 있습니다.</p>
      <p>비교 횟수는 랩별 제동·회전·가속 단계를 각각 셉니다. 단계 시간의 90% 이상을 다른 랩과 비교했으면 비교한 단계로 셉니다. 그보다 적으면 확인 필요로 남깁니다.</p>
      <p>회전 반응 큼은 참고로 표시합니다. 기록 5개로 평가했을 때 회전 반응 작음보다 근거가 약했습니다.</p>
      <p>시간 연결은 랩 전체의 느려짐과 단계 시작 속도 영향을 보정해 확인합니다. 위의 초 단위 시간 차이는 보정 전 관측값입니다. 시간 연결도 원인을 확정하지 않습니다.</p>
      <p>문턱은 검증 전 초기값입니다. 절대적인 언더·오버 판정이나 원인·셋업 효과를 확정하지 않습니다.</p>
    </details>
    </div></details>
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
