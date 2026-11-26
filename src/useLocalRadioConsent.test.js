import {act,renderHook,waitFor} from '@testing-library/react';
import useLocalRadio from './useLocalRadio';
let mockConsent;
jest.mock('./components/SessionLogAutoSend',()=>({useSessionLogAuto:()=>mockConsent}));
jest.mock('./useSpeechShortcut',()=>()=>({}));
beforeEach(()=>{
 localStorage.clear();mockConsent={state:{enabled:false,available:true}};
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:jest.fn(async()=>({getTracks:()=>[{stop:jest.fn()}]}))}});
 global.fetch=jest.fn(async()=>({ok:true,json:async()=>({})}));
});
test('request with logs off shows requirement before microphone/model, agreeing starts once, revoking stops',async()=>{
 const view=renderHook(()=>useLocalRadio({apiBase:'http://test',game:'iracing',standby:false,inputDevice:''}));
 await act(async()=>view.result.current.prepare());
 expect(view.result.current.logsRequired).toBe(true);
 expect(global.fetch).not.toHaveBeenCalled();expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
 mockConsent={state:{enabled:true,available:true}};view.rerender();
 await waitFor(()=>expect(view.result.current.enabled).toBe(true));
 expect(global.fetch.mock.calls.filter(([url])=>url.endsWith('/activate'))).toHaveLength(1);
 mockConsent={state:{enabled:false,available:true}};view.rerender();
 await waitFor(()=>expect(view.result.current.enabled).toBe(false));
 expect(global.fetch.mock.calls.some(([url])=>url.endsWith('/deactivate'))).toBe(true);
});
test('cancelling requirement does not start when logs are later enabled',async()=>{
 const view=renderHook(()=>useLocalRadio({apiBase:'http://test',game:'iracing',standby:false}));
 await act(async()=>view.result.current.prepare());act(()=>view.result.current.dismissLogsRequired());
 mockConsent={state:{enabled:true}};view.rerender();await act(async()=>{});
 expect(global.fetch).not.toHaveBeenCalled();
});

test('header opens installation first, then consent, and toggles the shared recognizer off',async()=>{
 let installed=false;
 global.fetch.mockImplementation(async()=>({ok:true,json:async()=>({installed:{sensevoice:installed}})}));
 const view=renderHook(()=>useLocalRadio({apiBase:'http://test',game:'iracing',standby:false}));
 await act(async()=>view.result.current.toggleFromHeader());
 expect(view.result.current.settingsOpen).toBe(true);
 expect(view.result.current.logsRequired).toBe(false);
 expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
 act(()=>view.result.current.setSettingsOpen(false));installed=true;
 await act(async()=>view.result.current.toggleFromHeader());
 expect(view.result.current.logsRequired).toBe(true);
 expect(global.fetch.mock.calls.some(([url])=>url.endsWith('/activate'))).toBe(false);
 mockConsent={state:{enabled:true,available:true}};view.rerender();
 await waitFor(()=>expect(view.result.current.enabled).toBe(true));
 await act(async()=>view.result.current.toggleFromHeader());
 expect(view.result.current.enabled).toBe(false);
 expect(global.fetch.mock.calls.some(([url])=>url.endsWith('/deactivate'))).toBe(true);
});

test('changing games during header installation check never starts the old request',async()=>{
 let complete;
 global.fetch.mockImplementation(()=>new Promise(resolve=>{complete=()=>resolve({ok:true,json:async()=>({installed:{sensevoice:true}})});}));
 const view=renderHook(({game})=>useLocalRadio({apiBase:'http://test',game,standby:false}),{initialProps:{game:'iracing'}});
 let pending;act(()=>{pending=view.result.current.toggleFromHeader();});
 view.rerender({game:'lmu'});
 await act(async()=>{complete();await pending;});
 expect(view.result.current.enabled).toBe(false);
 expect(view.result.current.logsRequired).toBe(false);
 expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
});
