import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from './ui/card';

const finite = value => typeof value === 'number' && Number.isFinite(value);
const measured = value => finite(value) && value >= 0;
const goals = { finish: '무급유 완주', reserve: '무급유 완주 + 안전 여유', off: '절약 피드백 끄기' };
const methods = { LAP_LIMIT: '경기 랩 제한', LEADER_FINISH_ESTIMATE: '선두 완주 시점 추정',
  TIMED_ESTIMATE: '경기 시간과 관측 페이스 추정', PLAYER_FINAL_LAP: '본인 마지막 랩 관측' };

// Uses the server's estimate and goal, so an unsent settings edit cannot relabel an older estimate.
export default function FuelStrategy({ view }) {
  if (!view) return null;
  const count = Number.isInteger(view.fuel_observed_laps) ? view.fuel_observed_laps : 0;
  const fuelKnown = measured(view.fuel_l);
  const useKnown = finite(view.fuel_l_per_lap) && view.fuel_l_per_lap > 0 && count > 0;
  const distanceKnown = measured(view.remaining_laps);
  const ready = fuelKnown && useKnown && distanceKnown;
  const mode = view.fuel_save_mode || 'finish';
  const short = ready && measured(view.fuel_finish_shortfall_l) ? view.fuel_finish_shortfall_l : null;
  const saving = ready && measured(view.fuel_goal_save_pct) ? view.fuel_goal_save_pct : null;
  return <Card asChild><section aria-label="연료 전략 판단 근거" className="mt-4">
    <CardHeader><CardTitle asChild><h3>연료 전략 판단 근거</h3></CardTitle>
      <CardDescription>선택 목표 · {goals[mode] || '미확인'}</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-2">
      <p>{fuelKnown ? `현재 연료 ${view.fuel_l.toFixed(2)} L` : '현재 연료 미확인'} · {useKnown ? `관측 ${count}랩 · ${view.fuel_l_per_lap.toFixed(3)} L/랩` : '소비량 관측 대기'}</p>
      {count > 0 && count < 3 && <p className="text-sm text-muted-foreground">관측 랩이 적어 잠정 계산입니다. 추가 랩에 따라 전략이 달라질 수 있습니다.</p>}
      <p>{distanceKnown ? `예상 남은 거리 ${view.remaining_laps.toFixed(2)}랩` : '완주 거리 미확인'} · {methods[view.finish_method] || '거리 계산 근거 미확인'}</p>
      {measured(view.reserve_laps) && <p>안전 여유 {view.reserve_laps.toFixed(1)}랩{useKnown && measured(view.fuel_reserve_l) ? ` · ${view.fuel_reserve_l.toFixed(2)} L` : ''}{mode === 'finish' ? ' · 완주 절약 목표에는 제외, 급유량에는 포함' : mode === 'reserve' ? ' · 절약 목표와 급유량에 포함' : ' · 급유량에 포함'}</p>}
      {!ready ? <p className="text-sm text-muted-foreground">연료·소비량·완주 거리가 확인되면 절약과 급유 대안을 함께 표시합니다.</p> : <>
        <p>{short == null ? '완주 부족량 미확인' : short > .1 ? `현재 소비량 유지 시 완주 연료 약 ${short.toFixed(2)} L 부족` : '현재 소비량으로 완주 연료 충족 예상'}</p>
        <p>{mode === 'off' ? '절약 안내는 꺼져 있습니다.' : saving == null ? '선택 목표의 절약률 미확인' : saving <= 0 ? '선택 목표에 추가 절약은 필요하지 않습니다.' : view.fuel_save_target_l_per_lap != null ? `무급유 대안 · 선택 목표까지 약 ${saving.toFixed(1)}% 절약` : `선택 목표에는 약 ${saving.toFixed(1)}% 절약이 필요해 현재 절약 안내 범위를 벗어납니다.`}</p>
        <p>{measured(view.fuel_to_add_l) ? `급유 대안 · 현재 소비량 유지 시 안전 여유를 포함해 총 ${view.fuel_to_add_l.toFixed(2)} L 추가 필요` : '급유량 미확인'}{Number.isInteger(view.stops_needed) && view.stops_needed > 0 ? ` · 예상 ${view.stops_needed}회 정차` : ''}</p>
        {(view.finish_may_extend || view.finish_may_shorten) && <p className="text-sm text-muted-foreground">종료선 통과 시점에 따라 예상 완주 거리가 한 랩 달라질 수 있습니다.</p>}
        <p className="text-sm text-muted-foreground">절약과 급유는 다른 대안입니다. 어느 쪽이 더 빠른지는 확정하지 않습니다.</p>
      </>}
    </CardContent>
  </section></Card>;
}
