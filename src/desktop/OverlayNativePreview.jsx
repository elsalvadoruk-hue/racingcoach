import React, { useEffect, useState } from 'react';
import { Button } from '../components/ui/button';
import { Alert, AlertDescription } from '../components/ui/alert';

const CONTENT_KEYS = ['formation_items','slots','selected','modes','graph','source','auto','quiet','size','detail','timing','manual_session'];

export default function OverlayNativePreview({ apiBase, fetcher, game, session, config, appearance, scene, availability }) {
  const [result,setResult] = useState(null), [failure,setFailure] = useState(null), [retry,setRetry] = useState(0);
  const body = JSON.stringify({
    config:Object.fromEntries(CONTENT_KEYS.map(key=>[key,config[key]])),session,scene,availability,
    appearance:{width:appearance?.width??100,height:appearance?.height??100,
      content_opacity:appearance?.content_opacity??100,background_opacity:appearance?.background_opacity??85},
    pixel_ratio:Math.max(.5,Math.min(3,window.devicePixelRatio||1)),
  });
  const key = game+body;
  useEffect(()=>{
    const abort = new AbortController();
    let active = true;
    const timer = setTimeout(async()=>{
      try {
        const response = await fetcher(`${apiBase}/api/overlay/studio/render?game=${game}`,{
          method:'POST',headers:{'Content-Type':'application/json'},body,signal:abort.signal,
        });
        const data = await response.json();
        if(!response.ok)throw new Error(response.status===404
          ? '실제 오버레이 미리보기를 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.'
          : typeof data.detail==='string'?data.detail:'미리보기를 불러오지 못했습니다.');
        if(data.renderer!=='native-tk-v1'||typeof data.svg!=='string'||!Array.isArray(data.texts)||!Array.isArray(data.ids)||!Number.isFinite(data.width)||data.width<=0||!Number.isFinite(data.height)||data.height<=0)throw new Error('미리보기 응답을 확인할 수 없습니다. 앱과 실행창을 다시 실행하세요.');
        if(active){setResult({key,data});setFailure(null);}
      }catch(error){if(active&&!abort.signal.aborted)setFailure({key,message:error.message});}
    },100);
    return ()=>{active=false;clearTimeout(timer);abort.abort();};
  },[apiBase,fetcher,game,body,key,retry]);
  const data=result?.key===key?result.data:null, error=failure?.key===key?failure.message:null;
  return <figure className="native-preview" data-testid={`hud-${session}`} data-mode={config.modes[session]}
    aria-busy={!data&&!error} style={data?{width:`${data.width}px`}:undefined}>
    {error?<Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={()=>{setFailure(null);setRetry(value=>value+1);}}>다시 시도</Button></AlertDescription></Alert>
      :data?<><img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(data.svg)}`} width={data.width} height={data.height}
        data-renderer={data.renderer} alt={`실제 오버레이 배치 · 샘플 수치 · ${data.texts.join(' · ')}`}/>
        <figcaption className="sr-only">{data.texts.join(' · ')}{config.modes[session]==='coach'&&data.ids.map(id=><span key={id} data-module={id}>{id}</span>)}</figcaption></>
      :<p className="native-preview-loading" role="status">실제 오버레이 미리보기 준비 중…</p>}
  </figure>;
}
