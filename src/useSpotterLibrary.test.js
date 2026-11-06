import { act,renderHook,waitFor } from '@testing-library/react';
import useSpotter from './useSpotter';
test('opening saved roles applies them before preview without preparing or synthesizing clips',async()=>{
  const original=global.fetch;localStorage.clear();
  localStorage.setItem('iracing.spotter.settings',JSON.stringify({voice:'Fenrir',voice_packs:{coach:'resume-coach@1',strategy:'resume-fuel@1'}}));
  global.fetch=jest.fn().mockResolvedValue({ok:true,json:async()=>({packs:[]})});
  try{
    const {result}=renderHook(()=>useSpotter('/local',{play:jest.fn(),cancelPriority:jest.fn()}));
    await waitFor(()=>expect(result.current.settingsBusy).toBe(false));
    const call=fetch.mock.calls.find(([url])=>url.endsWith('/spotter/settings'));
    expect(JSON.parse(call[1].body).voice_packs).toEqual({coach:'resume-coach@1',strategy:'resume-fuel@1'});
    expect(fetch.mock.calls.some(([url])=>url.endsWith('/prepare'))).toBe(false);
  }finally{global.fetch=original;localStorage.clear();}
});

test.each(['active', 'idle'])('a library refresh preserves active directions and refreshes the next start: %s',async mode=>{
  const saved={fetch:global.fetch,WebSocket:global.WebSocket};
  const sockets=[],recordDelivery=jest.fn();let generation=1;
  const manifest=()=>({voice:'Fenrir',clips:Object.fromEntries(['test','left','right'].map(key=>
    [key,{text:key,audio_base64:`${key}-${generation}`}]))});
  global.fetch=jest.fn(async url=>({ok:true,status:200,json:async()=>url.endsWith('/prepare')?manifest():
    url.endsWith('/install-status')?{state:'IDLE'}:{packs:[]}}));
  global.WebSocket=jest.fn(()=>{const socket={close:jest.fn()};sockets.push(socket);return socket;});
  const audio={play:jest.fn(async(_audio,_mime,options)=>{options.onStatus('started','');return true;}),
    setPriorityGate:jest.fn(),cancelPriority:jest.fn(),muted:false,volume:1};
  const send=async(key,raw,serial)=>act(async()=>sockets.at(-1).onmessage({data:JSON.stringify({
    type:'spotter',active:true,occupied:true,run_id:'synthetic',serial,state:raw,raw_state:raw,
    events:[{key,serial,state:raw,run_id:'synthetic',expires_in_ms:500}]
  })}));
  localStorage.clear();let hook;
  try{
    hook=renderHook(()=>useSpotter('http://synthetic',audio,{recordDelivery}));
    await waitFor(()=>expect(hook.result.current.library.view).not.toBeNull());
    await act(async()=>hook.result.current.toggle());
    await send('left',2,1);
    expect(audio.play).toHaveBeenLastCalledWith('left-1','audio/mpeg',expect.any(Object));
    if(mode==='idle')await act(async()=>hook.result.current.toggle());
    generation=2;
    await act(async()=>window.dispatchEvent(new Event('voice-library-changed')));
    expect(fetch.mock.calls.filter(([url])=>url.endsWith('/prepare'))).toHaveLength(1);
    if(mode==='active'){
      await send('right',3,2);
    }
    expect(audio.play).toHaveBeenLastCalledWith(mode==='active'?'right-1':'left-1','audio/mpeg',expect.any(Object));
    expect(recordDelivery.mock.calls.some(([,status,reason])=>status==='failed'&&reason==='missing_clip')).toBe(false);
    if(mode==='active')await act(async()=>hook.result.current.toggle());
    await act(async()=>hook.result.current.toggle());
    await send('right',3,3);
    expect(audio.play).toHaveBeenLastCalledWith('right-2','audio/mpeg',expect.any(Object));
    expect(fetch.mock.calls.filter(([url])=>url.endsWith('/prepare'))).toHaveLength(2);
    expect(hook.result.current.error).toBe('');
  }finally{hook?.unmount();Object.assign(global,saved);localStorage.clear();}
});
