import React from 'react';
import { Headphones, Mic, Settings2, Square, Volume2, VolumeX } from 'lucide-react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Field, FieldGroup, FieldLabel, FieldDescription } from '../components/ui/field';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Switch } from '../components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import SpotterPanel from '../components/SpotterPanel';
import PitWall from '../components/PitWall';
import { AudioPreferences } from './RuntimePreview';

export function VoiceWorkspace({ runtime, onSettings }) {
  const { controls, change, speech, host } = runtime;
  const active = ['recording', 'starting'].includes(host.phase), busy = host.phase !== 'idle';
  const statusText = host.phase === 'sending' ? '인식·저장·답변 중…' : active ? '듣고 있습니다 · 최대 45초'
    : controls.armed ? host.socketReady ? 'F8을 누른 채 말하고 놓으세요.' : 'F8 연결 중 · 화면 녹음 버튼 사용 가능' : '먼저 마이크를 준비하세요.';
  return <section className="voice-preview voice-settings-workspace" aria-label="음성 코치">
    <div className="utility-heading"><div><div className="eyebrow">VOICE COACH / RADIO SETTINGS</div><h1>음성 코치</h1><p>질문·오디오, 스포터, 피트월을 함께 관리하세요. 답변과 메모는 기록에서 확인합니다.</p></div><div className="flex items-center gap-3"><Badge variant="outline">{host.status?.connected ? '주행 연결됨' : '차량 연결 대기'}</Badge><Button variant="outline" size="sm" onClick={onSettings}><Settings2 data-icon="inline-start" />설치·연결 설정</Button></div></div>
    <div className="voice-console">
      <div className="voice-column" aria-label="질문과 오디오 설정" tabIndex={0}>
        {speech.available ? <Card className="voice-question"><CardHeader><CardTitle>주행 중 질문</CardTitle><CardDescription>{speech.verified ? 'Google 음성 인식 연결을 확인했습니다.' : 'Google 음성 API가 설정되어 있습니다. 실제 연결은 질문할 때 확인합니다.'}</CardDescription></CardHeader>
          <CardContent><div className="voice-f8"><div className="flex items-center gap-3"><Mic aria-hidden="true" /><kbd>F8</kbd><h2>누른 채 말하고 놓으세요</h2></div><Button aria-pressed={controls.armed} disabled={busy && host.phase !== 'recording'} onClick={() => change('armed', !controls.armed)}>{controls.armed ? '마이크 끄기' : host.phase === 'arming' ? '마이크 연결 중' : '마이크 준비'}</Button></div>
            <div className="flex flex-wrap gap-2"><Button variant={active ? 'destructive' : 'outline'} disabled={!controls.armed || ['sending', 'arming'].includes(host.phase)} onClick={active ? host.finish : host.start}>{active ? <Square data-icon="inline-start" /> : <Mic data-icon="inline-start" />}{active ? '녹음 완료 · 전송' : '녹음 시작'}</Button><Button variant="outline" onClick={host.stopSpeaking}><VolumeX data-icon="inline-start" />답변 중단</Button>{host.replay && <Button variant="outline" onClick={host.replay}><Volume2 data-icon="inline-start" />다시 듣기</Button>}</div>
            <p role="status" className="my-3 text-sm text-muted-foreground">{statusText}</p>
            <FieldGroup><Field><FieldLabel>답변 방식</FieldLabel><ToggleGroup type="single" value={controls.mode} disabled={busy} onValueChange={value => value && change('mode', value)} aria-label="음성 답변 방식"><ToggleGroupItem value="measured">측정값 답변</ToggleGroupItem><ToggleGroupItem value="ai">AI 원인 분석</ToggleGroupItem><ToggleGroupItem value="memo">메모만 기록</ToggleGroupItem></ToggleGroup><FieldDescription>{controls.mode === 'ai' ? '마이크로 질문할 때만 AI로 원인을 설명합니다.' : controls.mode === 'memo' ? '답변 없이 코너와 주행 상황에 메모를 남깁니다.' : '관측된 수치로 답합니다. 확인할 자료가 없으면 추측하지 않습니다.'}</FieldDescription></Field>
              <Field orientation="horizontal"><FieldLabel htmlFor="voice-speak">답변을 음성으로 듣기</FieldLabel><Switch id="voice-speak" checked={controls.speak} onCheckedChange={value => change('speak', value)} /></Field></FieldGroup>
          </CardContent><CardFooter><p>F8은 다른 페이지에서도 유지됩니다. 녹음 원본과 개인 질문·답변 음성은 디스크에 저장하지 않습니다. 음성 인식·합성은 요청할 때 Google Cloud 사용료가 발생합니다.</p></CardFooter></Card>
          : <Alert className="voice-api-notice"><Mic aria-hidden="true" /><AlertDescription>마이크 질문을 사용하려면 Google 음성 API 연결을 확인해 주세요. 스포터·피트월 설정은 계속 사용할 수 있습니다.</AlertDescription></Alert>}
        {host.error && <Alert variant="destructive"><AlertDescription>{host.error}</AlertDescription></Alert>}
        <AudioPreferences runtime={runtime} />
      </div>
      <div className="voice-column" aria-label="스포터 설정 영역" tabIndex={0}><SpotterPanel spotter={host.spotter} muted={controls.muted} volume={controls.volume / 100} audioError={host.audioOutput.message} /><p className="text-xs text-muted-foreground"><Headphones className="inline" aria-hidden="true" /> 스포터를 켜면 엔지니어 자동 보고도 함께 시작합니다. 피트월은 별도로 끌 수 있습니다.</p></div>
      <div className="voice-column" aria-label="피트월 설정 영역" tabIndex={0}><PitWall enabled={host.wallEnabled} onToggle={host.toggleWall} preferences={host.wallPreferences} onChange={host.changeWallPreferences} view={host.wallView} engineers={host.status?.engineers} onTestVoice={host.testVoice} busy={busy} speaking={host.audioOutput.isPlaying} /></div>
    </div>
  </section>;
}
