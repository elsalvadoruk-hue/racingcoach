// Beeps for the corner drill (iRacing_Backend/live_drill.py). Our own short tones, made here as WAV:
// a separate audio element from the voice player, so a beep is never queued behind a sentence or cut by one,
// sent to the same output device the driver picked for the radio.
const RATE = 22050;

function wav(parts) {
  // parts: [[frequency Hz (0 = silence), seconds], ...]
  const samples = parts.reduce((sum, [, seconds]) => sum + Math.round(seconds * RATE), 0);
  const buffer = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)));
  text(0, 'RIFF'); view.setUint32(4, 36 + samples * 2, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, RATE, true); view.setUint32(28, RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, 'data'); view.setUint32(40, samples * 2, true);
  let offset = 44;
  parts.forEach(([frequency, seconds]) => {
    const count = Math.round(seconds * RATE);
    for (let i = 0; i < count; i += 1) {
      const fade = Math.min(1, i / 200, (count - i) / 200);          // no click at either end
      const value = frequency ? Math.sin((2 * Math.PI * frequency * i) / RATE) * 0.6 * fade : 0;
      view.setInt16(offset, value * 32767, true);
      offset += 2;
    }
  });
  let binary = '';
  new Uint8Array(buffer).forEach((byte) => { binary += String.fromCharCode(byte); });
  return `data:audio/wav;base64,${btoa(binary)}`;
}

// What each drill event sounds like.
export const BEEP_SHAPES = {
  brake_beep: [[880, 0.07]],                       // the two counting beeps
  brake_point: [[1320, 0.16]],                     // the braking point itself: higher and longer
  throttle_beep: [[990, 0.06]],                   // full throttle here: one short tone
  end_beep: [[520, 0.09], [0, 0.07], [520, 0.09]], // the end of the section: press Active Reset's run key
  save_hint: [[440, 0.18]],                        // a good place to save the start point
  done: [[660, 0.1], [0, 0.03], [880, 0.1], [0, 0.03], [1100, 0.18]],
};

const cache = {};
export function beepSource(kind) {
  if (!BEEP_SHAPES[kind]) return null;
  cache[kind] = cache[kind] || wav(BEEP_SHAPES[kind]);
  return cache[kind];
}

export function eventSound(event) {
  if (event.kind === 'brake_beep') return event.last ? 'brake_point' : 'brake_beep';
  return BEEP_SHAPES[event.kind] ? event.kind : null;
}

export function playBeep(kind, { outputId = '', volume = 1, muted = false } = {}) {
  const source = beepSource(kind);
  if (!source || muted || typeof Audio === 'undefined') return null;
  const audio = new Audio(source);
  audio.volume = Math.max(0, Math.min(1, volume));
  const start = () => audio.play().catch(() => {});
  if (outputId && typeof audio.setSinkId === 'function') audio.setSinkId(outputId).then(start, start); else start();
  return audio;
}
