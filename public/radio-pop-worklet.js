// Microphone pops for the radio effect (src/radioEffect.js).
//
// Without a pop filter, the air of a ㅂ or ㅍ reaches the microphone as one slow push: measured on a
// pop filter comparison ("Peter Parker"), a half wave of about 30 Hz, far bigger than the voice, that
// drives the microphone past its limit. The voice riding on it is cut flat with it, and a radio that
// keeps its low end passes on the thump. The clips are clean, so the moments are found here, the push
// is added to the voice, and the sum is clipped, all before the radio filters.
//
// The amount was chosen by ear (artifacts/voice-pilot/radio-pops.html, scripts/voice_pilot_pops.py):
// processorOptions {amp, clip}, the push and the clipping point against the voice's recent peak.
// 'normal' is amp 2 / clip 1.2, 'strong' amp 3 / clip 1.05. The radio then cuts lows only below 150 Hz.
//
// The processor posts {pop: seconds} for each pop, which the tests and the measuring script read;
// the app ignores the messages.

const UP_S = 0.016;              // the push rises and falls back in 16 ms...
const BACK_S = 0.06;             // ...then swings back more slowly

class RadioPopProcessor extends AudioWorkletProcessor {
  constructor(options = {}) {
    super();
    const settings = options.processorOptions || {};
    this.amp = Number.isFinite(settings.amp) ? settings.amp : 2;
    this.clip = Number.isFinite(settings.clip) ? settings.clip : 1.2;
    const rate = sampleRate;
    this.fastKeep = Math.exp(-1 / (0.003 * rate));      // 3 ms energy: follows a burst as it starts
    this.riseKeep = Math.exp(-1 / (0.05 * rate));       // speech level: rises within words...
    this.fallKeep = Math.exp(-1 / (0.8 * rate));        // ...and lets go slowly through pauses
    this.fast = 0; this.level = 0;
    this.dip = 0;                // samples the voice has been far below its level (the closure)
    this.floor = 0;              // the lowest energy in that closure
    this.since = -1;             // samples since it came out of the closure, while the burst is judged
    this.closure = 0;            // how long that closure lasted
    this.cooldown = 0;           // no second pop right after one
    this.pop = -1;               // samples into the current pop, -1 when none
    this.popLength = Math.round((UP_S + BACK_S) * rate);
    this.peak = 0;               // the voice's recent peak: the push and the clipping are set against it
    this.peakKeep = Math.exp(-1 / (0.5 * rate));
    this.reach = 0;              // that peak, held for the pop under way
    this.time = 0;
  }

  process(inputs, outputs) {
    const input = inputs[0], output = outputs[0];
    if (!output || !output.length) return true;
    if (!input.length) { output.forEach((channel) => channel.fill(0)); return true; }
    const rate = sampleRate, source = input[0];
    for (let i = 0; i < source.length; i += 1) {
      const x = source[i];
      this.peak = Math.max(Math.abs(x), this.peak * this.peakKeep);
      this.fast = this.fastKeep * this.fast + (1 - this.fastKeep) * x * x;
      const keep = this.fast > this.level ? this.riseKeep : this.fallKeep;
      this.level = keep * this.level + (1 - keep) * this.fast;
      if (this.cooldown > 0) this.cooldown -= 1;

      // Measured on the app's voices (scripts/measure_radio_pops.py): before 피, 트, 플, 끝 the voice
      // drops 25 dB or more below its level for 30 to 100 ms, and comes back 20 dB or more within
      // 15 ms. A vowel after a pause rises more slowly, and a sentence start follows a longer silence.
      if (this.fast < this.level * 0.0032) {                       // -25 dB: inside a closure
        this.floor = this.dip === 0 ? this.fast : Math.min(this.floor, this.fast);
        this.dip += 1; this.since = -1;
      } else {
        if (this.dip > 0) { this.closure = this.dip; this.dip = 0; this.since = 0; }
        if (this.since >= 0) {
          const floor = Math.max(this.floor, this.level * 1e-5);
          if (this.fast >= floor * 100 && this.fast >= this.level * 0.01 && this.pop < 0 && this.cooldown === 0
              && this.amp > 0 && this.closure >= 0.025 * rate && this.closure <= 0.25 * rate) {
            this.pop = 0;
            this.reach = Math.max(this.peak, 0.02);
            this.cooldown = Math.round(0.12 * rate);
            this.port.postMessage({ pop: (this.time + i) / rate });
            this.since = -1;
          } else if (++this.since > 0.015 * rate) this.since = -1;  // too slow: not a burst
        }
      }

      let y = x;
      if (this.pop >= 0) {
        const t = this.pop / rate;
        const push = t < UP_S ? Math.sin((Math.PI * t) / UP_S)
          : -0.45 * Math.sin((Math.PI * (t - UP_S)) / 0.05) * Math.exp(-(t - UP_S) / 0.03);
        const edge = this.clip * this.reach;
        y = Math.max(-edge, Math.min(edge, x + this.amp * this.reach * push));
        this.pop += 1;
        if (this.pop >= this.popLength) this.pop = -1;
      }
      for (let c = 0; c < output.length; c += 1) output[c][i] = c === 0 || c >= input.length ? y : input[c][i];
    }
    this.time += source.length;
    return true;
  }
}

registerProcessor('radio-pop', RadioPopProcessor);
