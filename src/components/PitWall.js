import { Card, CardHeader, CardTitle, CardDescription, CardContent } from './ui/card';
import { Field, FieldGroup, FieldSet, FieldLegend, FieldLabel } from './ui/field';
import { Separator } from './ui/separator';
import { LiveHeader } from './LivePrimitives';
import { Play, RadioTower, Square, Volume2 } from 'lucide-react';
import { Button } from './ui/button';
import { NativeSelect } from './ui/native-select';
import { Input } from './ui/input';
import { Switch } from './ui/switch';
import React from 'react';
import { STANDBY_MESSAGE } from '../audioHost';
import { readGameItem } from '../lib/gameStorage';
import { useGame } from '../lib/gameContext';
import { readAlerts } from '../lib/voiceAlerts';
import VoiceAlertSettings from './VoiceAlertSettings';

export const ENGINEERS = {
  coach: { label: '주행 코치', voice: 'Algenib', style: 'border-primary/30 bg-primary/10 text-primary', description: '코너 느낌과 다음 주행 행동' },
  race: { label: '레이스 엔지니어', voice: 'Charon', style: 'border-accent/30 bg-accent/10 text-foreground', description: '같은 클래스 순위·앞뒤 페이스' },
  strategy: { label: '전략 엔지니어', voice: 'Orus', style: 'border-warning/30 bg-warning/10 text-warning', description: '완주 연료·급유량·피트 복귀 예측' },
};

