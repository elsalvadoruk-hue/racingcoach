// A radio sound laid over the voices while they play. The clips themselves are untouched:
// the audio element (driver's speed, pitch kept) is routed through a few filters, so the effect can be
// switched off, turned up or down, and costs nothing to produce.
//
// Values were chosen by ear on artifacts/voice-pilot/radio-effect.html (scripts/voice_pilot_radio.py).
// Whenever the effect cannot be applied safely (no Web Audio, the context not running yet, a chosen
// output device the context cannot be sent to) the clip plays exactly as it always did.

const KEY = 'iracing.voice.radioEffect';
export const EFFECT_LEVELS = [['off', '끔'], ['light', '약하게'], ['medium', '중간'], ['strong', '강하게'], ['harder', '더 강하게']];
// Band of a radio voice, how hard it is driven, and how much hiss rides under it.
// makeup brings the voice back to the loudness it has with the effect off: the presence peak, the
// clipping and the compressor's own makeup made each level louder (+3.5, +5.8, +8.2, +10.4 dB).
// Measured on 32 real clips rendered offline in Chromium, K-weighted and gated as in BS.1770;
// with these values every level is within 0.1 dB of the plain clip (median).
// bassMakeup is the same for the radio that keeps its lows down to POP_LOW_CUT (measured the same way).
const PRESETS = {
  light: { low: 220, high: 4200, mid: 3, drive: 1.5, hiss: 0.004, makeup: 0.667, bassMakeup: 0.624 },
  medium: { low: 320, high: 3400, mid: 6, drive: 3.0, hiss: 0.010, makeup: 0.511, bassMakeup: 0.442 },
  strong: { low: 420, high: 2800, mid: 9, drive: 6.0, hiss: 0.020, makeup: 0.388, bassMakeup: 0.329 },
  harder: { low: 520, high: 2400, mid: 12, drive: 11.0, hiss: 0.032, makeup: 0.302, bassMakeup: 0.262 },
};
// Microphone pops on ㅂ and ㅍ (public/radio-pop-worklet.js): the push of air and where the microphone
// flattens, against the voice's peak. Chosen by ear (2026-10-14) on artifacts/voice-pilot/radio-pops.html:
// 'normal' was "3-2", 'strong' was "3". Both were heard on a radio that keeps its lows down to 150 Hz,
// and the thump is gone with the higher cuts, so while pops are on every level cuts at POP_LOW_CUT.
export const POP_MODES = [['normal', '기본'], ['strong', '강하게'], ['off', '끄기']];
const POPS = { normal: { amp: 2, clip: 1.2 }, strong: { amp: 3, clip: 1.05 } };
const POP_LOW_CUT = 150;
const DEFAULTS = { level: 'off', squelch: true, hiss: true, pops: 'normal', voice: 100, noise: 100 };
const within = (value, low, high, fallback) => (typeof value === 'number' && Number.isFinite(value) ? Math.max(low, Math.min(high, value)) : fallback);

export function cleanSettings(saved) {
  const value = saved && typeof saved === 'object' ? saved : {};
  return {
    level: value.level in PRESETS ? value.level : 'off',
    squelch: value.squelch !== false, hiss: value.hiss !== false,
    // Saved before there were modes: on/off.
    pops: value.pops === false || value.pops === 'off' ? 'off' : value.pops === 'strong' ? 'strong' : 'normal',
    voice: within(value.voice, 20, 150, DEFAULTS.voice), noise: within(value.noise, 0, 400, DEFAULTS.noise),
  };
}
export function savedSettings() {
  try { return cleanSettings(JSON.parse(localStorage.getItem(KEY))); } catch { return { ...DEFAULTS }; }
}
export function storeSettings(settings) {
  try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch { /* The effect still applies for this page. */ }
}

let context = null, contextSink = '', sinkFlight = null, noiseBuffer = null, popsReady = false, popsLoading = false;
const Context = () => (typeof window !== 'undefined' ? window.AudioContext || window.webkitAudioContext : null);
const lostListeners = new Set();

