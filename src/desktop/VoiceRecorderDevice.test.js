import { VoiceRecorder } from '../voiceAudio';

test('the selected input is requested exactly while default keeps browser device selection', async () => {
  const getUserMedia = jest.fn(async () => { throw new Error('test-stop-before-audio-context'); });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  const recorder = new VoiceRecorder();
  await expect(recorder.open('usb-device')).rejects.toThrow('test-stop');
  expect(getUserMedia).toHaveBeenLastCalledWith({ audio: expect.objectContaining({ deviceId: { exact: 'usb-device' }, channelCount: 1 }) });
  await expect(recorder.open('default')).rejects.toThrow('test-stop');
  expect(getUserMedia.mock.calls.at(-1)[0].audio).not.toHaveProperty('deviceId');
});
