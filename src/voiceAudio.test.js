import { encodeWav } from './voiceAudio';

const decode = (encoded) => new DataView(Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0)).buffer);
test('recorded chunks become a 16kHz mono PCM WAV with clipped signed samples', () => {
  const wav = decode(encodeWav([new Float32Array([2, -2]), new Float32Array([0, .5])], 16000));
  expect(wav.getUint32(24, true)).toBe(16000);
  expect(wav.getUint16(22, true)).toBe(1);
  expect(wav.getUint16(34, true)).toBe(16);
  expect(wav.getUint32(40, true)).toBe(8);
  expect([0, 1, 2, 3].map((i) => wav.getInt16(44 + i * 2, true))).toEqual([32767, -32768, 0, 16383]);
});
test('48kHz source samples are averaged rather than dropped when downsampling', () => {
  const wav = decode(encodeWav([new Float32Array([1, 0, -1, .5, .5, .5])], 48000));
  expect(wav.getUint32(40, true)).toBe(4);
  expect(wav.getInt16(44, true)).toBe(0);
  expect(wav.getInt16(46, true)).toBe(16383);
});
