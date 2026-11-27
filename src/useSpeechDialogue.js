import { useCallback, useEffect, useRef, useState } from 'react';
import { localSpeechRequest } from './useLocalSpeech';

// Only a requested recording can answer a prompt. This hook never opens a mic.
export default function useSpeechDialogue(apiBase, owner, identity, enabled) {
  const [pending, setPending] = useState(null);
  const [notice, setNotice] = useState('');
  const current = useRef(null), timeout = useRef(null), generation = useRef(0), mounted = useRef(false);
  const post = useCallback((dialogueId, action, extra = {}) => localSpeechRequest(apiBase, `/dialogue/${action}`, {
    owner, dialogue_id: dialogueId, action, ...extra,
  }), [apiBase, owner]);
  const clear = useCallback(() => {
    clearTimeout(timeout.current); current.current = null;
    if (mounted.current) setPending(null);
  }, []);
  const cancel = useCallback((reason = 'cancelled') => {
    generation.current += 1;
    const previous = current.current;
    clear();
    if (mounted.current) setNotice(reason === 'timeout' && previous?.state==='awaiting_confirmation' ? '응답이 없어 확인 대기를 취소했습니다. 새 질문을 말씀해 주세요.' : '');
    if (previous) return post(previous.id, 'cancel', { reason }).catch(() => {});
    return Promise.resolve();
  }, [clear, post]);
  const ready = useCallback(async dialogue => {
    if (!dialogue?.id || !['awaiting_confirmation','followup'].includes(dialogue.state)) return;
    await cancel('state_changed');
    const ticket = generation.current;
    // Retain this ID while ready is in flight so cleanup can cancel it too.
    current.current = dialogue;
    try {
      const response = await post(dialogue.id, 'ready', {capture_id:dialogue.capture_id});
      if (!mounted.current || ticket !== generation.current) return;
      const next = response.dialogue;
      if (!next || next.id !== dialogue.id || !['awaiting_confirmation','followup'].includes(next.state) || !(next.remaining_ms > 0)) { clear(); return; }
      const remaining = Math.min(next.state==='awaiting_confirmation'?8000:20000, next.remaining_ms);
      const value = { ...next, remaining_ms: remaining, expiresAt: Date.now() + remaining };
      current.current = value; setPending(next.state==='awaiting_confirmation'?value:null); setNotice('');
      timeout.current = setTimeout(() => cancel('timeout'), remaining);
    } catch {
      if (mounted.current && ticket === generation.current) {
        await cancel('state_changed');
        setNotice('확인 대기를 시작하지 못했습니다. 질문을 다시 말씀해 주세요.');
      }
    }
  }, [cancel, clear, post]);
  const reserve = useCallback(async () => {
    if(mounted.current)setNotice('');
    const previous = current.current;
    if (!previous) return null;
    if (!previous.expiresAt || Date.now() >= previous.expiresAt) { await cancel('timeout'); return null; }
    const ticket = generation.current;
    clearTimeout(timeout.current);
    try {
      const response = await post(previous.id, 'begin');
      if (!mounted.current || ticket !== generation.current) return null;
      if(response.accepted!==true){clear();return null;}
      current.current={...previous,reserved:true,expiresAt:Date.now()+25000};
      setPending(null);setNotice('');
      return previous.id;
    } catch {
      if (mounted.current && ticket === generation.current) await cancel('state_changed');
      return null;
    }
  }, [cancel, clear, post]);
  const begin = useCallback(async captureId => {
    const previous=current.current;
    if(!previous?.reserved)return null;
    if(Date.now()>=previous.expiresAt){await cancel('recording_failed');return null;}
    const ticket=generation.current;
    try{
      const response=await post(previous.id,'begin',{capture_id:captureId});
      if(!mounted.current || ticket!==generation.current)return null;
      if(response.accepted!==true){clear();return null;}
      current.current={...previous,captureId};
      return previous.id;
    }catch{
      if(mounted.current && ticket===generation.current)await cancel('recording_failed');
      return null;
    }
  },[cancel,clear,post]);
  const consume=useCallback(captureId=>{
    if(current.current?.captureId===captureId){generation.current+=1;clear();}
  },[clear]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; cancel('state_changed'); };
  }, [cancel]);
  useEffect(() => { cancel('state_changed'); }, [identity, enabled, cancel]);
  return { pending, notice, ready, reserve, begin, consume, cancel };
}