export const PIT_WALL_KEY = 'iracing.voice.pitWall.v1';
export function readPitPreferences(storageKey = PIT_WALL_KEY, game = 'iracing') {
  try {
    const saved = JSON.parse(readGameItem(storageKey, game));
    return { alerts: readAlerts('pitwall', game, saved || {}), level: saved?.level === 'all' ? 'all' : 'important',
      fuel_save_mode: ['off', 'finish', 'reserve'].includes(saved?.fuel_save_mode) ? saved.fuel_save_mode : 'finish',
      reserve_laps: typeof saved?.reserve_laps === 'number' && saved.reserve_laps >= 0 && saved.reserve_laps <= 3 ? saved.reserve_laps : .5,
      pit_loss_s: typeof saved?.pit_loss_s === 'number' && saved.pit_loss_s > 0 && saved.pit_loss_s <= 600 ? saved.pit_loss_s : null,
      pit_rule: ['CONCURRENT', 'SEQUENTIAL'].includes(saved?.pit_rule) ? saved.pit_rule : 'UNKNOWN',
      hold_enabled: saved?.hold_enabled !== false,
      hold_lead_s: typeof saved?.hold_lead_s === 'number' && saved.hold_lead_s >= 0 && saved.hold_lead_s <= 5 ? saved.hold_lead_s : 1 };
  } catch { return { alerts: readAlerts('pitwall', game), level: 'important', reserve_laps: .5, pit_loss_s: null, fuel_save_mode: 'finish', pit_rule: 'UNKNOWN', hold_enabled: true, hold_lead_s: 1 }; }
}
export function PitWallSettings({ preferences, onChange, view, provider = 'iRacing', capabilities = {} }) {
  const lmu = provider === 'LMU';
  const unavailable = key => capabilities[key] === false;
  return (
    <FieldSet><FieldLegend>보고와 연료 전략</FieldLegend>
      <FieldGroup className="coach-live-fields">
        <Field><FieldLabel htmlFor="wall-fuel-mode">연료 절약 목표</FieldLabel><NativeSelect id="wall-fuel-mode" disabled={unavailable('fuel_strategy') || unavailable('finish_prediction')} aria-label="연료 절약 목표" value={preferences.fuel_save_mode || 'finish'} onChange={e => onChange({ fuel_save_mode: e.target.value })}><option value="finish">무급유 완주</option><option value="reserve">무급유 완주 + 안전 여유</option><option value="off">절약 피드백 끄기</option></NativeSelect></Field>
        <Field><FieldLabel htmlFor="wall-reserve">완주 여유 연료 · 랩</FieldLabel><Input id="wall-reserve" disabled={unavailable('fuel_strategy')} aria-label="완주 여유 연료 랩" type="number" min="0" max="3" step="0.1" value={preferences.reserve_laps} onChange={e => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n) && n >= 0 && n <= 3) onChange({ reserve_laps: n }); }} /></Field>
        <Field><FieldLabel htmlFor="wall-pit-loss">예상 피트 총손실 · 초</FieldLabel><Input id="wall-pit-loss" disabled={unavailable('pit_strategy')} aria-label="예상 피트 총손실 초" type="number" min="1" max="600" step="1" placeholder={view?.pit_loss_auto_s != null ? `자동 ${Math.round(view.pit_loss_auto_s)}초` : '진입 + 작업 + 복귀'} value={preferences.pit_loss_s ?? ''} onChange={e => { const n = Number(e.target.value); if (e.target.value === '') onChange({ pit_loss_s: null }); else if (Number.isFinite(n) && n > 0 && n <= 600) onChange({ pit_loss_s: n }); }} /></Field>
        <Field><FieldLabel htmlFor="wall-pit-rule">피트 작업 방식 · 인듀런스</FieldLabel><NativeSelect id="wall-pit-rule" disabled={unavailable('pit_strategy')} aria-label="피트 작업 방식" value={preferences.pit_rule || 'UNKNOWN'} onChange={e => onChange({ pit_rule: e.target.value })}><option value="UNKNOWN">{view?.pit_rule_source === 'RECORD' ? `자동 · 지난 경기 기록: ${view.pit_rule === 'SEQUENTIAL' ? '순차' : '동시'} 교체` : '자동 · 지난 경기 기록 따르기'}</option><option value="CONCURRENT">동시 교체 (급유와 타이어 같이)</option><option value="SEQUENTIAL">순차 교체 (급유 뒤 타이어)</option></NativeSelect>
          {!lmu && (preferences.pit_rule || 'UNKNOWN') === 'UNKNOWN' && <p className="mt-1 text-xs text-muted-foreground" data-testid="pit-rule-note">{view?.pit_rule_source === 'RECORD' ? '이 시리즈의 지난 경기 피트 기록대로 타이어 계획을 세웁니다.' : '이 시리즈의 공식 경기 기록이 있으면 그대로 따르고, 없으면 타이어 안내를 하지 않습니다.'}</p>}</Field>
        <Field><FieldLabel htmlFor="wall-hold">제동·조향 앞에서 말 시작 보류</FieldLabel><NativeSelect id="wall-hold" disabled={unavailable('speech_policy')} aria-label="제동 조향 앞에서 말 시작 보류" value={preferences.hold_enabled === false ? 'off' : 'on'} onChange={e => onChange({ hold_enabled: e.target.value === 'on' })}><option value="on">켜기</option><option value="off">끄기 · 언제든 말함</option></NativeSelect></Field>
        <Field><FieldLabel htmlFor="wall-hold-lead">보류 시작 · 제동·조향 몇 초 전</FieldLabel><Input id="wall-hold-lead" aria-label="보류 시작 초" type="number" min="0" max="5" step="0.1" disabled={unavailable('speech_policy') || unavailable('speech_learning') || preferences.hold_enabled === false} value={preferences.hold_lead_s ?? 1} onChange={e => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n) && n >= 0 && n <= 5) onChange({ hold_lead_s: n }); }} /></Field>
      </FieldGroup>
      {lmu && <p className="mt-3 text-xs text-muted-foreground">회색 설정은 아직 연결되지 않은 기능입니다. LMU는 현재 조작 부담으로 발화 시점을 판단하며 제동 지점 예측은 하지 않습니다.</p>}
      {!lmu && <p className="mt-3 text-xs leading-relaxed text-muted-foreground">켜면 제동·조향 중이거나 설정한 초 안에 제동·조향이 다가올 때 새 안내를 시작하지 않습니다. 이미 말하고 있는 안내는 끊지 않습니다. 좌우 차량·깃발 같은 급한 호출은 이 설정과 관계없이 바로 나옵니다. 다가오는 제동 지점은 깨끗한 랩 2개를 달린 뒤부터 알 수 있습니다.</p>}
    </FieldSet>
  );
}

