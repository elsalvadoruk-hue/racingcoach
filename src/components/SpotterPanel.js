import { Card } from './ui/card';
import { Field, FieldGroup, FieldSet, FieldLegend, FieldLabel } from './ui/field';
import { LiveHeader } from './LivePrimitives';
import { ArrowLeft, ArrowRight, CarFront, Headphones } from 'lucide-react';
import { Button } from './ui/button';
import { NativeSelect } from './ui/native-select';
import React from 'react';
import VoicePackProgress from './VoicePackProgress';
import { SPOTTER_VOICES } from '../useSpotter';
import { useGame } from '../lib/gameContext';
import { spotterAlertPatch } from '../lib/voiceAlerts';
import VoiceAlertSettings from './VoiceAlertSettings';
import CustomVoicePacks from './CustomVoicePacks';
import RoleVoiceSelect from './RoleVoiceSelect';

export function SpotterSettings({ settings, onChange: changeSettings, disabled = false, capabilities = {}, idPrefix = 'spotter', library, voiceDisabled = false }) {
  const supported = key => capabilities[key] !== false;
  const id = key => `${idPrefix}-${key}`;
  return <FieldSet disabled={disabled} className="spotter-settings mt-6 border-t pt-5"><FieldLegend>스포터 안내 설정</FieldLegend>
    <FieldGroup className="grid gap-4 sm:grid-cols-2">
      {supported('voice') && <RoleVoiceSelect role="spotter" library={library} settings={settings} onChange={changeSettings} disabled={disabled || voiceDisabled} defaults={SPOTTER_VOICES.map(([name, label]) => [name, `${name} · ${label}`])} />}
      {supported('formation') && <Field><FieldLabel htmlFor={id('formation')}>포메이션 랩 안내 개수</FieldLabel><NativeSelect id={id('formation')} aria-label="포메이션 랩 안내 개수" value={settings.formation_calls || 5} onChange={e => changeSettings({ formation_calls: Number(e.target.value) })}>{[1, 2, 3, 4, 5, 6, 7, 8].map(n => <option key={n} value={n}>{`${n}개`}</option>)}</NativeSelect></Field>}
      <Field data-disabled={disabled || (capabilities.repeat_toggle && settings.repeat_enabled === false) || undefined}><FieldLabel htmlFor={id('repeat')}>옆 차량 반복 간격</FieldLabel><NativeSelect id={id('repeat')} aria-label="옆 차량 반복 간격" disabled={disabled || (capabilities.repeat_toggle && settings.repeat_enabled === false)} value={settings.repeat_seconds} onChange={e => changeSettings({ repeat_seconds: Number(e.target.value) })}>{(capabilities.repeat_toggle ? [2, 3, 5, 8, 10] : [2, 3, 5]).map(n => <option key={n} value={n}>{n}초</option>)}</NativeSelect></Field>
      <Field><FieldLabel htmlFor={id('clear')}>클리어 확인 시간</FieldLabel><NativeSelect id={id('clear')} aria-label="클리어 확인 시간" disabled={disabled} value={settings.clear_delay} onChange={e => changeSettings({ clear_delay: Number(e.target.value) })}>{[.15, .3, .5].map(n => <option key={n} value={n}>{n * 1000}ms</option>)}</NativeSelect></Field>
    </FieldGroup>
    {capabilities.repeat_toggle && <p className="text-xs leading-relaxed text-muted-foreground">반복 안내를 끄면 옆에 계속 있는 차량의 반복 음성만 멈춥니다. 새 좌우 겹침과 클리어 안내는 유지합니다. 클리어는 겹침이 없는 상태를 설정한 시간 동안 확인한 뒤 안내합니다.</p>}
    {supported('formation') && <p className="text-xs leading-relaxed text-muted-foreground">포메이션 랩 안내는 레이스의 포메이션 랩을 도는 동안 경기 길이, 연료, 출발 요령 등을 정한 개수만큼 고르게 나눠 말하고, 마지막 인사는 랩의 80% 지점쯤에 합니다. 포메이션 랩이 없는 경기에서는 나오지 않습니다.</p>}
    {supported('pit_box') && <p className="text-xs leading-relaxed text-muted-foreground">피트박스 음성은 기본으로 꺼져 있습니다. 연습에서 추정 위치를 확인한 뒤 켜세요. 접근 차량의 초 단위 간격은 트랙 진행률로 환산한 추정이며 순위 간격과 다릅니다.</p>}
  </FieldSet>;
}

