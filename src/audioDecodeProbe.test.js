import { probeAudioDecode } from './audioDecodeProbe';

const original = window.OfflineAudioContext;
afterEach(() => { window.OfflineAudioContext = original; jest.useRealTimers(); });
const context = decode => { window.OfflineAudioContext = jest.fn(() => ({ decodeAudioData: decode })); };

test('decodable payload and known WAV distinguish native loading stalls without playing anything', async () => {
  const decode = jest.fn(async () => ({})); context(decode);
  expect(await probeAudioDecode('QUJDRA==')).toBe('decoded');
  expect(decode).toHaveBeenCalledTimes(2);
});
test('invalid payload is distinguished only when the control WAV decodes', async () => {
  context(jest.fn().mockRejectedValueOnce(new Error()).mockResolvedValueOnce({}));
  expect(await probeAudioDecode('QUJDRA==')).toBe('source_failed');
});
test('control decoder failure never labels a payload corrupt', async () => {
  context(jest.fn(async () => { throw new Error(); }));
  expect(await probeAudioDecode('QUJDRA==')).toBe('both_failed');
});
test('a hung decoder is bounded', async () => {
  jest.useFakeTimers(); context(() => new Promise(() => {}));
  const pending = probeAudioDecode('QUJDRA==');
  jest.advanceTimersByTime(1000);
  expect(await pending).toBe('timeout');
});
test('unavailable decoder and oversized payload avoid allocation', async () => {
  window.OfflineAudioContext = undefined;
  expect(await probeAudioDecode('QUJDRA==')).toBe('unavailable');
  const decode = jest.fn(); context(decode);
  expect(await probeAudioDecode('A'.repeat(3 * 1024 * 1024))).toBe('oversize');
  expect(decode).not.toHaveBeenCalled();
});
