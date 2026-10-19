import assert from 'node:assert/strict';
import { DEFAULT_CHANNEL_ORDER, normalizeChannelOrder, moveChannel, readChannelOrder, timelineHeights, lapCounts, fileLapLabel, isBestLap, sessionTypeLabel, sessionTypeTone } from './labPreferences.js';

test('old, duplicate or malformed preferences retain every supported channel once', () => {
  const order = normalizeChannelOrder(['throttle', 'unknown', 'throttle', 'speed']);
  assert.deepEqual(order.slice(0, 2), ['throttle', 'speed']);
  assert.equal(order.length, DEFAULT_CHANNEL_ORDER.length);
  assert.deepEqual(normalizeChannelOrder({ speed: 1 }), DEFAULT_CHANNEL_ORDER);
});
test('moving a visible channel preserves hidden channels for later modes', () => {
  const order = moveChannel(DEFAULT_CHANNEL_ORDER, 'throttle', 'delta');
  assert.equal(order[0], 'throttle');
  assert.ok(order.includes('gear') && order.includes('pedals'));
  assert.deepEqual(moveChannel(order, 'unknown', 'speed'), order);
});
test('blocked local storage restores a usable default order', () => {
  const read = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
  assert.deepEqual(readChannelOrder(), DEFAULT_CHANNEL_ORDER);
  read.mockRestore();
});
test('large gains with tiny or absent losses never escape the timeline', () => {
  assert.deepEqual(timelineHeights([{ delta_s: -5 }, { delta_s: .001 }, { delta_s: null }]), [20, 2, 0]);
  assert.deepEqual(timelineHeights([{ delta_s: -5 }, { delta_s: -2.5 }]), [20, 10]);
  assert.deepEqual(timelineHeights([{ delta_s: 0 }, { delta_s: NaN }]), [2, 0]);
});
test('file labels distinguish known zero valid laps from pending counts', () => {
  assert.equal(fileLapLabel({ detected_laps: 3, valid_laps: 0 }), '기록 3랩 · 유효 0 · 비유효 3');
  assert.equal(fileLapLabel({ detected_laps: null, valid_laps: null }), '랩 수 확인 전');
  assert.deepEqual(lapCounts([{ status: 'VALID' }, { status: 'INVALID' }, { status: 'OUT_LAP' }, { status: 'PARTIAL' }]), { detected_laps: 4, valid_laps: 1, complete_laps: 2 });
});
test('only valid timed laps can be best; invalid faster laps and missing times are excluded', () => {
  const laps = [{ status: 'VALID', lap_time_s: 100 }, { status: 'INVALID', lap_time_s: 90 }, { status: 'VALID', lap_time_s: null }, { status: 'VALID', lap_time_s: 101 }];
  assert.equal(isBestLap(laps[0], laps), true);
  assert.ok(laps.slice(1).every(lap => !isBestLap(lap, laps)));
});
test('actual session metadata distinguishes practice, qualifying, sprint and endurance race', () => {
  assert.equal(sessionTypeLabel({ session: { session_type: 'Practice' } }), '연습');
  assert.equal(sessionTypeTone({ session_type: 'Lone Qualify' }), 'qualifying');
  assert.equal(sessionTypeLabel({ session_type: 'Race', suggested_session_mode: 'BASELINE_3LAP' }), '레이스');
  assert.equal(sessionTypeTone({ session: { session_type: 'Race', suggested_session_mode: 'ENDURANCE' } }), 'endurance');
});
test('missing metadata does not infer practice from an analysis mode', () => {
  for (const value of [null, {}, { session: {} }, { suggested_session_mode: 'BASELINE_3LAP' }]) {
    assert.equal(sessionTypeLabel(value), '세션 유형 확인 필요');
    assert.equal(sessionTypeTone(value), 'unknown');
  }
});
