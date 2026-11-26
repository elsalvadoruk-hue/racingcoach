import React,{useState} from 'react';
import {Mic,Square} from 'lucide-react';
import {Button} from './ui/button';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription,DialogTrigger} from './ui/dialog';
import {Field,FieldLabel,FieldDescription,FieldGroup} from './ui/field';
import {Switch} from './ui/switch';
import {Alert,AlertDescription,AlertTitle} from './ui/alert';
import {ConsentDialog} from './SessionLogAutoSend';
import LocalSpeechSettings from './LocalSpeechSettings';
import SpeechShortcutField from './SpeechShortcutField';
import LocalSpeechCorrection from './LocalSpeechCorrection';

export function SpeechLogRequirement({radio}) {
  const auto=radio?.logAuto;
  if(!auto)return null;
  return <>
    <Dialog open={radio.logsRequired && !auto.asking} onOpenChange={open=>{if(!open)radio.dismissLogsRequired();}}>
      <DialogContent><DialogHeader><DialogTitle>음성 인식은 로그 보내기가 필수입니다</DialogTitle><DialogDescription>인식률 개선을 위해 한동안 로컬 인식 문장·보정·질문 연결·답변·처리 결과를 받습니다. 인식 테스트도 포함하며 녹음 원음과 Google 질문은 보내지 않습니다.</DialogDescription></DialogHeader>
      <p>전송 내용을 확인하고 로그 보내기를 켜면 음성 인식을 시작합니다. 스포터·피트월은 로그 보내기 없이 사용할 수 있습니다.</p>
      <Button disabled={!auto.state?.available || auto.busy} onClick={()=>auto.toggle(true)}>전송 안내 확인하고 로그 보내기 켜기</Button>
      {!auto.state?.available && <p role="alert">로그 보내기 연결 상태를 확인해 주세요.</p>}
      <Button variant="outline" onClick={radio.dismissLogsRequired}>나중에</Button></DialogContent>
    </Dialog>
    <ConsentDialog auto={auto}/>
  </>;
}

export function LocalSpeechSettingsButton({radio}) {
  if(!radio)return null;
  return <Button size="sm" variant="outline" aria-label="음성 인식" onClick={()=>radio.setSettingsOpen(true)}><Mic data-icon="inline-start"/>음성 인식{radio.enabled?' · 켜짐':''}</Button>;
}

