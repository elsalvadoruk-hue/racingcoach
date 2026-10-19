import { Card, CardHeader, CardTitle, CardDescription, CardContent } from './ui/card';
import { Field, FieldGroup, FieldSet, FieldLegend, FieldLabel } from './ui/field';
import { Separator } from './ui/separator';
import { LiveHeader, LiveMetric } from './LivePrimitives';
import { Play, RadioTower, Square, Volume2 } from 'lucide-react';
import { Button } from './ui/button';
import { NativeSelect } from './ui/native-select';
import { Input } from './ui/input';
import React from 'react';

export const ENGINEERS = {
  coach: { label: '주행 코치', voice: 'Algenib', style: 'border-primary/30 bg-primary/10 text-primary', description: '코너 느낌과 다음 주행 행동' },
  race: { label: '레이스 엔지니어', voice: 'Charon', style: 'border-accent/30 bg-accent/10 text-foreground', description: '같은 클래스 순위·앞뒤 페이스' },
  strategy: { label: '전략 엔지니어', voice: 'Orus', style: 'border-warning/30 bg-warning/10 text-warning', description: '완주 연료·급유량·피트 복귀 예측' },
};

export const PIT_WALL_KEY = 'iracing.voice.pitWall.v1';
export function readPitPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(PIT_WALL_KEY));
    return { level: ['important', 'all', 'off'].includes(saved?.level) ? saved.level : 'important',
      fuel_save_mode: ['off', 'finish', 'reserve'].includes(saved?.fuel_save_mode) ? saved.fuel_save_mode : 'finish',
      reserve_laps: typeof saved?.reserve_laps === 'number' && saved.reserve_laps >= 0 && saved.reserve_laps <= 3 ? saved.reserve_laps : .5,
      pit_loss_s: typeof saved?.pit_loss_s === 'number' && saved.pit_loss_s > 0 && saved.pit_loss_s <= 600 ? saved.pit_loss_s : null,
      pit_rule: ['CONCURRENT', 'SEQUENTIAL'].includes(saved?.pit_rule) ? saved.pit_rule : 'UNKNOWN',
      hold_enabled: saved?.hold_enabled !== false,
      hold_lead_s: typeof saved?.hold_lead_s === 'number' && saved.hold_lead_s >= 0 && saved.hold_lead_s <= 5 ? saved.hold_lead_s : 1 };
  } catch { return { level: 'important', reserve_laps: .5, pit_loss_s: null, fuel_save_mode: 'finish', pit_rule: 'UNKNOWN', hold_enabled: true, hold_lead_s: 1 }; }
}
const value = (number, unit = '', digits = 1) => number == null ? '수집 중' : `${Number(number).toFixed(digits)}${unit}`;