export default function SpotterPanel({ spotter, muted, volume, audioError, apiBase = '', voiceLocked = false }) {
  const { enabled, preparing, view, voice, error, toggle, test, settings, settingsBusy, changeSettings } = spotter;
  const { selectedGame } = useGame();
  const side = view?.raw_state;
  const traffic = view?.radio?.traffic;
  const box = view?.radio?.pit_box;
  const hazard = view?.radio?.hazards?.[0];
  const trafficLabels = { same_class: '같은 클래스', other_class: '다른 클래스', faster_class: '최근 랩 기준 빠른 클래스',
    slower_class: '최근 랩 기준 느린 클래스', lapping_car: '한 랩 이상 앞선 차량', lapped_car: '한 랩 이상 뒤처진 차량' };
  return <Card asChild><section aria-label="한국어 스포터" className="coach-panel">
    <LiveHeader title="한국어 스포터" topic="spotter" icon={Headphones} description={`좌우 차량과 주요 경기 신호 · ${voice}`}
      action={<Button variant={enabled || preparing ? 'outline' : 'default'} aria-pressed={enabled} onClick={toggle}>{preparing ? '음성 준비 중 · 취소' : enabled ? '스포터 끄기' : '스포터 켜기'}</Button>} />
    <div className="mt-4 grid grid-cols-3 items-stretch gap-2 text-center">
      <div className={`rounded-xl border p-3 ${[2, 4, 5].includes(side) ? 'border-warning bg-warning/10 text-warning' : 'border-border text-muted-foreground'}`}><ArrowLeft className="mx-auto mb-2" size={18} aria-hidden="true" /><p className="font-medium">왼쪽</p><p className="mt-1 text-sm">{side === 5 ? '두 대' : [2, 4].includes(side) ? '차량 있음' : '—'}</p></div>
      <div className="rounded-xl border border-success/30 bg-success/10 p-3"><CarFront className="mx-auto mb-2 text-primary" size={20} aria-hidden="true" /><p className="font-medium">내 차량</p><p className="mt-1 text-xs text-foreground">{view?.active ? '실시간 연결' : '연결 대기'}</p></div>
      <div className={`rounded-xl border p-3 ${[3, 4, 6].includes(side) ? 'border-warning bg-warning/10 text-warning' : 'border-border text-muted-foreground'}`}><ArrowRight className="mx-auto mb-2" size={18} aria-hidden="true" /><p className="font-medium">오른쪽</p><p className="mt-1 text-sm">{side === 6 ? '두 대' : [3, 4].includes(side) ? '차량 있음' : '—'}</p></div>
    </div>
    <p role="status" className="mt-3 text-sm text-foreground">{preparing ? '짧은 안내 음성을 준비하고 있습니다. 처음 한 번은 시간이 걸립니다.' : !enabled ? '스포터 꺼짐' : !view?.active ? view?.radio?.connected ? '기본 경기 안내 연결됨 · 좌우 겹침 안내는 대기 중' : '실제 차량 연결 대기 · 리플레이에서는 안내하지 않습니다.' : view.state_label}</p>
    {view?.last_text && <p className="mt-2 text-sm text-success">마지막 안내: {view.last_text}</p>}
    {view?.last_spoken_text && view.last_spoken_text !== view.last_text && <p className="mt-1 text-xs text-muted-foreground">음성 문구: {view.last_spoken_text} · 정확한 수치는 위 안내 참조</p>}
    {view?.last_engineer && <p className="mt-1 text-xs text-muted-foreground">담당: {({ spotter: '스포터', race: '레이스 엔지니어', strategy: '전략 엔지니어', coach: '주행 코치' })[view.last_engineer]}</p>}
    {view?.delivery_status && <p className="mt-1 text-xs text-muted-foreground">좌우 음성: {({ pending: '재생 대기', started: '재생 시작', completed: '재생 완료', muted: '음소거', interrupted: '중단 · 현재 상태 확인 후 재시도', failed: '실패 · 현재 상태 확인 후 재시도', cancelled: '취소' })[view.delivery_status]}</p>}
    {enabled && traffic?.available && <div className="mt-3 grid gap-2 sm:grid-cols-2">{['ahead', 'behind'].map((direction) => {
      const car = traffic[direction];
      return <p key={direction} className="min-w-0 break-words rounded-lg border border-border p-3 text-xs text-foreground">트랙 위 {direction === 'ahead' ? '앞차' : '뒤차'}: {car ? `${car.name || '차량'} · ${car.class_name ? car.class_name + ' · ' : ''}${trafficLabels[car.kind]} · 거리 환산 약 ${car.gap_estimate_s}초${car.approaching ? ' · 접근 중' : ''}` : '근접 차량 없음'}</p>;
    })}</div>}
    {enabled && hazard && <p role="status" className="mt-3 rounded-lg border border-warning bg-warning/10 p-3 text-sm text-warning">{hazard.kind === 'hazard_off_ahead' ? '전방 트랙 밖 차량' : '전방 매우 느린 차량'} · {hazard.name || '차량'} · 거리 환산 약 {hazard.gap_estimate_s}초</p>}
    {enabled && box && (box.ready || box.reason !== '피트 진입 대기') && <p className="mt-3 rounded-lg border border-success/30 p-3 text-sm text-success">{box.ready ? `피트박스까지 약 ${box.distance_estimate_m}m (추정)` : box.reason}{!box.enabled && ' · 음성 카운트다운 꺼짐'}</p>}
    {spotter.library?.view?.packs?.some(p => p.updates?.newCount > 0) && <p role="status" className="mt-3 text-sm text-warning">설치한 음성팩에 앱 업데이트 후 추가된 대본이 있습니다. 음성팩 설치·관리에서 추가 제작 목록을 저장하세요.</p>}
    {settings && <details className="voice-basic-settings"><summary>목소리·반복 설정{settingsBusy ? ' · 목소리 적용 중' : ''}</summary>
      <VoicePackProgress operation={spotter.library?.operation?.kind === 'selection' ? spotter.library.operation : settingsBusy ? {kind:'selection',state:'RUNNING',stage:'applying',done:0} : null} />
      <SpotterSettings library={spotter.library} settings={settings} onChange={changeSettings} disabled={preparing || settingsBusy} voiceDisabled={enabled || voiceLocked} /></details>}
    {settings && <CustomVoicePacks library={spotter.library} settings={settings} disabled={enabled || preparing || settingsBusy || voiceLocked} />}
    {enabled && <Button variant="outline" type="button" onClick={test} disabled={view?.occupied} className="mt-3  border  px-3 py-2 text-sm  disabled:opacity-40">스포터 목소리 듣기</Button>}
    {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
    {enabled && audioError && <p role="alert" className="mt-3 text-sm text-warning">{audioError}</p>}
    {enabled && (muted || volume === 0) && <p className="mt-3 text-sm text-warning">현재 음소거 상태입니다. 아래 오디오 설정에서 볼륨을 올리고 음소거를 해제하세요.</p>}
    {enabled && <details className="mt-3 rounded-xl border border-border p-3"><summary className="text-sm text-foreground">기본 안내 기능과 수집 상태</summary><div className="mt-3 grid gap-2 sm:grid-cols-2">{Object.entries({ flags: '깃발·출발 신호·페널티 표시', laps: '랩타임·세션 최고 기록', remaining: '남은 시간·랩', position: '클래스 순위', pit_speed: '피트 제한속도', pit_service: '피트 정차·작업', pit_box: '피트박스 위치', traffic: '트랙 위 근접 차량', engine: '차량 경고', briefing: '경기 전 브리핑' }).map(([key, label]) => <p key={key} className={`text-xs ${view?.radio?.capabilities?.[key] ? 'text-success' : 'text-muted-foreground'}`}>{label} · {view?.radio?.capabilities?.[key] ? '수집됨' : '데이터 대기'}</p>)}</div><p className="mt-3 text-xs leading-relaxed text-muted-foreground">패널티 종류·이행 시간은 확정하지 않습니다. 전방 위험은 약 10초 이내에서 트랙을 벗어났거나 매우 느린 차량이 측정될 때만 알리며 사고 여부나 원인은 판단하지 않습니다. 피트박스 거리는 추정치입니다. 게임의 공식 지시를 함께 확인하세요.</p></details>}
    {!!view?.radio?.history?.length && <details className="mt-3 rounded-xl border border-border p-3"><summary className="text-sm text-foreground">최근 무전 기록</summary><div className="mt-3 flex flex-col gap-2">{view.radio.history.slice(0, 8).map((item) => <p key={item.id} className="text-xs leading-relaxed text-foreground"><span className="text-success">{({ spotter: '스포터', race: '레이스', strategy: '전략', coach: '코치' })[item.engineer]}</span> · {item.text}</p>)}</div></details>}
    <p className="mt-3 text-xs leading-relaxed text-success">켜면 네 명의 자동 안내를 함께 시작합니다. 플래그는 스포터가 저장된 음성으로 전담합니다. 블루 플래그·화이트 플래그처럼 안내하며 AI 답변 토큰을 사용하지 않습니다. 엔지니어 자동 보고는 아래에서 별도로 중지할 수 있습니다.</p>
    <p className="mt-3 text-xs leading-relaxed text-muted-foreground">마이크와 자동 보고를 켜지 않아도 작동합니다. 아래 출력장치와 볼륨을 함께 사용합니다. 앱 페이지를 열어두세요. iRacing 또는 Crew Chief 스포터와 중복되지 않게 한 곳만 켜세요. 페이지를 다시 열면 스포터 켜기를 눌러주세요.</p>
    {settings && <VoiceAlertSettings scope="spotter" game={selectedGame} alerts={settings.alerts} disabled={preparing || settingsBusy} onChange={alerts => changeSettings(spotterAlertPatch(settings, alerts))} />}
  </section></Card>;
}