export default function LocalSpeechDialog({apiBase,radio,hideTrigger=false}) {
  const [ownOpen,setOwnOpen]=useState(false);
  const [modelStatus,setModelStatus]=useState(null);
  if(!radio)return null;
  const open=radio.settingsOpen ?? ownOpen,setOpen=radio.setSettingsOpen || setOwnOpen;
  const recording=['starting','recording'].includes(radio.phase),result=radio.testResult;
  const needsInstall=!radio.enabled && modelStatus?.installed?.[radio.engine.model]!==true;
  const modelSettings=<details key="model-settings" open={needsInstall || undefined}>
    <summary>{needsInstall?'먼저 음성 인식 모델을 설치하세요':'설치·모델·질문 예시'}</summary>
    <LocalSpeechSettings apiBase={apiBase} controller={radio.engine} armed={radio.enabled} busy={radio.busy} onStatusChange={setModelStatus}/>
  </details>;
  return <><Dialog open={open} onOpenChange={value=>{setOpen(value);if(!value)radio.cancelTest?.();}}>
    {!hideTrigger && <DialogTrigger asChild><Button size="sm" variant="outline" aria-label="음성 인식"><Mic data-icon="inline-start"/>음성 인식{radio.enabled?' · 켜짐':''}</Button></DialogTrigger>}
    <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader><DialogTitle>음성 인식 · 내 PC에서 질문</DialogTitle><DialogDescription>SenseVoice로 한국어 질문과 피트 명령을 처리합니다. Google 질문은 별도입니다.</DialogDescription></DialogHeader>
      {open && <FieldGroup>
        {needsInstall && modelSettings}
        <Field><FieldLabel>음성 인식</FieldLabel><div className="flex flex-wrap gap-2">
          <Button disabled={!radio.enabled && (radio.busy || needsInstall)} onClick={radio.prepare}>{radio.enabled?'음성 인식 끄기':radio.phase==='arming'?'모델 준비 중…':'음성 인식 켜기'}</Button>
          <Button variant="outline" disabled={!radio.enabled || radio.busy} onClick={radio.start}>질문 녹음 시작</Button>
          {recording && !radio.testing && <Button variant="destructive" onClick={radio.finish}><Square data-icon="inline-start"/>녹음 완료 · 전송</Button>}
        </div><p role="status">{radio.phase==='sending'?'음성을 인식하고 있습니다.':recording?'듣고 있습니다 · 최대 10초':radio.enabled?'대기 중 · '+(radio.shortcut?.binding?.label || '단축키 미지정'):needsInstall?'꺼짐 · 위에서 인식기 설치를 완료하세요.':'꺼짐 · 음성 인식 켜기로 시작하세요.'}</p>
        <FieldDescription>한 번 켜면 다음 앱 실행 때 같은 마이크에서 자동으로 준비합니다. 직접 끄면 자동 시작도 해제됩니다. 대기 중에는 마이크와 인식 연산을 멈추고 모델만 메모리에 유지합니다.</FieldDescription></Field>
        {radio.error && <Alert variant="destructive"><AlertDescription>{radio.error}</AlertDescription></Alert>}
        {radio.commandResult && <Alert><AlertTitle>피트 명령 결과</AlertTitle><AlertDescription>{radio.commandResult.message}</AlertDescription></Alert>}
        <Field><FieldLabel>마이크 인식 테스트</FieldLabel><div className="flex flex-wrap gap-2">
          {recording && radio.testing ? <Button variant="destructive" onClick={radio.finish}>테스트 녹음 완료</Button> : <Button variant="outline" disabled={!radio.enabled || radio.busy} onClick={radio.startTest}>인식 테스트 시작</Button>}
        </div><FieldDescription>주행 없이 인식을 확인합니다. 피트 명령을 전송하거나 답변을 재생하지 않습니다. 결과는 기록 화면에 저장하고 개선용 로그로 보냅니다.</FieldDescription>
        {result && <div aria-label="음성 인식 테스트 결과"><p>인식: {result.text || '인식된 문장 없음'}</p><LocalSpeechCorrection detail={result}/><p>연결 질문: {result.topic_name || (result.audio_reason==='no_speech'?'목소리 없음':result.audio_reason==='uncertain_audio'?'음성 인식 불확실':'지원 질문 미확인')}</p><p>처리 {(result.total_ms/1000).toFixed(2)}초 · 기록에 저장됨</p></div>}</Field>
        <SpeechShortcutField apiBase={apiBase} shortcut={radio.shortcut} disabled={radio.enabled || radio.busy} label="로컬 질문 단축키"/>
        <Field orientation="horizontal"><FieldLabel htmlFor="local-speech-answer">답변·피트 복창을 음성으로 듣기</FieldLabel><Switch id="local-speech-answer" checked={radio.speak} onCheckedChange={radio.setSpeak}/></Field>
        {!needsInstall && modelSettings}
        <details><summary>피트 명령·마이크 상세 설정</summary><FieldGroup>
          <Field><Field orientation="horizontal"><FieldLabel htmlFor="local-pit-commands">iRacing 피트 명령 보내기</FieldLabel><Switch id="local-pit-commands" checked={radio.pitCommands===true} onCheckedChange={radio.setPitCommands} disabled={radio.busy||radio.game!=='iracing'}/></Field>
          <FieldDescription>“피트 레인 타이어 교체”는 웻 네 바퀴, “피트 타이어 교체”는 슬릭 네 바퀴로 설정합니다. “피트 급유 15리터”는 추가 급유 명령이며 1~150리터를 지원합니다. 공기압은 100~400kPa 범위에서 단위를 말하며 해당 타이어 교체도 선택됩니다. 게임에서 설정을 확인한 뒤 복창합니다. 반영 미확인은 따로 알리며 결과는 기록에서도 확인합니다.</FieldDescription></Field>
          <Field><Field orientation="horizontal"><FieldLabel htmlFor="local-speech-echo">로컬 마이크 에코 제거</FieldLabel><Switch id="local-speech-echo" checked={radio.echoCancellation===true} onCheckedChange={radio.setEchoCancellation} disabled={radio.busy}/></Field>
          <FieldDescription>기본 꺼짐. Chrome에서 다른 소리를 재생할 때 말소리 변형을 줄이기 위한 설정입니다. 소음 억제와 자동 음량은 유지하며 다음 녹음부터 적용합니다. Google 질문은 별도입니다.</FieldDescription>
          {radio.inputProcessing && <FieldDescription>브라우저 적용값: 에코 제거 {radio.inputProcessing.effective?.echoCancellation===false?'꺼짐':radio.inputProcessing.effective?.echoCancellation===true?'켜짐':'확인되지 않음'}</FieldDescription>}</Field>
        </FieldGroup></details>
        <p className="text-sm text-muted-foreground">지난 질문은 기록 → 로컬 음성 인식 기록에서 검색합니다. 로그 보내기를 끄면 음성 인식도 중지합니다.</p>
      </FieldGroup>}
    </DialogContent>
  </Dialog><SpeechLogRequirement radio={radio}/></>;
}
