import {act,renderHook,waitFor} from '@testing-library/react';
import useVoicePackInstall from './voicePackInstall';
const originalFetch=global.fetch, originalXHR=global.XMLHttpRequest;
const response=value=>({ok:true,status:200,json:async()=>value});
afterEach(()=>{global.fetch=originalFetch;global.XMLHttpRequest=originalXHR;});

test('reopening the page restores a running server installation then reloads installed choices',async()=>{
  let status={id:'restored',kind:'install',state:'RUNNING',stage:'copying',done:4,total:9,elapsed_s:7};
  global.fetch=jest.fn(async()=>response(status));const reload=jest.fn();
  const {result}=renderHook(()=>useVoicePackInstall('/app',reload,jest.fn()));
  await waitFor(()=>expect(result.current.operation?.done).toBe(4));
  await expect(result.current.install('again')).rejects.toThrow('진행 중');
  status={...status,state:'COMPLETE',imported:{key:'ready@1',name:'완료팩'}};
  await waitFor(()=>expect(result.current.operation?.state).toBe('COMPLETE'),{timeout:2500});
  expect(reload).toHaveBeenCalledTimes(1);
});

test('upload bytes are shown immediately and 100 percent changes to validation, not completion',async()=>{
  let xhr;
  global.fetch=jest.fn(async()=>response({state:'IDLE'}));
  global.XMLHttpRequest=jest.fn(()=>{xhr={open:jest.fn(),setRequestHeader:jest.fn(),upload:{},send:jest.fn()};return xhr;});
  const {result}=renderHook(()=>useVoicePackInstall('/app',jest.fn(),jest.fn()));
  let install;
  act(()=>{install=result.current.install('',new File(['sample'],'pack.zip'));});
  act(()=>xhr.upload.onprogress({loaded:3,total:6,lengthComputable:true}));
  expect(result.current.operation).toMatchObject({state:'RUNNING',stage:'uploading',done:3,total:6});
  act(()=>xhr.upload.onload());
  expect(result.current.operation).toMatchObject({state:'RUNNING',stage:'checking',total:null});
  xhr.status=200;xhr.responseText=JSON.stringify({imported:{key:'ready@1'}});
  await act(async()=>{xhr.onload();await install;});
  expect(result.current.operation.state).toBe('COMPLETE');
});

test('voice selection reports pending and errors, and a later retry succeeds',async()=>{
  global.fetch=jest.fn(async()=>response({state:'IDLE'}));
  const {result}=renderHook(()=>useVoicePackInstall('/app',jest.fn(),jest.fn()));
  let reject,change;
  act(()=>{change=result.current.trackSelection(()=>new Promise((_,no)=>{reject=no;}));});
  expect(result.current.operation).toMatchObject({kind:'selection',state:'RUNNING',stage:'applying'});
  await act(async()=>{reject(Error('해시 오류'));await expect(change).rejects.toThrow('해시 오류');});
  expect(result.current.operation).toMatchObject({state:'FAILED',error:'해시 오류'});
  await act(()=>result.current.trackSelection(async()=>({settings:{}})));
  expect(result.current.operation.state).toBe('COMPLETE');
});

test('a fast selection keeps its result when the server still holds an older install status',async()=>{
  global.fetch=jest.fn(async()=>response({id:'old-install',kind:'install',state:'COMPLETE',imported:{key:'old@1'}}));
  const reload=jest.fn();
  const {result}=renderHook(()=>useVoicePackInstall('/app',reload,jest.fn()));
  await waitFor(()=>expect(result.current.operation?.id).toBe('old-install'));
  const previousCalls=global.fetch.mock.calls.length;
  await act(()=>result.current.trackSelection(async()=>({settings:{voice:'Fenrir'}})));
  await waitFor(()=>expect(global.fetch.mock.calls.length).toBeGreaterThan(previousCalls));
  expect(result.current.operation).toMatchObject({kind:'selection',state:'COMPLETE'});
});
