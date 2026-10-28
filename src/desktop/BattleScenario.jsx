import React from 'react';
import { ArrowRight, Play } from 'lucide-react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { cn } from '../lib/utils';
import { SIM_STATUS } from './battleReport';

const value = (n, unit = '') => Number.isFinite(n) ? `${n.toFixed(1)}${unit}` : '—';
const gap = n => !Number.isFinite(n) ? '—' : `${n >= 0 ? '상대가' : '내가'} ${Math.abs(n).toFixed(1)}m 앞`;

// Never extend a conditional trajectory beyond its supported comparison horizon.
export function trajectoryAt(frames, t) {
  if (!frames?.length || t < frames[0].t - .001 || t > frames.at(-1).t + .001) return null;
  let index = frames.findIndex(f => f.t >= t - .001);
  if (index < 0) index = frames.length - 1;
  return { frame: frames[index], index };
}

export function selectedRun(shown, response) {
  if (!shown) return null;
  const run = shown.alt.responses.find(r => r.id === response) || shown.alt.responses[0];
  const baseline = shown.corner.simulation.baselines.find(r => r.id === run?.id)?.trajectory;
  return run && baseline ? { ...run, baseline, alt: shown.alt, corner: shown.corner } : null;
}

export default function BattleScenario({ corner, active, selected, response, onShow, onPlay, onOpenCorner }) {
  const alt = active ? selected : corner.alternatives[0];
  const run = selectedRun({ corner, alt }, response);
  const end = run.trajectory.at(-1), base = run.baseline.at(-1), simulation = corner.simulation;
  const delta = end.distance_m - base.distance_m;
  return <article className={cn('battle-advice', active && 'is-active')} aria-label={`${corner.label} ${corner.lap}랩 대응 비교`}>
    <header><button type="button" className="battle-advice-title" onClick={() => onShow(corner, alt)} aria-pressed={active}>
      <strong>{corner.label} · {corner.lap}랩</strong><Badge variant="secondary">{alt.horizon_s.toFixed(1)}초 비교</Badge>
    </button></header>
    <p className="battle-advice-situation">진입 때 {gap(corner.gap_entry_m)}</p>
    <ToggleGroup type="single" orientation="vertical" variant="outline" size="sm" value={alt.id} onValueChange={id => {
      const chosen = corner.alternatives.find(a => a.id === id); if (chosen) onShow(corner, chosen);
    }} aria-label={`${corner.label} 대응 선택`} className="battle-action-options">
      {corner.alternatives.map(a => <ToggleGroupItem key={a.id} value={a.id}>{a.label}</ToggleGroupItem>)}
    </ToggleGroup>
    <p className="battle-warning">{alt.rankable ? '모형 안에서 전진거리 증가' : SIM_STATUS[alt.status]}</p>
    <table><thead><tr><th scope="col">{alt.horizon_s.toFixed(1)}초 뒤</th><th scope="col">기본 대응 모형</th><th scope="col">선택한 대응</th></tr></thead><tbody>
      <tr><th scope="row">속도</th><td>{value(base.speed_kph, ' km/h')}</td><td>{value(end.speed_kph, ' km/h')}</td></tr>
      <tr><th scope="row">상대와 간격</th><td>{gap(base.gap_m)}</td><td>{gap(end.gap_m)}</td></tr>
      <tr><th scope="row">전진거리 차이</th><td>기준</td><td>{delta > 0 ? '+' : ''}{value(delta, 'm')}</td></tr>
    </tbody></table>
    <p className="battle-note">위 숫자는 같은 모형끼리 비교합니다. 실제 주행은 지도에 실선으로 함께 표시합니다.</p>
    <details className="battle-method"><summary>오차와 계산 범위</summary>
      <p>이 장면의 기본 모형과 실제의 최대 위치 차이 {value(simulation.baseline_error_m, 'm')}. 별도 랩의 차량 반응 오차(90백분위) {value(simulation.validation?.distance_p90_m, 'm')}.</p>
      <p>상대 반응별 전진거리 차이 {alt.progress_range_m.map(n => value(n, 'm')).join(' ~ ')}. 위치 오차 기준 {value(alt.position_error_m, 'm')}보다 작은 차이는 우열을 정하지 않습니다. 성공 확률이나 신뢰구간은 아닙니다.</p>
      <p>도로 경계를 모르므로 옆으로 피하거나 추월하는 경로는 계산하지 않습니다. 변속·타이어·노면·공기 흐름은 단순화하며 상대 차량의 반응도 가정입니다.</p>
      {simulation.multi_car_unmodelled && <p>다른 가까운 차량이 있어 두 차량만의 계산으로 우열을 정하지 않습니다.</p>}
      {simulation.omitted?.length > 0 && <p>앞차 제약으로 기본 대응과 차이가 없는 조작 {simulation.omitted.length}개는 생략했습니다.</p>}
    </details>
    <div className="battle-scenario-buttons"><Button variant="outline" size="sm" onClick={() => onPlay(corner, alt)}><Play data-icon="inline-start" />비교 재생</Button>
      <Button variant="ghost" size="sm" onClick={() => onOpenCorner(corner, corner.lap)}>실제 코너 보기<ArrowRight data-icon="inline-end" /></Button></div>
  </article>;
}