// Told when the context lost its output device, where the browser reports it (an "error" event).
export function onLost(listener) {
  lostListeners.add(listener);
  return () => { lostListeners.delete(listener); };
}

// A context whose device failed is let go: the next clip warms a fresh one and, until it runs, plays plain.
function open() {
  const made = new (Context())();
  made.addEventListener?.('error', () => {
    if (context !== made) return;
    context = null; contextSink = ''; sinkFlight = null; popsReady = false; popsLoading = false;
    made.close?.().catch?.(() => {});
    lostListeners.forEach((listener) => listener());
  });
  return made;
}

// Called from clicks (enabling the spotter, the test sound, changing the setting): a context may only start then.
export function warm(settings, sinkId = '') {
  if (!settings || !Context()) return;
  try {
    context = context || open();
    // With autoplay blocked, resume() can remain pending until a future gesture. Let the native
    // player report that restriction instead of occupying the radio queue indefinitely.
    const resuming = context.state === 'suspended'
      ? Promise.race([context.resume().catch(() => {}), new Promise(resolve => setTimeout(resolve, 100))]) : undefined;
    // The pops run in a worklet; until it has loaded (or where there is none) clips play without them.
    if (settings.level !== 'off' && !popsReady && !popsLoading && context.audioWorklet) {
      popsLoading = true;
      const loading = context;
      loading.audioWorklet.addModule(`${process.env.PUBLIC_URL || ''}/radio-pop-worklet.js`)
        .then(() => { if (context === loading) popsReady = true; }, () => {}).finally(() => { if (context === loading) popsLoading = false; });
    }
    if (sinkFlight) return Promise.all([resuming, sinkFlight.then(() => warm(settings, sinkId))]);
    if (sinkId !== contextSink && typeof context.setSinkId === 'function') {
      const wanted = sinkId, routing = context;
      const flight = routing.setSinkId(wanted).then(() => { if (context === routing) contextSink = wanted; }, () => {})
        .finally(() => { if (sinkFlight === flight) sinkFlight = null; });
      sinkFlight = flight;
      return Promise.all([resuming, flight]);
    }
    return resuming;
  } catch { context = null; }
}

// True only when a clip routed through the context is certain to be heard on the chosen device.
export function ready(settings, sinkId = '') {
  return !!settings && !!context && !sinkFlight && context.state === 'running' && contextSink === sinkId;
}

function curve(drive) {                         // soft clipping: the grit of an overdriven handset
  const samples = 1024, shape = new Float32Array(samples);
  for (let i = 0; i < samples; i += 1) { const x = (i * 2) / (samples - 1) - 1; shape[i] = Math.tanh(drive * x) / Math.tanh(drive); }
  return shape;
}
function noise() {
  if (!noiseBuffer || noiseBuffer.sampleRate !== context.sampleRate) {
    noiseBuffer = context.createBuffer(1, context.sampleRate * 2, context.sampleRate);
    const data = noiseBuffer.getChannelData(0);
    for (let i = 0; i < data.length; i += 1) data[i] = Math.random() * 2 - 1;
  }
  return noiseBuffer;
}
function burst(seconds, level) {                 // the squelch: a short rush of band-limited noise
  if (!context || level <= 0) return;
  const at = context.currentTime, source = context.createBufferSource();
  source.buffer = noise();
  const band = context.createBiquadFilter(); band.type = 'bandpass'; band.frequency.value = 2200; band.Q.value = 0.8;
  const gain = context.createGain();
  gain.gain.setValueAtTime(level, at); gain.gain.exponentialRampToValueAtTime(0.0005, at + seconds);
  source.connect(band).connect(gain).connect(context.destination);
  source.start(at, Math.random()); source.stop(at + seconds);
}

