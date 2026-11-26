export function speechFits(windowSeconds, text) {
  return windowSeconds == null || windowSeconds >= Math.max(2, [...(text || '').replace(/\s/g, '')].length / 4.5 + 1);
}

export function informationFits(sample, required) {
  const ahead = sample?.workload_in_s;
  return !Number.isFinite(required) || !Number.isFinite(ahead) || sample?.speech_hold_s < 0
    || sample?.on_pit_road === true || ahead >= required;
}

export function encodeWav(chunks, sampleRate, targetRate = 16000) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const input = new Float32Array(total);
  let offset = 0;
  chunks.forEach((chunk) => { input.set(chunk, offset); offset += chunk.length; });
  const count = Math.floor(total * targetRate / sampleRate);
  const buffer = new ArrayBuffer(44 + count * 2);
  const view = new DataView(buffer);
  const writeText = (at, value) => [...value].forEach((char, index) => view.setUint8(at + index, char.charCodeAt(0)));
  writeText(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); writeText(8, 'WAVE');
  writeText(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, targetRate, true);
  view.setUint32(28, targetRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  writeText(36, 'data'); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i += 1) {
    // Average source samples when downsampling; don't simply discard samples.
    const start = Math.floor(i * sampleRate / targetRate);
    const end = Math.max(start + 1, Math.floor((i + 1) * sampleRate / targetRate));
    let sum = 0;
    for (let j = start; j < end && j < total; j += 1) sum += input[j];
    const value = Math.max(-1, Math.min(1, sum / (end - start)));
    view.setInt16(44 + i * 2, value < 0 ? value * 32768 : value * 32767, true);
  }
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
  return btoa(binary);
}

export const VOICE_CAPTURE_PROFILES = Object.freeze({
  standard: Object.freeze({ echoCancellation: true, noiseSuppression: true, autoGainControl: true }),
  echo_off: Object.freeze({ echoCancellation: false, noiseSuppression: true, autoGainControl: true }),
  echo_only: Object.freeze({ echoCancellation: true, noiseSuppression: false, autoGainControl: false }),
  raw: Object.freeze({ echoCancellation: false, noiseSuppression: false, autoGainControl: false }),
});

export class VoiceRecorder {
  async open(inputDevice = '', processingProfile = 'standard') {
    if (!Object.prototype.hasOwnProperty.call(VOICE_CAPTURE_PROFILES, processingProfile)) throw new Error('지원하지 않는 마이크 처리 방식입니다.');
    const requested = VOICE_CAPTURE_PROFILES[processingProfile];
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Chrome 또는 Edge의 localhost 화면에서 마이크를 사용하세요.');
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, ...requested,
      ...(inputDevice && inputDevice !== 'default' ? { deviceId: { exact: inputDevice } } : {}) } });
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      this.context = new Context({ sampleRate: 16000 });
      await this.context.audioWorklet.addModule(`${process.env.PUBLIC_URL || ''}/voice-capture-worklet.js?v=2`);
      await this.context.resume();
      this.source = this.context.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.context, 'voice-capture');
      this.silent = this.context.createGain(); this.silent.gain.value = 0;
      this.node.port.onmessage = ({ data }) => {
        if (data?.type === 'stopped') {
          if (data.id === this.finishId) this.completeFinish?.();
        } else if (this.recording && data instanceof Float32Array) this.chunks.push(data);
      };
      this.source.connect(this.node); this.node.connect(this.silent); this.silent.connect(this.context.destination);
      for (const track of this.stream.getAudioTracks?.() || []) track.enabled = false;
      await this.context.suspend?.();
      const track = this.stream.getAudioTracks?.()[0];
      const settings = track?.getSettings?.() || {};
      const effective = {};
      for (const key of ['echoCancellation', 'noiseSuppression', 'autoGainControl']) {
        effective[key] = typeof settings[key] === 'boolean' ? settings[key] : null;
      }
      for (const key of ['sampleRate', 'channelCount']) {
        effective[key] = Number.isFinite(settings[key]) ? settings[key] : null;
      }
      return { deviceId: settings.deviceId || '', label: track?.label || '',
        processing: { profile: processingProfile, requested: { ...requested }, effective } };
    } catch (error) { this.close(); throw error; }
  }
  async start() {
    if (!this.context || !this.stream?.active) throw new Error('마이크 연결이 끊겼습니다. 다시 준비하세요.');
    for (const track of this.stream.getAudioTracks?.() || []) track.enabled = true;
    await this.context.resume();
    this.chunks = []; this.recording = true;
    this.node.port.postMessage?.({ recording: true });
  }
  finish({ tailMs = 0, maxSeconds = 45 } = {}) {
    if (this.finishing) return this.finishing;
    if (!this.recording) return Promise.reject(new Error('녹음이 중지됐습니다.'));
    this.finishId = (this.finishId || 0) + 1;
    this.finishing = new Promise((resolve, reject) => {
      const done = error => {
        clearTimeout(this.tailTimer); clearTimeout(this.finishTimer);
        this.completeFinish = null; this.cancelFinish = null;
        this.recording = false;
        for (const track of this.stream?.getAudioTracks?.() || []) track.enabled = false;
        this.context?.suspend?.().catch(() => {});
        const chunks = this.chunks || []; this.chunks = [];
        if (error) { reject(error); return; }
        try {
          const seconds = chunks.reduce((sum, chunk) => sum + chunk.length, 0) / this.context.sampleRate;
          if (seconds < .25) throw new Error('너무 짧게 녹음했습니다. 버튼을 누르고 한 문장 말해주세요.');
          if (seconds > maxSeconds) throw new Error(`녹음은 ${maxSeconds}초 이내로 해 주세요.`);
          resolve(encodeWav(chunks, this.context.sampleRate));
        } catch (e) { reject(e); }
      };
      this.completeFinish = () => done();
      this.cancelFinish = () => done(new Error('녹음을 취소했습니다.'));
      const requestStop = () => {
        this.finishTimer = setTimeout(() => done(new Error('녹음 종료를 확인하지 못했습니다. 다시 질문해 주세요.')), 750);
        this.node.port.postMessage({ type: 'finish', recording: false, id: this.finishId });
      };
      if (tailMs > 0) this.tailTimer = setTimeout(requestStop, Math.min(200, tailMs));
      else requestStop();
    });
    this.finishing = this.finishing.finally(() => { this.finishing = null; });
    return this.finishing;
  }
  close() {
    this.cancelFinish?.();
    this.recording = false; this.chunks = [];
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect(); this.node?.disconnect();
    this.context?.close().catch(() => {});
  }
}

// A call cut off for one of these reasons was not the driver's choice: it is said again
// from the start once the radio is free, if it is still true by then.
// How fast the voices are played, pitch kept (HTMLAudioElement.playbackRate with preservesPitch). The
// clips are unchanged, so any speed costs nothing. Spotter, pit wall and multipart calls
// have separate absolute settings: roles default to 1.15, multipart calls to 1.30.
// Formation briefing keeps its fixed 1.15.
// The server budgets time-sensitive calls at the slowest supported speed (1.0).
export const DEFAULT_RATE = 1.15;
export const MULTIPART_DEFAULT_RATE = 1.3;
export const RATE_MIN = 1, RATE_MAX = 1.4;

// output_removed / output_error: the output device went away under the call for a moment (Windows reopening it).
export const RESUMABLE = ['preempted', 'nearby_car', 'output_removed', 'output_error'];
export const MAX_RESUMES = 2;
