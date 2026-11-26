import React,{useEffect,useRef,useState} from 'react';
import {Button} from './ui/button';
import {Field,FieldLabel,FieldDescription} from './ui/field';
import {buttonSocket} from '../useButtonRadio';
import {keyboardShortcut} from '../useSpeechShortcut';

export default function SpeechShortcutField({apiBase,shortcut,disabled,label='질문 단축키'}) {
  const [capturing,setCapturing]=useState(false),[choices,setChoices]=useState([]),[error,setError]=useState('');
  const apply=useRef();
  apply.current=async value=>{
    setCapturing(false);setChoices([]);setError('');
    try{await shortcut.save(value);}catch(e){setError(e.message);}
  };
  useEffect(()=>{
    if(!capturing) return undefined;
    let timer,done=false;
    const found=new Map();
    const accept=value=>{
      if(done)return;
      found.set(value.code,value);
      if(!timer) timer=setTimeout(()=>{
        done=true;
        if(found.size===1) apply.current(value);else setChoices([...found.values()]);
      },160);
    };
    const down=e=>{
      e.preventDefault();e.stopImmediatePropagation();
      if(e.key==='Escape'){setCapturing(false);return;}
      const value=keyboardShortcut(e);if(value)accept(value);
    };
    window.addEventListener('keydown',down,true);
    const socket=buttonSocket(apiBase,true);
    socket.onmessage=({data})=>{
      const event=JSON.parse(data);
      if(event.code)accept({code:event.code,label:'휠 '+event.code.split(':').at(-1)});
      if(event.error)setError(event.error);
    };
    return()=>{done=true;clearTimeout(timer);window.removeEventListener('keydown',down,true);socket.close();};
  },[capturing,apiBase]);
  return <Field><FieldLabel>{label}</FieldLabel><div className="flex flex-wrap items-center gap-2">
    <kbd>{shortcut.binding?.label || '지정 안 됨'}</kbd>
    <Button variant="outline" disabled={disabled} onClick={()=>{setChoices([]);setCapturing(!capturing);}}>{capturing?'지정 취소':'단축키 지정'}</Button>
    <Button variant="ghost" disabled={disabled || !shortcut.binding} onClick={()=>apply.current(null)}>연결 해제</Button>
  </div><FieldDescription>{capturing?'키보드 조합이나 휠 버튼을 누르세요. Esc로 취소합니다.':'누른 동안 녹음하고 놓으면 전송합니다. 무전 버튼·다른 음성 질문과 중복할 수 없습니다.'}</FieldDescription>
  {!!choices.length && <div className="flex flex-wrap gap-2">{choices.map(value=><Button key={value.code} onClick={()=>apply.current(value)}>{value.label} 사용</Button>)}</div>}
  {error && <p role="alert">{error}</p>}</Field>;
}
