import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import LiveCoach from '../components/LiveCoach';
import { VoiceRecorder } from '../voiceAudio';

const mockOpen = jest.fn(), mockClose = jest.fn();
const mockStart = jest.fn(), mockFinish = jest.fn();
const mockStop = jest.fn();
const mockDelivery = jest.fn();
const mockAudio = { stop: mockStop, stopNormal: mockStop, play: jest.fn(), refresh: jest.fn(),
  isPriorityBlocked: jest.fn(() => false), outputId: '', devices: [], volume: 1, muted: false };
const mockSpotter = { enabled: false, view: null, settings: {}, toggle: jest.fn() };
jest.mock('../components/SessionLogAutoSend',()=>({useSessionLogAuto:()=>({state:{enabled:true,available:true}})}));
jest.mock('../useAudioOutput', () => ({ __esModule: true, default: () => mockAudio }));
jest.mock('../useSpotter', () => ({ __esModule: true, default: () => mockSpotter }));
jest.mock('../useRadioDiagnostics', () => ({ __esModule: true, default: () => mockDelivery }));
jest.mock('../voiceAudio', () => ({ ...jest.requireActual('../voiceAudio'), VoiceRecorder: jest.fn().mockImplementation(() => ({ open: mockOpen, close: mockClose })) }));

beforeEach(() => {
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:jest.fn(async()=>({getTracks:()=>[{stop:jest.fn()}]}))}});
  mockDelivery.mockClear();
  localStorage.clear(); mockOpen.mockReset().mockResolvedValue(undefined); mockClose.mockReset();
  mockStart.mockReset().mockResolvedValue(undefined); mockFinish.mockReset().mockReturnValue('wav-data');
  VoiceRecorder.mockImplementation(() => ({ open: mockOpen, close: mockClose, start: mockStart, finish: mockFinish }));
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ speech: {}, notes: [] }) }));
  global.WebSocket = class { close() {} };
});

test.each([false,true])('Google finish awaits one recording and cancels on disarm=%s',async(disarm)=>{
  let host,complete,pending;
  mockFinish.mockImplementation(()=>new Promise(resolve=>{complete=resolve;}));
  global.fetch=jest.fn(async url=>({ok:true,json:async()=>url.endsWith('/captures')?{capture_id:'cloud'}:
    url.endsWith('/utterances')?{note:{id:'cloud',context:{},text:'질문'}}:{speech:{},notes:[]}}));
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value=>{host=value;return null;}}</LiveCoach>);
  await act(async()=>host.prepare());await act(async()=>host.start());
  act(()=>{pending=host.finish();});
  await act(async()=>host.finish());expect(mockFinish).toHaveBeenCalledTimes(1);
  if(disarm)await act(async()=>host.prepare());
  await act(async()=>{complete('long-wav');await pending;});
  expect(global.fetch.mock.calls.filter(([url])=>url.endsWith('/api/live-coach/utterances'))).toHaveLength(disarm?0:1);
  expect(host.phase).toBe('idle');
});

test('local AEC defaults off, is locked during capture and persists independently of Google', async () => {
  let host;
  const renderHost = () => render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host = value; return null; }}</LiveCoach>);
  const view = renderHost();
  expect(host.localRadio.echoCancellation).toBe(false);
  await act(async () => host.localRadio.prepare());
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalled();
  await act(async () => host.localRadio.startTest());
  expect(mockOpen).toHaveBeenLastCalledWith(host.inputDevice, 'echo_off');
  act(() => host.localRadio.setEchoCancellation(true));
  expect(host.localRadio.echoCancellation).toBe(false);
  await act(async () => host.localRadio.finish());
  const loads = global.fetch.mock.calls.filter(([url]) => url.endsWith('/local-speech/activate')).length;
  act(() => host.localRadio.setEchoCancellation(true));
  expect(localStorage.getItem('iracing.local-speech.echo-cancellation.v1')).toBe('true');
  expect(host.localRadio.enabled).toBe(true);
  expect(global.fetch.mock.calls.filter(([url]) => url.endsWith('/local-speech/activate'))).toHaveLength(loads);
  await act(async () => host.localRadio.startTest());
  expect(mockOpen).toHaveBeenLastCalledWith(host.inputDevice, 'standard');
  act(() => host.localRadio.cancelTest());
  await act(async () => host.localRadio.stop());
  await act(async () => host.prepare());
  expect(mockOpen).toHaveBeenLastCalledWith(host.inputDevice);
  view.unmount();
  await act(async () => { renderHost(); });
  expect(host.localRadio.echoCancellation).toBe(true);
});