// Routes one audio element through the effect. Returns what the player calls as the clip goes on,
// or null when the clip should simply play as it is.
export function attach(audio, settings, { sinkId = '', volume = 1, muted = false } = {}) {
  if (!ready(settings, sinkId)) return null;
  const preset = PRESETS[settings.level];
  let source;
  try {
    // Always route through a gain when Web Audio is ready, including with radio effects off.
    // HTMLMediaElement.volume stays within 0..1; only the excess above 100% goes here.
    const boost = context.createGain(); boost.gain.value = Math.max(1, volume);
    if (!preset) {
      source = context.createMediaElementSource(audio);
      source.connect(boost).connect(context.destination);
      return { boosted: true, start() {}, update(next) { boost.gain.value = Math.max(1, next.volume); },
        release() { source.disconnect(); boost.disconnect(); } };
    }
    const pops = POPS[settings.pops] && popsReady ? POPS[settings.pops] : null;
    const high = context.createBiquadFilter(); high.type = 'highpass'; high.frequency.value = pops ? POP_LOW_CUT : preset.low; high.Q.value = 0.7;
    const low = context.createBiquadFilter(); low.type = 'lowpass'; low.frequency.value = preset.high; low.Q.value = 0.7;
    const mid = context.createBiquadFilter(); mid.type = 'peaking'; mid.frequency.value = 1800; mid.Q.value = 1; mid.gain.value = preset.mid;
    const shaper = context.createWaveShaper(); shaper.curve = curve(preset.drive); shaper.oversample = '2x';
    const squeeze = context.createDynamicsCompressor();
    squeeze.threshold.value = -24; squeeze.ratio.value = 8; squeeze.attack.value = 0.003; squeeze.release.value = 0.12;
    const level = context.createGain(); level.gain.value = 0.9 * (pops ? preset.bassMakeup : preset.makeup) * settings.voice / 100;
    source = context.createMediaElementSource(audio);
    const nodes = [source, high, low, mid, shaper, squeeze, level, boost];
    let input = source;
    if (pops) {
      // Ahead of the filters: the microphone overloads first, the radio passes on what is left.
      const node = new AudioWorkletNode(context, 'radio-pop', { processorOptions: pops });
      input = source.connect(node); nodes.push(node);
    }
    input.connect(high).connect(low).connect(mid).connect(shaper).connect(squeeze).connect(level).connect(boost).connect(context.destination);
    // The noises do not pass through the element, so its volume and mute are applied to them here.
    let loudness = muted ? 0 : volume * settings.noise / 100;
    let hiss = null, hissGain = null, released = false;
    return {
      boosted: true,
      update(next) {
        boost.gain.value = Math.max(1, next.volume);
        loudness = next.muted ? 0 : next.volume * settings.noise / 100;
        if (hissGain) hissGain.gain.value = preset.hiss * loudness;
      },
      start(first = true) {
        if (released) return;
        if (settings.squelch && first) burst(0.05, 0.05 * loudness);
        if (settings.hiss && loudness > 0) {
          hiss = context.createBufferSource(); hiss.buffer = noise(); hiss.loop = true;
          const band = context.createBiquadFilter(); band.type = 'bandpass'; band.frequency.value = 2000; band.Q.value = 0.5;
          const gain = context.createGain(); gain.gain.value = preset.hiss * loudness; hissGain = gain;
          hiss.connect(band).connect(gain).connect(context.destination); hiss.start();
          nodes.push(band, gain);
        }
      },
      // completed: the call was heard to its end, which is when a radio lets go with a click.
      release(completed = false) {
        if (released) return;
        released = true;
        try { hiss?.stop(); } catch { /* It may not have started. */ }
        if (completed && settings.squelch) burst(0.12, 0.09 * loudness);
        nodes.forEach((node) => { try { node.disconnect(); } catch { /* Already gone. */ } });
      },
    };
  } catch {
    // Once captured, an element cannot return to native output. Keep a plain audible route.
    if (source) { try { source.disconnect(); source.connect(context.destination); } catch { /* Context failed. */ } }
    return source ? { start() {}, update() {}, release() { source.disconnect(); } } : null;
  }
}

// For tests: forget the context.
export function reset() { context = null; contextSink = ''; sinkFlight = null; noiseBuffer = null; popsReady = false; popsLoading = false; }
