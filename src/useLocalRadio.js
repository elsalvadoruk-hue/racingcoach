import { useSessionLogAuto } from './components/SessionLogAutoSend';
import {useCallback,useEffect,useRef,useState} from 'react';
import useLocalSpeech,{localSpeechRequest} from './useLocalSpeech';
import useSpeechShortcut from './useSpeechShortcut';
import useSpeechDialogue from './useSpeechDialogue';
import {VoiceRecorder} from './voiceAudio';

export default function useLocalRadio({apiBase,game,standby,inputDevice,status,cloudBusy,onResult,stopAudio}) {
  const [error,setError]=useState(''),[phase,setPhase]=useState('idle'),[testResult,setTestResult]=useState(null);
  const [speak,setSpeak]=useState(true);
  const [pitCommands,setPitCommandsValue]=useState(()=>{
    try{return localStorage.getItem('iracing.local-speech.pit-commands.v1')==='true';}catch{return false;}
  });
  const [commandResult,setCommandResult]=useState(null);
  const [echoCancellation,setEchoCancellationValue]=useState(()=>{
    try{return localStorage.getItem('iracing.local-speech.echo-cancellation.v1')==='true';}catch{return false;}
  });
  const [inputProcessing,setInputProcessing]=useState(null);
  const logAuto=useSessionLogAuto(apiBase);
  const [logsRequired,setLogsRequired]=useState(false);
  const [settingsOpen,setSettingsOpen]=useState(false),[headerBusy,setHeaderBusy]=useState(false);
  const engine=useLocalSpeech(apiBase,game,standby,setError);
  const dialogue=useSpeechDialogue(apiBase,engine.owner,
    `${game}:${inputDevice}:${status?.connected===true}:${status?.snapshot?.run_id || ''}`,engine.active && !standby);
  const current=useRef({}),capture=useRef(null),epoch=useRef(0),timer=useRef(),mounted=useRef(true),waiting=useRef(false),state=useRef('idle');
  const testing=useRef(false);
  current.current={engine,status,cloudBusy,onResult,stopAudio,speak,inputDevice,echoCancellation,pitCommands,dialogue};
  const setPitCommands=value=>{
    if(state.current!=='idle')return;
    setPitCommandsValue(value===true);
    try{localStorage.setItem('iracing.local-speech.pit-commands.v1',String(value===true));}catch{/* Optional browser preference. */}
  };
  const setEchoCancellation=value=>{
    if(state.current!=='idle')return;
    const enabled=value===true;
    setEchoCancellationValue(enabled);setInputProcessing(null);
    try{localStorage.setItem('iracing.local-speech.echo-cancellation.v1',String(enabled));}catch{/* Optional browser preference. */}
  };
  const change=value=>{state.current=value;if(mounted.current)setPhase(value);};
  const closeCapture=()=>{clearTimeout(timer.current);capture.current?.recorder.close();capture.current=null;};
  const stop=useCallback(()=>{
    current.current.dialogue.cancel('state_changed');
    epoch.current++;closeCapture();change('idle');current.current.stopAudio?.('local_microphone_off');
    if(mounted.current){setInputProcessing(null);setCommandResult(null);}
    return current.current.engine.stop();
  },[]);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;epoch.current += 1;closeCapture();};},[]);
  useEffect(()=>{stop();},[game,inputDevice,standby,stop]);
  useEffect(()=>{if(!engine.active && state.current!=='arming'){epoch.current++;closeCapture();change('idle');}},[engine.active]);
  const prepare=async()=>{
    if(engine.active){await stop();return;}
    if(state.current!=='idle')return;
    if(standby){setError('음성을 재생하는 앱 창에서 켜 주세요.');return;}
    if(!logAuto.state?.enabled){setLogsRequired(true);return;}
    const ticket=++epoch.current;
    change('arming');setError('');
    try{
      // Check permission once, then release the device during standby.
      const mic=await navigator.mediaDevices.getUserMedia({audio:inputDevice && inputDevice!=='default'?{deviceId:{exact:inputDevice}}:true});
      mic.getTracks().forEach(track=>track.stop());
      if(ticket!==epoch.current)return;
      await engine.activate();
      if(ticket!==epoch.current){await engine.stop();return;}
      return true;
    }catch(e){setError(e.message);await engine.stop();return false;}
    finally{if(ticket===epoch.current)change('idle');}
  };
  const prepareRef=useRef(prepare);prepareRef.current=prepare;
  const toggleFromHeader=async()=>{
    if(current.current.engine.active){await stop();return;}
    if(headerBusy || state.current!=='idle')return;
    const ticket=epoch.current;
    setHeaderBusy(true);
    try{
      const installation=await localSpeechRequest(apiBase);
      if(ticket!==epoch.current || !mounted.current)return;
      if(!installation.installed?.[current.current.engine.model]){setSettingsOpen(true);return;}
      if(await prepareRef.current()===false && mounted.current)setSettingsOpen(true);
    }catch(e){if(ticket===epoch.current && mounted.current){setError(e.message);setSettingsOpen(true);}}
    finally{if(mounted.current)setHeaderBusy(false);}
  };
  useEffect(()=>{if(logsRequired && logAuto.state?.enabled){setLogsRequired(false);prepareRef.current();}},[logsRequired,logAuto.state?.enabled]);
  useEffect(()=>{if(logAuto.state && !logAuto.state.enabled && (engine.active || state.current==='arming'))stop();},[logAuto.state,engine.active,stop]);
  const finish=async()=>{
    if(state.current==='starting'){waiting.current=true;return;}
    if(state.current!=='recording')return;
    const ticket=epoch.current, turn=capture.current;
    clearTimeout(timer.current);change('sending');
    try{
      const audio=await turn.recorder.finish({tailMs:180,maxSeconds:10});turn.recorder.close();
      const captureEnd=performance.now();
      if(!mounted.current || ticket!==epoch.current)return;
      capture.current=null;
      const body={owner:engine.owner,model:engine.model,audio_base64:audio};
      const result=await localSpeechRequest(apiBase,turn.test?'/test':'/utterances',
        turn.test?{...body,game}:{...body,capture_id:turn.id,speak:current.current.speak,allow_pit_commands:game==='iracing'&&current.current.pitCommands,
          ...(turn.dialogueId?{dialogue_id:turn.dialogueId}:{}),delivery:{capture_ms:Math.max(0,Math.round(captureEnd-turn.startedAt))}});
      const responseAt=performance.now();
      if(!mounted.current || ticket!==epoch.current)return;
      if(!turn.test)current.current.dialogue.consume(turn.id);
      if(turn.test)setTestResult(result);else {
        const stillEnabled=()=>ticket===epoch.current && current.current.engine.active;
        const onPromptReady=()=>{
          if(mounted.current && stillEnabled())return current.current.dialogue.ready(result.dialogue);
        };
        const playback=await current.current.onResult?.(result,stillEnabled,{captureEnd,responseAt,onPromptReady});
        if(!mounted.current || !stillEnabled())return;
        if(result.dialogue && !result.audio_parts?.length && playback!==false) await onPromptReady();
      }
      if(!turn.test)setCommandResult(result.command_result||null);
      setError(result.warning || '');
    }catch(e){if(ticket===epoch.current && mounted.current){if(turn.dialogueId)current.current.dialogue.cancel('recording_failed');setError(e.message);}}
    finally{turn.recorder.close();if(ticket===epoch.current){testing.current=false;change('idle');}}
  };
  const latestFinish=useRef(finish);latestFinish.current=finish;
  const start=async(test=false)=>{
    if(!engine.active || state.current!=='idle')return;
    if(!logAuto.state?.enabled){setLogsRequired(true);await stop();return;}
    if(current.current.cloudBusy){setError('Google 질문이 끝난 뒤 사용하세요.');return;}
    const ticket=++epoch.current;
    waiting.current=false;change('starting');setError('');current.current.stopAudio?.('local_question');
    const recorder=new VoiceRecorder();const turn={recorder,test};capture.current=turn;testing.current=test;
    timer.current=setTimeout(()=>{
      if(state.current==='starting') {
        epoch.current += 1;closeCapture();current.current.dialogue.cancel('recording_failed');change('idle');
        setError('질문 위치 준비가 지연되어 녹음을 취소했습니다. 다시 시도하세요.');
      } else latestFinish.current();
    },9500);
    try{
      if(test)await current.current.dialogue.cancel('state_changed');
      else turn.dialogueId=await current.current.dialogue.reserve();
      if(ticket!==epoch.current){recorder.close();return;}
      const input=await recorder.open(inputDevice,current.current.echoCancellation?'standard':'echo_off');
      if(ticket!==epoch.current){recorder.close();return;}
      setInputProcessing(input?.processing||null);
      // PTT already reserved the dialogue; capture sound while attaching the
      // position snapshot so a slow metadata request cannot discard speech.
      await recorder.start();turn.startedAt=performance.now();
      if(ticket!==epoch.current){recorder.close();return;}
      if(!test){
        const response=await fetch(apiBase+'/api/live-coach/captures',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
        const value=await response.json();if(!response.ok)throw Error(value.detail);
        turn.id=value.capture_id;
        if(ticket!==epoch.current){recorder.close();return;}
        turn.dialogueId=await current.current.dialogue.begin(turn.id);
      }
      if(ticket!==epoch.current){recorder.close();return;}
      change('recording');
      if(waiting.current)await latestFinish.current();
    }catch(e){recorder.close();if(ticket===epoch.current){if(turn.dialogueId)current.current.dialogue.cancel('recording_failed');setError(e.message);change('idle');}}
  };
  const shortcut=useSpeechShortcut(apiBase,'local',engine.active && !standby,()=>start(false),()=>finish(),setError);
  return {logAuto,logsRequired,dismissLogsRequired:()=>setLogsRequired(false),engine,enabled:engine.active,phase,busy:phase!=='idle',testing:testing.current,error,testResult,speak,setSpeak,prepare,stop,
    settingsOpen,setSettingsOpen,headerBusy,toggleFromHeader,
    dialogue:dialogue.pending,dialogueNotice:dialogue.notice,cancelDialogue:()=>dialogue.cancel('cancelled'),
    echoCancellation,setEchoCancellation,inputProcessing,
    game,pitCommands,setPitCommands,commandResult,
    start:()=>start(false),startTest:()=>start(true),finish,shortcut,
    cancelTest:()=>{if(testing.current){epoch.current++;closeCapture();testing.current=false;change('idle');}}};
}
