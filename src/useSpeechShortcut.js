import { useCallback, useEffect, useRef, useState } from 'react';

export function keyboardShortcut(e) {
  if (e.repeat || ['Control','Alt','Shift','Meta','Process','Unidentified'].includes(e.key)) return null;
  const vk = e.keyCode;
  if (!vk || vk > 254) return null;
  const mods = Number(e.ctrlKey) + Number(e.altKey)*2 + Number(e.shiftKey)*4 + Number(e.metaKey)*8;
  const name = e.code?.startsWith('Numpad') ? e.code : e.key === ' ' ? 'Space' : e.key?.length === 1 ? e.key.toUpperCase() : e.key;
  return { code: 'key:'+vk+':'+mods, label:[e.ctrlKey && 'Ctrl',e.altKey && 'Alt',e.shiftKey && 'Shift',e.metaKey && 'Win',name].filter(Boolean).join('+') };
}

export default function useSpeechShortcut(apiBase, channel, enabled, onDown, onUp, onError) {
  const [config,setConfig] = useState(null);
  const code=config?.bindings?.[channel]?.code;
  const current=useRef({});
  current.current={onDown,onUp,onError,binding:config?.bindings?.[channel]};
  const refresh=useCallback(async () => {
    const response=await fetch(apiBase+'/api/live-coach/speech-shortcuts');
    const value=await response.json();
    if(!response.ok) throw Error(value.detail || '단축키 설정을 불러오지 못했습니다.');
    setConfig(value); return value;
  },[apiBase]);
  const save=async binding => {
    const latest=await refresh();
    const response=await fetch(apiBase+'/api/live-coach/speech-shortcuts',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...latest,bindings:{...latest.bindings,[channel]:binding}})});
    const value=await response.json();
    if(!response.ok) throw Error(value.detail || '단축키를 저장하지 못했습니다.');
    setConfig(value);
    window.dispatchEvent(new Event('speech-shortcuts-changed'));
  };
  useEffect(() => {
    let closed=false;
    const update=()=>refresh().catch(e=>{if(!closed) current.current.onError?.(e.message);});
    update(); window.addEventListener('speech-shortcuts-changed',update);
    return ()=>{closed=true;window.removeEventListener('speech-shortcuts-changed',update);};
  },[refresh]);
  useEffect(() => {
    if(!enabled || !code) return undefined;
    let socket,timer,closed=false,held=null;
    function connect() {
      const url=new URL(apiBase+'/api/live-coach/speech-shortcuts/events/'+channel);
      url.protocol=url.protocol==='https:'?'wss:':'ws:';
      socket=new WebSocket(url);
      socket.onmessage=({data})=>{
        const event=JSON.parse(data);
        if(event.type==='ptt') event.down ? current.current.onDown?.() : current.current.onUp?.();
        if(event.error) current.current.onError?.(event.error);
      };
      socket.onclose=()=>{current.current.onUp?.();if(!closed) timer=setTimeout(connect,2000);};
      socket.onerror=()=>socket.close();
    }
    connect();
    function down(e) {
      if(e.defaultPrevented || /INPUT|TEXTAREA|SELECT/.test(e.target?.tagName) || e.target?.isContentEditable) return;
      const key=keyboardShortcut(e);
      if(key?.code!==current.current.binding?.code || held!==null) return;
      e.preventDefault(); held=e.keyCode; current.current.onDown?.();
    }
    function up(e) {
      if(held!==e.keyCode) return;
      e.preventDefault(); held=null;current.current.onUp?.();
    }
    const blur=()=>{if(held!==null){held=null;current.current.onUp?.();}};
    window.addEventListener('keydown',down);window.addEventListener('keyup',up);window.addEventListener('blur',blur);
    return ()=>{closed=true;clearTimeout(timer);socket?.close();window.removeEventListener('keydown',down);window.removeEventListener('keyup',up);window.removeEventListener('blur',blur);};
  },[apiBase,channel,enabled,code]);
  return {binding:config?.bindings?.[channel],save,refresh};
}
