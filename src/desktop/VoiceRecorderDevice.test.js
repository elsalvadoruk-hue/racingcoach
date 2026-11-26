import { VoiceRecorder } from '../voiceAudio';

test('echo-off comparison changes only echo cancellation and rejects unknown profiles before opening mic', async () => {
  const getUserMedia = jest.fn(async () => { throw new Error('capture-requested'); });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  const recorder = new VoiceRecorder();
  await expect(recorder.open('usb-device', 'echo_off')).rejects.toThrow('capture-requested');
  expect(getUserMedia).toHaveBeenLastCalledWith({ audio: {
    channelCount: 1, deviceId: { exact: 'usb-device' },
    echoCancellation: false, noiseSuppression: true, autoGainControl: true,
  } });
  await expect(recorder.open('', 'raw')).rejects.toThrow('capture-requested');
  expect(getUserMedia.mock.calls.at(-1)[0].audio).toEqual({ channelCount: 1,
    echoCancellation: false, noiseSuppression: false, autoGainControl: false });
  await expect(recorder.open('', 'unknown')).rejects.toThrow();
  expect(getUserMedia).toHaveBeenCalledTimes(2);
});

test('the selected input is requested exactly while default keeps browser device selection', async () => {
  const getUserMedia = jest.fn(async () => { throw new Error('test-stop-before-audio-context'); });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  const recorder = new VoiceRecorder();
  await expect(recorder.open('usb-device')).rejects.toThrow('test-stop');
  expect(getUserMedia).toHaveBeenLastCalledWith({ audio: expect.objectContaining({ deviceId: { exact: 'usb-device' }, channelCount: 1 }) });
  await expect(recorder.open('default')).rejects.toThrow('test-stop');
  expect(getUserMedia.mock.calls.at(-1)[0].audio).not.toHaveProperty('deviceId');
  expect(getUserMedia.mock.calls.at(-1)[0].audio).toEqual({ channelCount: 1,
    echoCancellation: true, noiseSuppression: true, autoGainControl: true });
});

test('prepared recorder suspends audio processing until a press and stops it after release', async () => {
  const track = { enabled: true, stop: jest.fn(), getSettings: () => ({
    echoCancellation: true, noiseSuppression: true, sampleRate: 48000, deviceId: 'private-device-id',
  }) };
  const stream = { active: true, getAudioTracks: () => [track], getTracks: () => [track] };
  const context = { sampleRate: 16000, audioWorklet: { addModule: jest.fn(async () => {}) },
    resume: jest.fn(async () => {}), suspend: jest.fn(async () => {}), close: jest.fn(async () => {}),
    createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
    createGain: () => ({ gain: {}, connect() {} }) };
  const port = { postMessage: jest.fn(message => {
    if (message.type === 'finish') queueMicrotask(() => {
      port.onmessage({ data: new Float32Array(128).fill(.5) });
      port.onmessage({ data: { type: 'stopped', id: message.id } });
    });
  }) };
  const oldContext = window.AudioContext, oldNode = global.AudioWorkletNode;
  window.AudioContext = jest.fn(() => context);
  global.AudioWorkletNode = jest.fn(() => ({ port, connect() {}, disconnect() {} }));
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => stream } });
  const recorder = new VoiceRecorder();
  try {
    const input = await recorder.open('', 'echo_off');
    expect(input.processing.profile).toBe('echo_off');
    expect(input.processing.requested.echoCancellation).toBe(false);
    // Report the browser's actual value, even when it differs from the request.
    expect(input.processing.effective.echoCancellation).toBe(true);
    expect(input.processing.effective.autoGainControl).toBeNull();
    expect(input.processing.effective.sampleRate).toBe(48000);
    expect(input.processing.effective).not.toHaveProperty('deviceId');
    expect(track.enabled).toBe(false);expect(context.suspend).toHaveBeenCalledTimes(1);
    await recorder.start();
    expect(track.enabled).toBe(true);
    port.onmessage({ data: new Float32Array(8000) });
    const encoded = await recorder.finish();
    // The queued final worklet buffer must be retained before its stop ack.
    expect(atob(encoded).length).toBe(44 + (8000 + 128) * 2);
    expect(track.enabled).toBe(false);expect(context.suspend).toHaveBeenCalledTimes(2);
  } finally { recorder.close();window.AudioContext=oldContext;global.AudioWorkletNode=oldNode; }
  expect(track.stop).toHaveBeenCalled();
});

function finishingRecorder(seconds=.5) {
  const recorder=new VoiceRecorder(),track={enabled:true,stop:jest.fn()};
  recorder.recording=true;recorder.chunks=[new Float32Array(seconds*16000)];
  recorder.stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
  recorder.context={sampleRate:16000,suspend:jest.fn(async()=>{}),close:jest.fn(async()=>{})};
  recorder.node={port:{postMessage:jest.fn()},disconnect:jest.fn()};
  return {recorder,track};
}

test('local tail retains sound until acknowledged and a second release shares the same finish',async()=>{
  jest.useFakeTimers();
  const {recorder,track}=finishingRecorder();
  try {
    const pending=recorder.finish({tailMs:180,maxSeconds:10});
    expect(recorder.finish()).toBe(pending);
    jest.advanceTimersByTime(179);expect(recorder.node.port.postMessage).not.toHaveBeenCalled();
    expect(track.enabled).toBe(true);recorder.chunks.push(new Float32Array(1600));
    jest.advanceTimersByTime(1);expect(recorder.node.port.postMessage).toHaveBeenCalledTimes(1);
    recorder.completeFinish();expect(atob(await pending).length).toBe(44+9600*2);
    expect(track.enabled).toBe(false);
  }finally{recorder.close();jest.useRealTimers();}
});

test.each(['cancel','timeout'])('finish %s discards audio and stops capture',async(kind)=>{
  jest.useFakeTimers();const {recorder,track}=finishingRecorder();
  try {
    const pending=recorder.finish({tailMs:180});
    const rejected=expect(pending).rejects.toThrow(kind==='cancel'?'취소':'종료를 확인');
    if(kind==='cancel')recorder.close();else jest.advanceTimersByTime(930);
    await rejected;expect(recorder.chunks).toEqual([]);expect(track.enabled).toBe(false);
  }finally{recorder.close();jest.useRealTimers();}
});

test('Google retains its long question limit while local capture is bounded at ten seconds',async()=>{
  const {recorder}=finishingRecorder(44);
  const pending=recorder.finish();recorder.completeFinish();
  expect(atob(await pending).length).toBe(44+44*16000*2);recorder.close();
  const local=finishingRecorder(10.1).recorder;
  const rejected=expect(local.finish({maxSeconds:10})).rejects.toThrow('10초');
  local.completeFinish();await rejected;local.close();
});
