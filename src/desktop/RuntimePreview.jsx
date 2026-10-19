import React, { useState } from 'react';
import { ChevronDown, Headphones, Layers3, Radio, RefreshCw, Volume2 } from 'lucide-react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Field, FieldGroup, FieldLabel, FieldDescription, FieldSet, FieldLegend } from '../components/ui/field';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Switch } from '../components/ui/switch';
import { Tooltip, TooltipTrigger, TooltipContent } from '../components/ui/tooltip';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { EFFECT_LEVELS, POP_MODES } from '../radioEffect';
import VoiceVolumeSlider from '../components/VoiceVolumeSlider';
export { DesktopRuntimeProvider, useLabControls } from './DesktopRuntime';

function DeviceOptions({ devices, kind, selected }) {
  const available = devices.filter(d => d.kind === kind);
  return <><NativeSelectOption value="default">시스템 기본 장치</NativeSelectOption>{selected !== 'default' && !available.some(d => d.deviceId === selected) && <NativeSelectOption value={selected}>저장한 장치 · 현재 확인 필요</NativeSelectOption>}{available.map((device, i) => <NativeSelectOption key={device.deviceId} value={device.deviceId}>{device.label || `${kind === 'audioinput' ? '입력' : '출력'}장치 ${i + 1}`}</NativeSelectOption>)}</>;
}

export function RuntimeControls({ runtime }) {
  const { controls, change, host } = runtime;
  const audio = host.audioOutput;
  const [deviceDialog, setDeviceDialog] = useState(false);
  const selectedDevice = audio.devices.find(d => d.deviceId === controls.output);
  const outputLabel = controls.output === 'default' ? '시스템 기본 장치' : selectedDevice?.label || '저장한 장치 · 확인 필요';
  return <div className="runtime-controls" aria-label="빠른 조작">
    <Tooltip><TooltipTrigger asChild><Badge variant="outline" tabIndex={0}>{runtime.host?.status?.connected ? '주행 연결됨' : '차량 연결 대기'}</Badge></TooltipTrigger><TooltipContent>실제 앱의 오버레이 자동 표시·스포터·피트월을 제어합니다.</TooltipContent></Tooltip>
    <FieldGroup className="runtime-switches">
      {[[Layers3, 'overlay', '오버레이'], [Headphones, 'spotter', '스포터'], [Radio, 'pitwall', '피트월']].map(([Icon, key, label]) => <Field key={key} orientation="horizontal" className="runtime-switch">
        <FieldLabel htmlFor={`quick-${key}`}><Icon aria-hidden="true" />{label}<span>{controls[key] ? '켜짐' : '꺼짐'}</span></FieldLabel>
        <Switch id={`quick-${key}`} aria-label={label} checked={controls[key]} disabled={key === 'overlay' ? runtime.overlayBusy || !runtime.overlay?.supported : key === 'spotter' ? runtime.host?.spotter.preparing : false} onCheckedChange={value => change(key, value)} />
      </Field>)}
    </FieldGroup>
    <Field orientation="horizontal" className="runtime-input"><FieldLabel htmlFor="output-device-trigger"><Volume2 aria-hidden="true" /><span>출력장치</span></FieldLabel><Button id="output-device-trigger" variant="outline" aria-label="출력장치 선택" aria-haspopup="dialog" aria-expanded={deviceDialog} title={outputLabel} onClick={() => { setDeviceDialog(true); audio.refresh({ ask: true }); }}><span className="truncate">{outputLabel}</span><ChevronDown data-icon="inline-end" /></Button></Field>
    <Dialog open={deviceDialog} onOpenChange={setDeviceDialog}><DialogContent className="max-w-lg"><DialogHeader><DialogTitle>오디오 출력장치</DialogTitle><DialogDescription>스포터·엔지니어·질문 답변을 들을 헤드셋이나 스피커를 선택하세요. 목록이 안 보이면 마이크 접근을 허용해 주세요. 확인용 마이크는 바로 종료하며 녹음하지 않습니다.</DialogDescription></DialogHeader>
      <FieldGroup><Field data-disabled={!audio.supported}><FieldLabel htmlFor="quick-output">출력장치 선택</FieldLabel><NativeSelect id="quick-output" value={controls.output} disabled={!audio.supported} onChange={event => change('output', event.target.value)}><DeviceOptions devices={audio.devices} kind="audiooutput" selected={controls.output} /></NativeSelect></Field><FieldDescription role="status">{!audio.supported ? '이 브라우저는 출력장치 선택을 지원하지 않아 시스템 기본 장치로 재생합니다. 최신 Chrome 또는 Edge에서 선택할 수 있습니다.' : audio.message || (audio.refreshing ? '장치 목록과 마이크 접근 권한을 확인하고 있습니다.' : '선택은 자동 저장됩니다. 장치를 바꾸면 재생 중인 음성이 중단됩니다.')}</FieldDescription></FieldGroup>
      <div className="flex flex-wrap justify-between gap-3"><Button variant="outline" disabled={!audio.supported || audio.refreshing} onClick={() => audio.refresh({ ask: true })}><RefreshCw data-icon="inline-start" />{audio.refreshing ? '목록 확인 중' : '목록 갱신'}</Button><Button variant="outline" disabled={host.phase !== 'idle'} onClick={host.testOutput}>테스트 소리</Button><Button onClick={() => setDeviceDialog(false)}>선택 완료</Button></div>
    </DialogContent></Dialog>
  </div>;
}

