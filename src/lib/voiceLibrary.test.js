import { act, renderHook, waitFor } from '@testing-library/react';
import useVoiceLibrary, { cleanVoiceSettings,voiceSelections } from './voiceLibrary';
const original=global.fetch;
beforeEach(()=>{localStorage.clear();global.fetch=jest.fn().mockResolvedValue({ok:true,json:async()=>({packs:[]})});});
afterEach(()=>{global.fetch=original;});
test('a delayed previous backend cannot replace the current library',async()=>{
  let finish;fetch.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const {result,rerender}=renderHook(({base})=>useVoiceLibrary(base),{initialProps:{base:'/first'}});
  rerender({base:'/second'});await waitFor(()=>expect(result.current.view).toEqual({packs:[]}));
  await act(async()=>finish({ok:true,json:async()=>({packs:[{key:'old@1'}]})}));
  expect(result.current.view).toEqual({packs:[]});
});
test('unapply clears both games while preserving unrelated role and driving preferences',async()=>{
  localStorage.setItem('iracing.spotter.settings',JSON.stringify({voice:'Fenrir',repeat_seconds:5,voice_packs:{coach:'remove@1',strategy:'keep@1'}}));
  localStorage.setItem('iracing.spotter.settings.lmu',JSON.stringify({voice:'Aoede',custom_pack:'remove@1',repeat_seconds:2}));
  const {result}=renderHook(()=>useVoiceLibrary('/app'));await waitFor(()=>expect(result.current.view).toBeTruthy());
  await act(()=>result.current.remove('remove@1',false));
  expect(voiceSelections(JSON.parse(localStorage.getItem('iracing.spotter.settings')))).toEqual({strategy:'keep@1'});
  expect(JSON.parse(localStorage.getItem('iracing.spotter.settings')).repeat_seconds).toBe(5);
  expect(cleanVoiceSettings({custom_pack:'remove@1'}).custom_pack).toBeNull();
  expect(voiceSelections(JSON.parse(localStorage.getItem('iracing.spotter.settings.lmu')))).toEqual({});
  expect(fetch.mock.calls.find(([url])=>url.endsWith('/remove'))[1].body).toBe(JSON.stringify({key:'remove@1',delete:false}));
});
