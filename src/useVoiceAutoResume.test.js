import {act,renderHook,waitFor} from '@testing-library/react';
import useVoiceAutoResume from './useVoiceAutoResume';
import {resolveDevice} from './voiceDeviceIdentity';

const mic={deviceId:'mic',groupId:'m',label:'Microphone',kind:'audioinput'};
const speaker={deviceId:'speaker',groupId:'s',label:'Speakers',kind:'audiooutput'};
const identity=d=>({id:d.deviceId,group:d.groupId,label:d.label});
const key='iracing.voice.resume.v1:iracing';
let devices,listener;
function props(){return {game:'iracing',input:'',audio:{outputId:'',standby:false},
 spotter:{enabled:false,preparing:false,toggle:jest.fn()},
 localRadio:{enabled:false,prepare:jest.fn(),stop:jest.fn(),logAuto:{state:{enabled:true}}},
 wallEnabled:false,setWallEnabled:jest.fn()};}
beforeEach(()=>{
 localStorage.clear();devices=[mic,speaker,{...mic,deviceId:'default'},{...speaker,deviceId:'default'}];
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{enumerateDevices:jest.fn(async()=>devices),
  addEventListener:jest.fn((_,callback)=>{listener=callback;}),removeEventListener:jest.fn()}});
});
const saved=()=>localStorage.setItem(key,JSON.stringify({local:true,spotter:true,wall:true,mic:identity(mic),radioMic:identity(mic),speaker:identity(speaker)}));

test('Windows default resolves physical device and hidden identities never match',()=>{
 expect(resolveDevice(devices,'audioinput','')).toEqual(identity(mic));
 expect(resolveDevice([{...mic,deviceId:'default'}],'audioinput','')).toBeNull();
 expect(resolveDevice([{...mic,label:''}],'audioinput','mic')).toBeNull();
});
test('same devices restore once without opening microphone in the resume detector',async()=>{
 saved();const p=props();const view=renderHook(v=>useVoiceAutoResume(v),{initialProps:p});
 await waitFor(()=>expect(p.spotter.toggle).toHaveBeenCalledTimes(1));
 expect(p.localRadio.prepare).toHaveBeenCalledTimes(1);
 await act(async()=>listener());expect(p.localRadio.prepare).toHaveBeenCalledTimes(1);
 view.rerender({...p,spotter:{...p.spotter,enabled:true}});
 await waitFor(()=>expect(p.setWallEnabled).toHaveBeenCalledWith(true));
});
test('changed Windows default microphone with same alias prevents restoration',async()=>{
 saved();devices=[{...mic,deviceId:'new',groupId:'new'},{...mic,deviceId:'default',groupId:'new'},speaker,{...speaker,deviceId:'default'}];
 const p=props();const view=renderHook(()=>useVoiceAutoResume(p));
 await waitFor(()=>expect(view.result.current).toContain('달라'));
 expect(p.localRadio.prepare).not.toHaveBeenCalled();expect(p.spotter.toggle).not.toHaveBeenCalled();
});
test('changed speaker holds radio but same microphone restores recognition',async()=>{
 saved();devices=[mic,{...mic,deviceId:'default'},{...speaker,deviceId:'new',groupId:'new'},{...speaker,deviceId:'default',groupId:'new'}];
 const p=props();renderHook(()=>useVoiceAutoResume(p));
 await waitFor(()=>expect(p.localRadio.prepare).toHaveBeenCalledTimes(1));expect(p.spotter.toggle).not.toHaveBeenCalled();
});
test('logs disabled blocks local restoration but allows radio; standby tab restores nothing',async()=>{
 saved();const p=props();p.localRadio.logAuto.state.enabled=false;
 const view=renderHook(v=>useVoiceAutoResume(v),{initialProps:{...p,audio:{...p.audio,standby:true}}});
 await act(async()=>{});expect(p.spotter.toggle).not.toHaveBeenCalled();
 view.rerender(p);await waitFor(()=>expect(p.spotter.toggle).toHaveBeenCalledTimes(1));
 expect(p.localRadio.prepare).not.toHaveBeenCalled();
});
test('new install never starts and only confirmed playback remembers radio',async()=>{
 const p=props();const view=renderHook(v=>useVoiceAutoResume(v),{initialProps:p});
 await act(async()=>{});expect(p.spotter.toggle).not.toHaveBeenCalled();
 view.rerender({...p,spotter:{...p.spotter,enabled:true},localRadio:{...p.localRadio,enabled:true},wallEnabled:true});
 await waitFor(()=>expect(JSON.parse(localStorage.getItem(key)).local).toBe(true));
 expect(JSON.parse(localStorage.getItem(key)).speaker).toBeUndefined();
 view.rerender({...p,audio:{...p.audio,playbackConfirmed:identity(speaker)},spotter:{...p.spotter,enabled:true},wallEnabled:true});
 await waitFor(()=>expect(JSON.parse(localStorage.getItem(key)).speaker).toEqual(identity(speaker)));
 view.rerender(p);await waitFor(()=>expect(JSON.parse(localStorage.getItem(key)).spotter).toBe(false));
 expect(JSON.parse(localStorage.getItem(key)).local).toBe(false);
});