export { VoiceWorkspace as VoicePreview } from './VoiceWorkspace';

export function AudioPreferences({ runtime }) {
  const { controls, change, devices, refreshDevices, message, deviceBusy } = runtime;
  const audio = runtime.host.audioOutput;
  const effectsOff = controls.effect.level === 'off';
  return <Card className="audio-settings-card"><CardHeader><CardTitle>음성·장치 세부 설정</CardTitle><CardDescription>스포터·엔지니어·질문 답변에 함께 적용되며 이 브라우저에 자동 저장됩니다.</CardDescription></CardHeader><CardContent><FieldGroup className="audio-preferences">
    <FieldSet className="audio-section"><FieldLegend variant="label">출력·재생</FieldLegend><FieldGroup>
      <Field orientation="horizontal" className="audio-setting-row" data-disabled={!audio.supported}><FieldLabel htmlFor="settings-output">출력장치</FieldLabel><NativeSelect id="settings-output" value={controls.output} disabled={!audio.supported} onChange={event => change('output', event.target.value)}><DeviceOptions devices={audio.devices} kind="audiooutput" selected={controls.output} /></NativeSelect></Field>
      <Field orientation="horizontal" className="audio-setting-row"><FieldLabel htmlFor="settings-volume">음성 볼륨</FieldLabel><div className="audio-control"><VoiceVolumeSlider id="settings-volume" aria-describedby="settings-volume-help" value={controls.volume} onChange={value => change('volume', value)} /><output htmlFor="settings-volume">{controls.volume}%</output></div></Field>
      <FieldDescription id="settings-volume-help">0~130%. 빨간 구간은 100% 초과 증폭입니다. 소리가 찌그러지면 볼륨을 낮추세요. 게임에 묻히면 Windows 볼륨 믹서에서 게임 소리만 낮춰 보세요.</FieldDescription>
      <Field orientation="horizontal" className="audio-setting-row"><FieldLabel htmlFor="settings-muted">음소거</FieldLabel><Switch id="settings-muted" aria-label="음소거" checked={controls.muted} onCheckedChange={value => change('muted', value)} /></Field>
      {[['spotterRate', '스포터 속도', 'spotter-rate'], ['pitwallRate', '피트월 속도', 'pitwall-rate'], ['rate', '여러 문장 속도', 'rate']].map(([key, label, id]) => <Field key={key} orientation="horizontal" className="audio-setting-row"><FieldLabel htmlFor={`settings-${id}`}>{label}</FieldLabel><div className="audio-control"><input id={`settings-${id}`} aria-describedby="settings-rate-help" type="range" min="1" max="1.4" step="0.05" value={controls[key] ?? (key === 'rate' ? 1.3 : 1.15)} onChange={event => change(key, Number(event.target.value))} /><output htmlFor={`settings-${id}`}>{(controls[key] ?? (key === 'rate' ? 1.3 : 1.15)).toFixed(2)}배</output></div></Field>)}
    </FieldGroup><FieldDescription id="settings-rate-help">각각 1.0~1.4배입니다. 기본은 스포터·피트월 1.15배, 여러 문장 1.30배입니다. 여러 문장 안내는 담당 속도와 곱하지 않고 ‘여러 문장 속도’만 적용합니다. 포메이션 브리핑은 1.15배 고정입니다. 다음 안내부터 적용하며 음높이를 유지합니다.</FieldDescription></FieldSet>
    {runtime.speech.available && <FieldSet className="audio-section"><FieldLegend variant="label">마이크 입력</FieldLegend><FieldGroup>
      <Field orientation="horizontal" className="audio-setting-row"><FieldLabel htmlFor="settings-input">마이크 입력장치</FieldLabel><NativeSelect id="settings-input" value={controls.microphone} onChange={event => change('microphone', event.target.value)}><DeviceOptions devices={devices} kind="audioinput" selected={controls.microphone} /></NativeSelect></Field>
      <Field><Button variant="outline" size="sm" disabled={deviceBusy} onClick={() => refreshDevices({ ask: true })}><RefreshCw data-icon="inline-start" />{deviceBusy ? '접근 권한 확인 중' : '마이크 접근 허용·목록 갱신'}</Button><FieldDescription role="status">{message || '장치 이름 확인 후 마이크를 바로 종료하며 녹음하지 않습니다.'}</FieldDescription></Field>
    </FieldGroup></FieldSet>}
    <FieldSet className="audio-section"><FieldLegend variant="label">무전 효과</FieldLegend><FieldGroup>
      <Field orientation="horizontal" className="audio-setting-row"><FieldLabel htmlFor="settings-effect">효과 강도</FieldLabel><NativeSelect id="settings-effect" aria-label="무전 효과" value={controls.effect.level} onChange={event => change('effect', { level: event.target.value })}>{EFFECT_LEVELS.map(([value, label]) => <NativeSelectOption key={value} value={value}>{label}</NativeSelectOption>)}</NativeSelect></Field>
      <Field orientation="horizontal" className="audio-setting-row" data-disabled={effectsOff}><FieldLabel htmlFor="settings-squelch">시작·끝 잡음</FieldLabel><Switch id="settings-squelch" aria-label="시작·끝 무전 잡음(칙)" checked={controls.effect.squelch} disabled={effectsOff} onCheckedChange={value => change('effect', { squelch: value })} /></Field>
      <Field orientation="horizontal" className="audio-setting-row" data-disabled={effectsOff}><FieldLabel htmlFor="settings-hiss">바탕 잡음</FieldLabel><Switch id="settings-hiss" aria-label="말하는 동안 바탕 잡음" checked={controls.effect.hiss} disabled={effectsOff} onCheckedChange={value => change('effect', { hiss: value })} /></Field>
      <Field orientation="horizontal" className="audio-setting-row" data-disabled={effectsOff}><FieldLabel htmlFor="settings-pops">팝노이즈</FieldLabel><NativeSelect id="settings-pops" value={controls.effect.pops} disabled={effectsOff} onChange={event => change('effect', { pops: event.target.value })}>{POP_MODES.map(([value, label]) => <NativeSelectOption key={value} value={value}>{label}</NativeSelectOption>)}</NativeSelect></Field>
      {[["voice", "음성 크기", 20, 150, 5], ["noise", "잡음 크기", 0, 400, 10]].map(([key, label, min, max, step]) => <Field key={key} orientation="horizontal" className="audio-setting-row" data-disabled={effectsOff}><FieldLabel htmlFor={`settings-${key}`}>{label}</FieldLabel><div className="audio-control"><input id={`settings-${key}`} type="range" min={min} max={max} step={step} disabled={effectsOff} value={controls.effect[key]} onChange={event => change('effect', { [key]: Number(event.target.value) })} /><output htmlFor={`settings-${key}`}>{controls.effect[key]}%</output></div></Field>)}
    </FieldGroup></FieldSet>
  </FieldGroup></CardContent><CardFooter className="audio-footer"><div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={!audio.supported || audio.refreshing} onClick={() => audio.refresh({ ask: true })}><RefreshCw data-icon="inline-start" />출력장치 목록 새로고침</Button><Button variant="outline" size="sm" disabled={runtime.host?.phase !== 'idle'} onClick={runtime.host?.testOutput}>테스트 소리</Button></div><p role="status">{audio.message || '장치 이름 확인에 마이크 권한을 사용합니다. 확인 후 바로 종료하며 녹음하지 않습니다.'}</p></CardFooter></Card>;
}
