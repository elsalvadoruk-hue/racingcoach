import * as radioEffect from './radioEffect';

// A stand-in for Web Audio that records what was built and connected.
function fakeContext({ state = 'running', sink = true } = {}) {
  const made = [];
  const node = (kind) => {
    const item = { kind, connected: [], disconnected: false, started: false, stopped: false,
      frequency: { value: 0 }, Q: { value: 0 }, gain: { value: 1, setValueAtTime() {}, exponentialRampToValueAtTime() {} },
      threshold: { value: 0 }, ratio: { value: 0 }, attack: { value: 0 }, release: { value: 0 },
      connect(target) { item.connected.push(target); return target; }, disconnect() { item.disconnected = true; },
      start() { item.started = true; }, stop() { item.stopped = true; } };
    made.push(item);
    return item;
  };
  class Context {
    constructor() { this.state = state; this.sampleRate = 8000; this.currentTime = 0; this.destination = { kind: 'destination' }; Context.last = this; }
    resume() { this.state = 'running'; return Promise.resolve(); }
    createMediaElementSource(audio) { const item = node('source'); item.audio = audio; return item; }
    createBiquadFilter() { return node('filter'); }
    createWaveShaper() { return node('shaper'); }
    createDynamicsCompressor() { return node('compressor'); }
    createGain() { return node('gain'); }
    createBufferSource() { return node('noise'); }
    createBuffer(channels, length) { return { sampleRate: this.sampleRate, getChannelData: () => new Float32Array(length) }; }
  }
  if (sink) Context.prototype.setSinkId = function setSinkId(id) { this.sink = id; return Promise.resolve(); };
  return { Context, made };
}
const settings = (patch = {}) => radioEffect.cleanSettings({ level: 'medium', ...patch });
afterEach(() => { radioEffect.reset(); delete window.AudioContext; localStorage.clear(); });

test('settings are off by default and anything odd is brought back into range', () => {
  expect(radioEffect.savedSettings()).toEqual({ level: 'off', squelch: true, hiss: true, pops: 'normal', voice: 100, noise: 100 });
  expect(radioEffect.cleanSettings({ level: 'louder', voice: 900, noise: -5, squelch: false })).toEqual({ level: 'off', squelch: false, hiss: true, pops: 'normal', voice: 150, noise: 0 });
  radioEffect.storeSettings(settings({ level: 'harder', noise: 250 }));
  expect(radioEffect.savedSettings()).toMatchObject({ level: 'harder', noise: 250 });
});

test('without Web Audio, when off, or before the context runs, the clip is left alone', () => {
  expect(radioEffect.attach({}, settings())).toBeNull();                       // no AudioContext in this browser
  const { Context } = fakeContext({ state: 'suspended' });
  window.AudioContext = Context;
  Context.prototype.resume = () => new Promise(() => {});                      // never allowed to start
  radioEffect.warm(settings());
  expect(radioEffect.ready(settings())).toBe(false);
  expect(radioEffect.attach({}, settings())).toBeNull();
  radioEffect.warm(settings({ level: 'off' }));
  expect(radioEffect.attach({}, settings({ level: 'off' }))).toBeNull();
});

test('a clip is routed through the filters, with hiss and squelch, and let go when it ends', () => {
  const { Context, made } = fakeContext();
  window.AudioContext = Context;
  radioEffect.warm(settings());
  const audio = {};
  const effect = radioEffect.attach(audio, settings(), { volume: 0.5, muted: false });
  expect(effect).not.toBeNull();
  const source = made.find((item) => item.kind === 'source');
  expect(source.audio).toBe(audio);
  expect(made.filter((item) => item.kind === 'filter')).toHaveLength(3);        // high-pass, low-pass, the presence peak
  effect.start(true);
  const noises = () => made.filter((item) => item.kind === 'noise');
  expect(noises()).toHaveLength(2);                                             // the opening click and the hiss
  expect(noises().every((item) => item.started)).toBe(true);
  effect.release(true);
  expect(noises()).toHaveLength(3);                                             // the closing click
  expect(source.disconnected).toBe(true);
  effect.release(true);                                                         // letting go twice does nothing more
  expect(noises()).toHaveLength(3);
});

