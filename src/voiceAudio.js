export function speechFits(windowSeconds, text) {
  return windowSeconds == null || windowSeconds >= Math.max(2, [...(text || '').replace(/\s/g, '')].length / 4.5 + 1);
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

export class VoiceRecorder {
  async open(inputDevice = '') {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Chrome 또는 Edge의 localhost 화면에서 마이크를 사용하세요.');
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      ...(inputDevice && inputDevice !== 'default' ? { deviceId: { exact: inputDevice } } : {}) } });
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      this.context = new Context({ sampleRate: 16000 });
      await this.context.audioWorklet.addModule(`${process.env.PUBLIC_URL || ''}/voice-capture-worklet.js`);
      await this.context.resume();
      this.source = this.context.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.context, 'voice-capture');
      this.silent = this.context.createGain(); this.silent.gain.value = 0;
      this.node.port.onmessage = ({ data }) => { if (this.recording) this.chunks.push(data); };
      this.source.connect(this.node); this.node.connect(this.silent); this.silent.connect(this.context.destination);
      const track = this.stream.getAudioTracks?.()[0];
      return { deviceId: track?.getSettings?.().deviceId || '', label: track?.label || '' };
    } catch (error) { this.close(); throw error; }
  }
  async start() {
    if (!this.context || !this.stream?.active) throw new Error('마이크 연결이 끊겼습니다. 다시 준비하세요.');
    await this.context.resume();
    this.chunks = []; this.recording = true;
  }
  finish() {
    this.recording = false;
    const chunks = this.chunks || []; this.chunks = [];
    if (chunks.reduce((sum, chunk) => sum + chunk.length, 0) / this.context.sampleRate < .25) throw new Error('너무 짧게 녹음했습니다. 버튼을 누르고 한 문장 말해주세요.');
    return encodeWav(chunks, this.context.sampleRate);
  }
  close() {
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
