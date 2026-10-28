import { banterObservation } from './banterObservation';

test('observes the current clock and explicit booleans without touching audio', () => {
  const output = { audioBusy: jest.fn(() => false), busy: false, muted: false, play: jest.fn() };
  expect(banterObservation({ candidate_id: 'candidate', session_time: 10 }, { radio: { session_time: 11 } }, output, 0))
    .toEqual({ candidate_id: 'candidate', session_time: 11, audio_busy: false, engineer_busy: false, muted: false, pending_audio: false });
  expect(output.play).not.toHaveBeenCalled();
});

test('missing and throwing audio state remains unknown and pending work blocks it', () => {
  const output = { audioBusy: () => { throw Error('synthetic'); }, busy: 0, muted: undefined };
  const observed = banterObservation({}, { radio: { session_time: 1 }, events: [{}] }, output, false);
  expect(observed).toMatchObject({ audio_busy: null, engineer_busy: null, muted: null, pending_audio: true });
});

test('zero volume is treated as muted for shadow selection', () => {
  expect(banterObservation({}, { radio: {} }, { muted: false, volume: 0 }, false).muted).toBe(true);
});