export default function PitWall({ enabled, onToggle, preferences, onChange, view, engineers, onTestVoice, busy, speaking }) {
  const target = preferences.fuel_save_mode === 'off' ? '피드백 꺼짐' : view?.fuel_save_target_l_per_lap == null
    ? view?.fuel_l_per_lap != null && view?.remaining_laps != null ? view?.fuel_save_feasible === false ? '절약으로는 불가 · 급유 필요' : '현재 계산상 절약 불필요' : '완주 거리·소비량 수집 중'
    : value(view.fuel_save_target_l_per_lap, 'L/랩', 2);
  return <Card asChild><section aria-label="레이스 피트월" className="coach-panel">
    <LiveHeader title="레이스 피트월" topic="pitWall" icon={RadioTower} description="주행 코치 · 레이스 엔지니어 · 전략 엔지니어"
      action={<Button variant={enabled ? 'outline' : 'default'} aria-pressed={enabled} onClick={onToggle}>{enabled ? <Square data-icon="inline-start" /> : <Play data-icon="inline-start" />}{enabled ? '자동 보고 중지' : '자동 보고 시작'}</Button>} />
    <p role="status" className="text-sm text-muted-foreground">{enabled && preferences.level !== 'off' ? speaking ? '엔지니어 발화 중 · 다음 보고는 순서대로 전달' : view?.active ? '자동 보고 활성 · 상황 변화를 확인 중' : '자동 보고 활성 · 진행 중인 Race 세션 대기' : '자동 보고 꺼짐 · 음성 또는 텍스트 질문은 사용 가능'}</p>
    <div className="coach-pit-metrics">
      {[['클래스 순위', value(view?.class_position, '위', 0)], ['예상 남은 거리', view?.remaining_laps == null ? '수집 중' : `${Number(view.remaining_laps).toFixed(1)}랩${view?.finish_may_extend ? ' · 한 랩 늘 수 있음' : view?.finish_may_shorten ? ' · 한 랩 줄 수 있음' : ''}`], ['다음 급유 · 여유분 포함', view?.fuel_next_stop_full ? `가득${view?.stops_needed > 1 ? ` · 급유 약 ${view.stops_needed}번` : ''}` : value(view?.fuel_next_stop_l ?? view?.fuel_to_add_l, 'L')], ['피트 복귀 예상', view?.rejoin_class_position_estimate == null ? '조건 확인 필요' : `약 ${view.rejoin_class_position_estimate}위`]].map(([label, content]) => <LiveMetric key={label} label={label} value={content} />)}
    </div>
    <Separator className="my-6" />
    <FieldSet><FieldLegend>보고와 연료 전략</FieldLegend>
      <FieldGroup className="coach-live-fields">
        <Field><FieldLabel htmlFor="wall-level">자동 보고 범위</FieldLabel><NativeSelect id="wall-level" aria-label="자동 보고 범위" value={preferences.level} onChange={e => onChange({ level: e.target.value })}><option value="important">중요한 것만</option><option value="all">일반 보고 포함</option><option value="off">질문에만 답변</option></NativeSelect></Field>
        <Field><FieldLabel htmlFor="wall-fuel-mode">연료 절약 목표</FieldLabel><NativeSelect id="wall-fuel-mode" aria-label="연료 절약 목표" value={preferences.fuel_save_mode || 'finish'} onChange={e => onChange({ fuel_save_mode: e.target.value })}><option value="finish">무급유 완주</option><option value="reserve">무급유 완주 + 안전 여유</option><option value="off">절약 피드백 끄기</option></NativeSelect></Field>
        <Field><FieldLabel htmlFor="wall-reserve">완주 여유 연료 · 랩</FieldLabel><Input id="wall-reserve" aria-label="완주 여유 연료 랩" type="number" min="0" max="3" step="0.1" value={preferences.reserve_laps} onChange={e => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n) && n >= 0 && n <= 3) onChange({ reserve_laps: n }); }} /></Field>
        <Field><FieldLabel htmlFor="wall-pit-loss">예상 피트 총손실 · 초</FieldLabel><Input id="wall-pit-loss" aria-label="예상 피트 총손실 초" type="number" min="1" max="600" step="1" placeholder={view?.pit_loss_auto_s != null ? `자동 ${Math.round(view.pit_loss_auto_s)}초` : '진입 + 작업 + 복귀'} value={preferences.pit_loss_s ?? ''} onChange={e => { const n = Number(e.target.value); if (e.target.value === '') onChange({ pit_loss_s: null }); else if (Number.isFinite(n) && n > 0 && n <= 600) onChange({ pit_loss_s: n }); }} /></Field>
        <Field><FieldLabel htmlFor="wall-pit-rule">피트 작업 방식 · 인듀런스</FieldLabel><NativeSelect id="wall-pit-rule" aria-label="피트 작업 방식" value={preferences.pit_rule || 'UNKNOWN'} onChange={e => onChange({ pit_rule: e.target.value })}><option value="UNKNOWN">모름 · 타이어 안내 안 함</option><option value="CONCURRENT">동시 교체 (급유와 타이어 같이)</option><option value="SEQUENTIAL">순차 교체 (급유 뒤 타이어)</option></NativeSelect></Field>
        <Field><FieldLabel htmlFor="wall-hold">제동·조향 앞에서 말 시작 보류</FieldLabel><NativeSelect id="wall-hold" aria-label="제동 조향 앞에서 말 시작 보류" value={preferences.hold_enabled === false ? 'off' : 'on'} onChange={e => onChange({ hold_enabled: e.target.value === 'on' })}><option value="on">켜기</option><option value="off">끄기 · 언제든 말함</option></NativeSelect></Field>
        <Field><FieldLabel htmlFor="wall-hold-lead">보류 시작 · 제동·조향 몇 초 전</FieldLabel><Input id="wall-hold-lead" aria-label="보류 시작 초" type="number" min="0" max="5" step="0.1" disabled={preferences.hold_enabled === false} value={preferences.hold_lead_s ?? 1} onChange={e => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n) && n >= 0 && n <= 5) onChange({ hold_lead_s: n }); }} /></Field>
      </FieldGroup>
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">켜면 제동·조향 중이거나 설정한 초 안에 제동·조향이 다가올 때 새 안내를 시작하지 않습니다. 이미 말하고 있는 안내는 끊지 않습니다. 좌우 차량·깃발 같은 급한 호출은 이 설정과 관계없이 바로 나옵니다. 다가오는 제동 지점은 깨끗한 랩 2개를 달린 뒤부터 알 수 있습니다.</p>
    </FieldSet>
    <div className="coach-fuel-grid">
      <LiveMetric label="현재 절약 목표 소비량" value={target} />
      <LiveMetric label="최근 깨끗한 완료 랩 소비량" value={value(view?.fuel_last_lap?.use_l, 'L', 2)} note={view?.fuel_last_lap?.target_l != null ? `해당 랩 시작 목표 ${value(view.fuel_last_lap.target_l, 'L', 2)}` : '완료 랩에서 계산합니다.'} />
      <LiveMetric label="무급유 완주 목표 · 여유분 없음" value={value(view?.fuel_target_l_per_lap, 'L/랩', 2)} note={view?.fuel_save_feasible === false ? '절약으로는 닿지 않는 거리입니다.' : view?.fuel_save_pct > 0 ? `필요한 절약 ${view.fuel_save_pct}%` : '현재 관측 소비량 기준'} />
      <LiveMetric label="완주까지 부족 · 여유분 제외" value={value(view?.fuel_finish_shortfall_l, 'L')} tone="warning" />
      <LiveMetric label="설정한 안전 여유분" value={value(view?.fuel_reserve_l, 'L')} />
      <LiveMetric label="경기 종료 예상" value={view?.finish_total_laps != null ? `내 기준 ${view.finish_total_laps}랩` : '시간제 레이스에서 계산'}
        note={view?.finish_total_laps != null ? `선두가 종료 시각과 ${value(view?.finish_leader_margin_s, '초', 0)} · 내가 선두 피니시와 ${value(view?.finish_own_margin_s, '초', 0)} 차이${view?.fuel_next_stop_longer_l != null ? ` · 한 랩 늘면 급유 ${Math.ceil(view.fuel_next_stop_longer_l)}L` : ''}` : '선두가 시간 종료 뒤 처음 결승선을 지나면 끝납니다.'} />
      <LiveMetric label="예상 피트 총손실" value={preferences.pit_loss_s != null ? `${preferences.pit_loss_s}초 · 직접 입력` : view?.pit_loss_auto_s != null ? `${Math.round(view.pit_loss_auto_s)}초 · 자동` : '자료 없음'}
        note={view?.pit_transit_loss_s != null ? `이 트랙의 피트 통과 손실 ${Math.round(view.pit_transit_loss_s)}초${view?.pit_stall_estimate_s != null ? ` + 정차 ${Math.round(view.pit_stall_estimate_s)}초` : ''}` : '이 트랙에서 피트한 기록이 생기면 자동으로 채워집니다.'} />
      <LiveMetric label="타이어 계획" value={({ keep: '그대로', change: '교체' })[view?.tyre_decision] || '안내 없음'}
        note={view?.tyre_wear_pct_per_lap != null ? `마모 랩당 ${Number(view.tyre_wear_pct_per_lap).toFixed(2)}%${view?.tyre_laps_on_set != null ? ` · 지금 세트 ${view.tyre_laps_on_set}랩${view?.tyre_stints_on_set ? ` (${view.tyre_stints_on_set}번째 스틴트)` : ''}` : ''}` : '마모 기록이 생기면 표시됩니다.'} />
      <LiveMetric label="보고 구간 학습" value={view?.speech_learned_laps >= 2 ? '학습됨' : `${view?.speech_learned_laps || 0} / 2랩`} note={view?.speech_window_s != null ? `발화 여유 약 ${value(view.speech_window_s, '초')}` : '깨끗한 랩에서 발화 구간을 확인합니다.'} />
    </div>
    <Separator className="my-6" />
    <h3 className="text-sm font-medium">엔지니어 목소리</h3>
    <div className="coach-role-grid">{Object.entries(ENGINEERS).map(([key, role]) => <Card key={key} className="bg-muted/40 shadow-none"><CardHeader className="p-4 pb-3"><CardTitle className="text-sm">{role.label}</CardTitle><CardDescription>{role.description}</CardDescription></CardHeader><CardContent className="p-4 pt-0"><p className="text-xs text-muted-foreground">{(engineers?.[key]?.voice || role.voice).replace('ko-KR-Chirp3-HD-', '')} · 남성 · 1.15배속</p><Button variant="outline" size="sm" onClick={() => onTestVoice(key)} disabled={busy} className="mt-3"><Volume2 data-icon="inline-start" />목소리 들어보기</Button></CardContent></Card>)}</div>
    <div className="coach-live-info">
      <p>자동 보고는 AI 토큰을 사용하지 않습니다. 고정 음성은 최초 합성 후 재사용하고, 정확한 수치는 화면과 기록에 남깁니다. 새 질문 답변의 음성 합성에는 비용이 발생할 수 있습니다.</p>
      <p>일반 보고는 제동·조향 부담이 적을 때 전달합니다. 질문·녹음·옆 차량 접근 중에는 기다리고, 깃발은 스포터가 안내합니다. 페이지를 다시 열면 자동 보고를 켜세요.</p>
      <p>시간제 경기는 종료 뒤 마지막 랩까지 계산합니다. 연료 목표와 복귀 순위는 추정이며, 상대 피트 계획·추월·교통 변화는 반영하지 않습니다.</p>
    </div>
  </section></Card>;
}