export default function PitWall({ enabled, onToggle, preferences, onChange, view, engineers, onTestVoice, busy, speaking, standby = false, provider = 'iRacing', capabilities = {}, statusText }) {
  const lmu = provider === 'LMU';
  const { selectedGame } = useGame();
  const roleDescription = key => !lmu ? ENGINEERS[key].description : ({
    coach: '코너 코칭은 기준 구간 연결 대기',
    race: '랩타임·클래스 순위·경기 시간',
    strategy: '현재 연료·관측 소비량·연료 경고',
  })[key];
  return <Card asChild><section aria-label="레이스 피트월" className="coach-panel">
    <LiveHeader title="레이스 피트월" topic="pitWall" icon={RadioTower} description="주행 코치 · 레이스 엔지니어 · 전략 엔지니어"
      action={<Button variant={enabled ? 'outline' : 'default'} aria-pressed={enabled} onClick={onToggle}>{enabled ? <Square data-icon="inline-start" /> : <Play data-icon="inline-start" />}{enabled ? '자동 보고 중지' : '자동 보고 시작'}</Button>} />
    <p role="status" className="text-sm text-muted-foreground">{statusText || (enabled && preferences.level !== 'off' ? standby ? STANDBY_MESSAGE : speaking ? '엔지니어 발화 중 · 다음 보고는 순서대로 전달' : view?.active ? '자동 보고 활성 · 상황 변화를 확인 중' : '자동 보고 활성 · 진행 중인 Race 세션 대기' : lmu ? '자동 보고 꺼짐 · 질문 기능은 아직 지원하지 않습니다.' : '자동 보고 꺼짐 · 음성 또는 텍스트 질문은 사용 가능')}</p>
    <details className="voice-basic-settings"><summary>연료·발화 설정</summary>
    <PitWallSettings preferences={preferences} onChange={onChange} view={view} provider={provider} capabilities={capabilities} />
    </details>
    <Separator className="my-6" />
    <h3 className="text-sm font-medium">엔지니어 목소리</h3>
    <div className="coach-role-grid">{Object.entries(ENGINEERS).map(([key, role]) => <Card key={key} className="bg-muted/40 shadow-none"><CardHeader className="p-4 pb-3"><CardTitle className="text-sm">{role.label}</CardTitle><CardDescription>{roleDescription(key)}</CardDescription></CardHeader><CardContent className="p-4 pt-0">
      {lmu && <Field orientation="horizontal"><FieldLabel htmlFor={`wall-role-${key}`}>{role.label} 보고</FieldLabel><Switch id={`wall-role-${key}`} checked={preferences.engineers?.includes(key) === true} onCheckedChange={enabled => onChange({ engineers: enabled ? [...(preferences.engineers || []), key] : preferences.engineers.filter(item => item !== key) })} /></Field>}
      <p className="text-xs text-muted-foreground">{(engineers?.[key]?.voice || role.voice).replace('ko-KR-Chirp3-HD-', '')} · 남성 · 공통 재생 속도</p><Button variant="outline" size="sm" onClick={() => onTestVoice(key)} disabled={busy || capabilities.voice_test?.[key] === false || (lmu && !preferences.engineers?.includes(key))} className="mt-3"><Volume2 data-icon="inline-start" />목소리 들어보기</Button></CardContent></Card>)}</div>
    <div className="coach-live-info">
      <p>{lmu ? 'LMU는 준비된 고정 음성만 재사용하며 새로운 합성 요청을 보내지 않습니다. 질문·마이크·AI 답변은 아직 지원하지 않습니다.' : '자동 보고는 AI 토큰을 사용하지 않습니다. 고정 음성은 최초 합성 후 재사용하고, 정확한 수치는 화면과 기록에 남깁니다. 새 질문 답변의 음성 합성에는 비용이 발생할 수 있습니다.'}</p>
      <p>{lmu ? '일반 보고는 조작 부담이 적을 때 전달합니다. 좌우 스포터는 피트월보다 우선합니다. 피트월 설정은 LMU용으로 따로 저장됩니다.' : '일반 보고는 제동·조향 부담이 적을 때 전달합니다. 질문·녹음·옆 차량 접근 중에는 기다리고, 깃발은 스포터가 안내합니다. 페이지를 다시 열면 자동 보고를 켜세요.'}</p>
      <p>{lmu ? '연료·완주 거리·피트 전략은 현재 측정 가능한 항목만 표시합니다. 시간제 경기의 선두 종료 랩이나 상대 피트 계획을 알 수 없으면 추측하지 않습니다.' : '시간제 경기는 종료 뒤 마지막 랩까지 계산합니다. 연료 목표와 복귀 순위는 추정이며, 상대 피트 계획·추월·교통 변화는 반영하지 않습니다.'}</p>
    </div>
    <VoiceAlertSettings scope="pitwall" game={selectedGame} alerts={preferences.alerts} onChange={alerts => onChange({ alerts, level: preferences.level === 'off' ? 'all' : preferences.level })} />
  </section></Card>;
}
