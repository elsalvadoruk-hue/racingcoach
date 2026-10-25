import { renderHook,waitFor } from '@testing-library/react';
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
