import assert from 'node:assert/strict';
import { controlWindow, controlStatus } from './controlEvents.js';

test('native adjacent pulses merge for drawing while retaining time when clipping', () => {
  const signal = { available: true, coverage: [[0, 1]], events: [[.1, .2, .3], [.2, .3, .6], [.5, .5, 0]] };
  const view = controlWindow(signal, .15, .6);
  assert.deepEqual(view.runs, [[.15, .3], [.5, .5]]);
  assert.ok(Math.abs(view.duration - .75) < 1e-9);
  assert.equal(view.complete, true);
});
test('missing channel, missing window and observed inactive have different labels', () => {
  assert.equal(controlStatus(undefined, 0, 1), '작동 신호 없음');
  assert.equal(controlStatus({ available: true, coverage: [[.1, .2]], events: [] }, .4, .5), '선택 범위 관측 없음');
  assert.equal(controlStatus({ available: true, coverage: [[0, 1]], events: [] }, .4, .5), '개입 없음');
  assert.match(controlStatus({ available: false, reason: 'AGGREGATE_REFERENCE' }, 0, 1), /종합 기준/);
});
test('a gap is neither joined into an active run nor reported as full coverage', () => {
  const signal = { available: true, coverage: [[.1, .2], [.3, .4]], events: [[.1, .2, .3], [.3, .4, .3]] };
  const view = controlWindow(signal, .1, .4);
  assert.equal(view.runs.length, 2);
  assert.equal(view.complete, false);
  assert.match(controlStatus(signal, .1, .4), /일부 누락/);
});
test('revisited distance never double-counts coverage and conceals an unobserved gap', () => {
  const view=controlWindow({available:true,coverage:[[.1,.2],[.1,.2]],events:[]},.1,.3);
  assert.ok(Math.abs(view.coverage-.1)<1e-9);
  assert.equal(view.complete,false);
});
test('the inactive side of an event boundary is not drawn as a new intervention', () => {
  const view=controlWindow({available:true,coverage:[[0,1]],events:[[.1,.2,.3]]},.2,.3);
  assert.deepEqual(view.runs,[]);
});
test('an isolated observed flag never claims a measured zero intervention time', () => {
  const signal={available:true,coverage:[[.2,.2]],events:[[.2,.2,0]]};
  assert.match(controlStatus(signal,.1,.3),/개입 관측.*시간 확인 필요/);
});