test('local microphone sends audio only to local endpoint and keeps transcript in the history', async () => {
  let host;
  const note = { id: 'question', created_at: 1, text: '연료 얼마 남았어', reply: '지금 연료는 24리터입니다.',
    context: { snapshot: { run_id: 'run-one' }, local_question: { result: 'prepared', topic: 'fuel', total_ms: 100 } } };
  global.fetch = jest.fn(async url => ({ ok: true, json: async () =>
    url.endsWith('/status') ? { connected: true, game: 'iracing', snapshot: { run_id: 'run-one' }, speech: {} } :
    url.endsWith('/captures') ? { capture_id: 'question' } :
    url.endsWith('/local-speech/utterances') ? { note, audio_base64: 'cached-answer', audio_parts: ['cached-answer'] } : {} }));
  mockAudio.play.mockClear().mockResolvedValue(true);
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host = value; return null; }}</LiveCoach>);
  await act(async () => host.localRadio.prepare());
  expect(host.armed).toBe(false);
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalled();
  await act(async () => host.localRadio.start());
  expect(host.localRadio.phase).toBe('recording');
  await act(async () => host.localRadio.finish());
  expect(host.notes[0].text).toBe('연료 얼마 남았어');
  expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/api/live-coach/utterances'))).toBe(false);
  const call = global.fetch.mock.calls.find(([url]) => url.endsWith('/local-speech/utterances'));
  expect(JSON.parse(call[1].body)).toMatchObject({ audio_base64: 'wav-data', model: 'sensevoice' });
  const options = mockAudio.play.mock.calls.at(-1)[2];
  expect(options.valid()).toBe(true);
  await act(async () => host.localRadio.stop());
  expect(options.valid()).toBe(false);
  expect(host.replay).toBeNull();
});

test('the production host opens the selected microphone and disarms it before a device change', async () => {
  localStorage.setItem('iracing.voice.inputDevice', 'usb-mic');
  let host;
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host = value; return null; }}</LiveCoach>);
  await act(async () => { await host.prepare(); });
  expect(mockOpen).toHaveBeenCalledWith('usb-mic'); expect(host.armed).toBe(true);
  act(() => host.changeInputDevice('second-mic'));
  expect(host.armed).toBe(false); expect(mockClose).toHaveBeenCalled();
  expect(localStorage.getItem('iracing.voice.inputDevice.iracing')).toBe('second-mic');
  await act(async () => { await host.prepare(); });
  expect(mockOpen).toHaveBeenLastCalledWith('second-mic');
});

test('local microphone test works disconnected and never creates a question or Google request', async () => {
  let host;
  global.fetch = jest.fn(async url => ({ ok: true, json: async () =>
    url.endsWith('/local-speech/test') ? { text: '기름 얼마 남았어', topic: 'fuel', topic_name: '현재 연료', total_ms: 123 } :
    url.endsWith('/status') ? { connected: false, speech: {} } : {} }));
  mockAudio.play.mockClear();
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host = value; return null; }}</LiveCoach>);
  await act(async () => host.localRadio.prepare());
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  await act(async () => host.localRadio.startTest());
  await act(async () => host.localRadio.finish());
  expect(host.localRadio.testResult.text).toBe('기름 얼마 남았어');
  expect(host.notes).toHaveLength(0);
  expect(mockAudio.play).not.toHaveBeenCalled();
  expect(global.fetch.mock.calls.some(([url]) => url.endsWith('/captures') || url.endsWith('/utterances'))).toBe(false);
  expect(host.armed).toBe(false);
});

