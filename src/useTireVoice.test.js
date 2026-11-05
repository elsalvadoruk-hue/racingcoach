import { act, renderHook } from '@testing-library/react';
import useTireVoice from './useTireVoice';

const event = {id:'tire-event-1',key:'pressure_high_FL',text:'왼쪽 앞 타이어 압력 높음.',quiet:true,remaining_s:12};
const status = patch => ({version:1,voice_enabled:true,enabled:false,running:true,voice_event:event,...patch});
const response = data => ({ok:true,json:async()=>data});
const makeOutput = patch => ({standby:false,muted:false,volume:1,isPlaying:false,audioBusy:()=>false,
  isPriorityBlocked:()=>false,play:jest.fn(async (audio,mime,options)=>{ options.onStatus('started');return true; }),...patch});
const settle = () => act(async()=>{for(let i=0;i<20;i+=1) await Promise.resolve();});

beforeEach(()=>jest.useFakeTimers());
afterEach(()=>jest.useRealTimers());

test('voice only uses the shared pitwall output and acknowledges actual start',async()=>{
  const output=makeOutput(),recordDelivery=jest.fn();
  const fetcher=jest.fn(async url=>response(url.includes('/render')?{event,audio_base64:'cached-audio'}:status()));
  const view=renderHook(()=>useTireVoice('http://local','iracing',output,{recordDelivery},fetcher));
  await settle();
  expect(output.play).toHaveBeenCalledWith('cached-audio','audio/mpeg',expect.objectContaining({channel:'pitwall',interrupt:false,priority:0}));
  expect(fetcher).toHaveBeenCalledWith('http://local/api/tire-overlay/voice/tire-event-1/ack?game=iracing',{method:'POST'});
  expect(recordDelivery).toHaveBeenCalledWith(expect.objectContaining({engineer:'coach',key:'tire_pressure_high_FL'}),'started',undefined);
  await act(async()=>jest.advanceTimersByTime(1000));await settle();
  expect(output.play).toHaveBeenCalledTimes(1);view.unmount();
});

test('tire playback results belong to the explicitly linked driving session',async()=>{
  const output=makeOutput(),recordDelivery=jest.fn();
  const fetcher=jest.fn(async url=>response(url.includes('/render')
    ?{event:{...event,run_id:'actual-driving-run'},audio_base64:'cached-audio'}:status()));
  const view=renderHook(()=>useTireVoice('', 'iracing', output, {recordDelivery}, fetcher));
  await settle();
  expect(recordDelivery).toHaveBeenCalledWith(expect.objectContaining({run_id:'actual-driving-run'}),'started',undefined);
  view.unmount();
});

test.each([{standby:true},{muted:true},{volume:0},{isPlaying:true},{audioBusy:()=>true},{isPriorityBlocked:()=>true}])('busy or inactive audio owner never overlaps other radio: %p',async patch=>{
  const output=makeOutput(patch),fetcher=jest.fn(async()=>response(status()));
  const view=renderHook(()=>useTireVoice('', 'iracing', output, {}, fetcher));
  await settle();expect(output.play).not.toHaveBeenCalled();expect(fetcher).toHaveBeenCalledTimes(1);view.unmount();
});

test('missing prepared audio is skipped without requesting synthesis or ack',async()=>{
  const output=makeOutput(),fetcher=jest.fn(async url=>response(url.includes('/render')?{event:null,missing_prepared_audio:true}:status()));
  const view=renderHook(()=>useTireVoice('', 'iracing', output, {}, fetcher));
  await settle();await act(async()=>jest.advanceTimersByTime(1000));await settle();
  expect(fetcher.mock.calls.filter(([url])=>url.includes('/render'))).toHaveLength(1);
  expect(fetcher.mock.calls.some(([url])=>/ack|prepare|synth/.test(url))).toBe(false);
  expect(output.play).not.toHaveBeenCalled();view.unmount();
});

test('recovered state or changed context after audio preparation cancels playback',async()=>{
  const output=makeOutput();let polls=0;
  const fetcher=jest.fn(async url=>response(url.includes('/render')?{event,audio_base64:'cached'}:status({voice_event:++polls===1?event:null})));
  const view=renderHook(()=>useTireVoice('', 'iracing',output,{},fetcher));
  await settle();expect(output.play).not.toHaveBeenCalled();view.unmount();
});

test('switching games aborts old rendering and ignores a response that ignores abort',async()=>{
  const output=makeOutput();let finish,oldSignal;
  const fetcher=jest.fn(async(url,options)=>{
    if(url.includes('/render')){oldSignal=options.signal;return new Promise(resolve=>{finish=resolve;});}
    return response(url.includes('game=lmu')?status({voice_enabled:false,voice_event:null}):status());
  });
  const view=renderHook(({game})=>useTireVoice('',game,output,{},fetcher),{initialProps:{game:'iracing'}});
  await settle();expect(finish).toBeDefined();view.rerender({game:'lmu'});await settle();
  expect(oldSignal.aborted).toBe(true);
  await act(async()=>finish(response({event,audio_base64:'late'})));await settle();
  expect(output.play).not.toHaveBeenCalled();view.unmount();
});