test('a stronger level is not a louder voice: each one is brought back to the plain loudness', () => {
  const { Context, made } = fakeContext();
  window.AudioContext = Context;
  const voiceGain = (patch) => {
    radioEffect.warm(settings(patch));
    radioEffect.attach({}, settings(patch), { volume: 1 });
    return made.filter((item) => item.kind === 'gain').pop().gain.value;
  };
  const gains = ['light', 'medium', 'strong', 'harder'].map((level) => voiceGain({ level }));
  expect(gains).toEqual([...gains].sort((a, b) => b - a));                       // the harder the effect, the lower the gain
  expect(gains[3]).toBeLessThan(gains[0] * 0.5);
  expect(voiceGain({ level: 'medium', voice: 50 })).toBeCloseTo(gains[1] / 2);   // the voice slider still scales it
});

test('microphone pops come from a worklet ahead of the radio filters, once it has loaded, in two strengths or off', async () => {
  const { Context, made } = fakeContext();
  const loaded = [];
  Context.prototype.audioWorklet = { addModule: (url) => { loaded.push(url); return Promise.resolve(); } };
  window.AudioContext = Context;
  const built = [];
  window.AudioWorkletNode = function AudioWorkletNode(context, name, options) {
    const item = { kind: 'pops', name, options, connected: [], connect(target) { item.connected.push(target); return target; }, disconnect() {} };
    built.push(item); made.push(item); return item;
  };
  const lowCut = () => made.filter((item) => item.kind === 'filter')[made.filter((item) => item.kind === 'filter').length - 3].frequency.value;
  radioEffect.warm(settings({ level: 'harder' }));
  expect(radioEffect.attach({}, settings({ level: 'harder' }), { volume: 1 })).not.toBeNull();   // not loaded yet: no pops
  expect(built).toHaveLength(0);
  expect(lowCut()).toBe(520);                                                    // and the level's own low cut
  await Promise.resolve(); await Promise.resolve();
  radioEffect.warm(settings());
  expect(loaded).toEqual(['/radio-pop-worklet.js']);                             // loaded once
  radioEffect.attach({}, settings({ level: 'harder' }), { volume: 1 });
  expect(built).toHaveLength(1);
  expect(built[0].name).toBe('radio-pop');
  expect(built[0].options.processorOptions).toEqual({ amp: 2, clip: 1.2 });      // the default, "3-2"
  expect(lowCut()).toBe(150);                                                    // the thump needs the lows
  const source = made.filter((item) => item.kind === 'source').pop();
  expect(source.connected[0]).toBe(built[0]);                                    // the voice reaches the pops first
  expect(built[0].connected[0].kind).toBe('filter');                             // and then the radio band
  radioEffect.attach({}, settings({ level: 'harder', pops: 'strong' }), { volume: 1 });
  expect(built[1].options.processorOptions).toEqual({ amp: 3, clip: 1.05 });
  radioEffect.attach({}, settings({ level: 'harder', pops: 'off' }), { volume: 1 });
  expect(built).toHaveLength(2);
  expect(lowCut()).toBe(520);
  expect(radioEffect.cleanSettings({ pops: false }).pops).toBe('off');           // saved by the version with a check box
  expect(radioEffect.cleanSettings({ pops: true }).pops).toBe('normal');
  delete window.AudioWorkletNode;
});

test('muted or with the noise turned down, only the voice is filtered', () => {
  const { Context, made } = fakeContext();
  window.AudioContext = Context;
  radioEffect.warm(settings());
  const quiet = radioEffect.attach({}, settings({ noise: 0 }), { volume: 1 });
  quiet.start(true); quiet.release(true);
  const muted = radioEffect.attach({}, settings(), { volume: 1, muted: true });
  muted.start(true); muted.release(true);
  expect(made.filter((item) => item.kind === 'noise')).toHaveLength(0);
  const cut = radioEffect.attach({}, settings(), { volume: 1 });
  cut.start(false); cut.release(false);                                         // a later sentence that was interrupted: hiss only
  expect(made.filter((item) => item.kind === 'noise')).toHaveLength(1);
});

test('a chosen output device is only used when the context can be sent there', async () => {
  const able = fakeContext();
  window.AudioContext = able.Context;
  radioEffect.warm(settings(), 'headset');
  expect(radioEffect.ready(settings(), 'headset')).toBe(false);                 // not confirmed yet
  await Promise.resolve();
  expect(radioEffect.ready(settings(), 'headset')).toBe(true);
  expect(radioEffect.ready(settings(), '')).toBe(false);                        // and no longer on the default device
  radioEffect.reset();
  const unable = fakeContext({ sink: false });
  window.AudioContext = unable.Context;
  radioEffect.warm(settings(), 'headset');
  await Promise.resolve();
  expect(radioEffect.attach({}, settings(), { sinkId: 'headset' })).toBeNull(); // the clip keeps its own device and plays plain
});