test('a delayed capture context cannot keep a local microphone recording beyond its limit', async () => {
  jest.useFakeTimers();
  let host, resolveCapture, pending;
  global.fetch = jest.fn(async url => url.endsWith('/captures') ? new Promise(resolve => { resolveCapture=resolve; }) :
    { ok:true, json:async()=>({}) });
  const view = render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host=value;return null; }}</LiveCoach>);
  try {
    await act(async()=>host.localRadio.prepare());
    act(()=>{pending=host.localRadio.start();});
    await act(async()=>{await Promise.resolve();await Promise.resolve();});
    await act(async()=>jest.advanceTimersByTime(9500));
    expect(host.localRadio.phase).toBe('idle');
    expect(host.localRadio.error).toContain('지연');
    resolveCapture({ok:true,json:async()=>({capture_id:'late'})});
    await act(async()=>pending);
    expect(global.fetch.mock.calls.some(([url])=>url.endsWith('/local-speech/utterances'))).toBe(false);
  } finally {view.unmount();jest.useRealTimers();}
});

test('a late microphone permission response cannot rearm a device after another input was selected', async () => {
  let finishOpen, host, pending;
  mockOpen.mockImplementation(() => new Promise(resolve => { finishOpen = resolve; }));
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value => { host = value; return null; }}</LiveCoach>);
  act(() => { pending = host.prepare(); });
  await waitFor(() => expect(host.phase).toBe('arming'));
  act(() => host.changeInputDevice('different-mic'));
  await act(async () => { finishOpen(); await pending; });
  expect(host.armed).toBe(false); expect(host.inputDevice).toBe('different-mic'); expect(host.phase).toBe('idle');
});

test('local partial report logs only the sentences with playable audio', async () => {
  let host;
  const note = {id:'partial-report',created_at:1,reply:'현재 클래스 9위입니다. 5분 남았습니다.',
    context:{snapshot:{run_id:'run-one'},local_question:{result:'prepared_partial'}}};
  global.fetch = jest.fn(async url => ({ok:true,json:async()=>
    url.endsWith('/status') ? {connected:true,game:'iracing',snapshot:{run_id:'run-one'},speech:{}} :
    url.endsWith('/captures') ? {capture_id:'partial-report'} :
    url.endsWith('/local-speech/utterances') ? {note,audio_parts:['clip'],spoken_text:'현재 클래스 9위입니다.'} : {}}));
  mockAudio.play.mockClear().mockResolvedValue(true);
  render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value=>{host=value;return null;}}</LiveCoach>);
  await act(async()=>host.localRadio.prepare());
  await act(async()=>host.localRadio.start());
  await act(async()=>host.localRadio.finish());
  const options = mockAudio.play.mock.calls.at(-1)[2];
  await act(async()=>options.onStatus('completed'));
  expect(mockDelivery).toHaveBeenCalledWith(expect.objectContaining({event_id:'partial-report',text:'현재 클래스 9위입니다.'}), 'completed', undefined);
});

test('pit command preference is passed only to real local requests and is locked during capture', async()=>{
 let host;
 global.fetch=jest.fn(async url=>({ok:true,json:async()=>
  url.endsWith('/status')?{connected:true,game:'iracing',snapshot:{run_id:'run-one'},speech:{}}:
  url.endsWith('/captures')?{capture_id:'pit'}:
  url.endsWith('/local-speech/utterances')?{note:{id:'pit',context:{snapshot:{run_id:'run-one'}}},command_result:{result:'command_applied',message:'반영 확인'}}:{}}));
 render(<LiveCoach apiBase="http://localhost:24158" monitor visible={false}>{value=>{host=value;return null;}}</LiveCoach>);
 expect(host.localRadio.pitCommands).toBe(false);
 act(()=>host.localRadio.setPitCommands(true));
 expect(localStorage.getItem('iracing.local-speech.pit-commands.v1')).toBe('true');
 await act(async()=>host.localRadio.prepare());
 await act(async()=>host.localRadio.start());
 act(()=>host.localRadio.setPitCommands(false));
 expect(host.localRadio.pitCommands).toBe(true);
 await act(async()=>host.localRadio.finish());
 const request=global.fetch.mock.calls.find(([url])=>url.endsWith('/local-speech/utterances'));
 expect(JSON.parse(request[1].body).allow_pit_commands).toBe(true);
 expect(host.localRadio.commandResult.message).toBe('반영 확인');
 await act(async()=>host.localRadio.startTest());
 await act(async()=>host.localRadio.finish());
 const testRequest=global.fetch.mock.calls.find(([url])=>url.endsWith('/local-speech/test'));
 expect(JSON.parse(testRequest[1].body).allow_pit_commands).toBeUndefined();
 await act(async()=>host.localRadio.stop());
 expect(host.localRadio.commandResult).toBeNull();
});
