import { act, renderHook } from '@testing-library/react';
import useSpeechDialogue from './useSpeechDialogue';

const prompt = { id:'dialogue-one', capture_id:'capture-one', state:'awaiting_confirmation', prompt:'경기 남은 시간 말씀이신가요?', remaining_ms:8000, topic:'race_time' };
beforeEach(() => {
  jest.useFakeTimers();
  global.fetch=jest.fn(async(_url,options)=>{
    const request=JSON.parse(options.body);
    return {ok:true,json:async()=>request.action==='begin'?{accepted:true}:{dialogue:request.action==='cancel'?null:prompt}};
  });
});
afterEach(()=>jest.useRealTimers());
const requests=()=>global.fetch.mock.calls.map(([,options])=>JSON.parse(options.body));

test('a ready prompt expires silently after eight seconds and cannot be claimed by a late answer',async()=>{
  const {result}=renderHook(()=>useSpeechDialogue('http://test','owner','run-one',true));
  await act(async()=>result.current.ready(prompt));
  expect(result.current.pending.id).toBe(prompt.id);
  await act(async()=>jest.advanceTimersByTime(7999));
  expect(result.current.pending).not.toBeNull();
  await act(async()=>jest.advanceTimersByTime(1));
  expect(result.current.pending).toBeNull();
  expect(result.current.notice).toContain('응답이 없어');
  expect(requests().at(-1)).toMatchObject({action:'cancel',reason:'timeout'});
  let id;
  await act(async()=>{id=await result.current.begin('late-capture');});
  expect(id).toBeNull();
  expect(requests().some(row=>row.action==='begin')).toBe(false);
});

test('PTT claims exactly the pending prompt and removes its countdown while a bounded capture runs',async()=>{
  const {result}=renderHook(()=>useSpeechDialogue('http://test','owner','run-one',true));
  await act(async()=>result.current.ready(prompt));
  await act(async()=>jest.advanceTimersByTime(7000));
  let id;
  await act(async()=>result.current.reserve());
  await act(async()=>{id=await result.current.begin('capture-two');});
  expect(id).toBe(prompt.id);expect(result.current.pending).toBeNull();
  await act(async()=>jest.advanceTimersByTime(2000));
  expect(requests().at(-1)).toMatchObject({action:'begin',capture_id:'capture-two',dialogue_id:prompt.id});
});

test.each(['session','disabled','owner'])('%s changes clear a pending prompt without recording anything',async kind=>{
  const {result,rerender}=renderHook(({owner,identity,enabled})=>useSpeechDialogue('http://test',owner,identity,enabled),
    {initialProps:{owner:'owner',identity:'run-one',enabled:true}});
  await act(async()=>result.current.ready(prompt));
  await act(async()=>rerender({owner:kind==='owner'?'other':'owner',identity:kind==='session'?'run-two':'run-one',enabled:kind!=='disabled'}));
  expect(result.current.pending).toBeNull();
  expect(requests()).toContainEqual(expect.objectContaining({action:'cancel',reason:'state_changed',dialogue_id:prompt.id}));
});

test('late ready replies cannot resurrect a prompt after identity change',async()=>{
  let respond,pending;
  global.fetch=jest.fn((_url,options)=>JSON.parse(options.body).action==='ready'?new Promise(resolve=>{respond=resolve;}):Promise.resolve({ok:true,json:async()=>({})}));
  const {result,rerender}=renderHook(({identity})=>useSpeechDialogue('http://test','owner',identity,true),{initialProps:{identity:'run-one'}});
  act(()=>{pending=result.current.ready(prompt);});
  await act(async()=>{await Promise.resolve();});
  act(()=>rerender({identity:'run-two'}));
  await act(async()=>{respond({ok:true,json:async()=>({dialogue:prompt})});await pending;});
  expect(result.current.pending).toBeNull();
});

test('begin rejected by the server clears the local prompt instead of supplying a stale ID',async()=>{
  const {result}=renderHook(()=>useSpeechDialogue('http://test','owner','run-one',true));
  await act(async()=>result.current.ready(prompt));
  await act(async()=>result.current.reserve());
  global.fetch.mockResolvedValueOnce({ok:true,json:async()=>({dialogue:null})});
  let id;
  await act(async()=>{id=await result.current.begin('capture-two');});
  expect(id).toBeNull();expect(result.current.pending).toBeNull();
});

test('ordinary answers retain a hidden twenty-second followup that expires without a confirmation notice',async()=>{
  const followup={...prompt,state:'followup',prompt:'',remaining_ms:20000};
  global.fetch.mockResolvedValueOnce({ok:true,json:async()=>({dialogue:followup})});
  const {result}=renderHook(()=>useSpeechDialogue('http://test','owner','run-one',true));
  await act(async()=>result.current.ready(followup));
  expect(result.current.pending).toBeNull();
  await act(async()=>jest.advanceTimersByTime(20000));
  expect(result.current.notice).toBe('');
  expect(requests().at(-1)).toMatchObject({action:'cancel',reason:'timeout'});
});

test('PTT reserves before hardware latency and binds its capture after the eight-second silence deadline',async()=>{
  const {result}=renderHook(()=>useSpeechDialogue('http://test','owner','run-one',true));
  await act(async()=>result.current.ready(prompt));
  await act(async()=>jest.advanceTimersByTime(7900));
  await act(async()=>result.current.reserve());
  await act(async()=>jest.advanceTimersByTime(1500));
  let id;
  await act(async()=>{id=await result.current.begin('slow-hardware');});
  expect(id).toBe(prompt.id);expect(result.current.notice).toBe('');
  expect(requests().filter(row=>row.action==='begin')).toEqual([
    expect.not.objectContaining({capture_id:expect.any(String)}),
    expect.objectContaining({capture_id:'slow-hardware'}),
  ]);
});
