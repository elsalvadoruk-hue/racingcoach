import assert from 'node:assert/strict';
import { DEFAULT_CHANNEL_ORDER, normalizeChannelOrder, moveChannel, readChannelOrder, timelineHeights, lapCounts, fileLapLabel, fileBestLap, fileRecordedLabel, fileChoiceLabel, isBestLap, sessionTypeLabel, sessionTypeTone } from './labPreferences.js';

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

test('IBT choices put the recorded minute, track and millisecond best before existing details', () => {
  const name = 'mclaren720sgt3_hockenheim gp 2026-10-22 21-55-12.ibt';
  const label = fileChoiceLabel({ name, track_name: 'Hockenheim', best_lap_s: 110,
    detected_laps: 21, valid_laps: 16, session_type: 'Race', car_name: 'McLaren' });
  assert.equal(label, '26-10-22 21:55 · Hockenheim · 베랩 1:50.000 · 기록 21랩 · 유효 16 · 비유효 5 · 레이스 · McLaren');
  assert.ok(!label.includes('.ibt'));
});

test('LMU choices use recording metadata for opaque names and retain measurement eligibility', () => {
  const label = fileChoiceLabel({ name: 'abc123', recording_time: '2026-10-10T22:22:59',
    track_name: 'Monza', best_lap_s: 119.9996, detected_laps: 5, measurement_laps: 3, session_type: 'Practice' });
  assert.ok(label.startsWith('26-10-10 22:22 · Monza · 베랩 2:00.000 ·'));
  assert.ok(label.includes('측정 가능 3 · 유효성 확인 필요'));
  assert.equal(fileRecordedLabel({ name: 'track_2026-10-10_22-22-59.duckdb' }), '26-10-10 22:22');
  const local = new Date('2026-10-20T10:00:00Z');
  assert.equal(fileRecordedLabel({ recording_time: '2026-10-20T10_00_00Z' }),
    `26-10-${String(local.getDate()).padStart(2, '0')} ${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}`);
});

test('unknown or invalid dates and missing best laps stay unknown rather than using modification time', () => {
  assert.equal(fileRecordedLabel({ name: 'unknown.ibt', modified_at: 1790000000 }), '시간 확인 전');
  assert.equal(fileRecordedLabel({ name: 'track 2026-02-30 22-22-00.ibt' }), '시간 확인 전');
  assert.equal(fileRecordedLabel({ name: 'track 2026-10-10 25-22-00.ibt' }), '시간 확인 전');
  assert.ok(fileChoiceLabel({ name: 'unknown.ibt', best_lap_s: 0 }).includes('베랩 확인 전'));
});

test('file best excludes faster invalid and incomplete laps in either game', () => {
  assert.equal(fileBestLap([{ status: 'INVALID', lap_time_s: 80 }, { status: 'VALID', lap_time_s: 100 },
    { status: 'UNVERIFIED', measurement_eligible: true, complete: true, lap_time_s: 99.123 },
    { status: 'UNVERIFIED', measurement_eligible: true, complete: false, lap_time_s: 70 }]), 99.123);
  assert.equal(fileBestLap([]), null);
});
