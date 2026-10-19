import { gapAt, heldPositionAt, positionAt, replayPair, replaySpan, timeAtPct, timeline } from './ghost';

// the player's lap takes 100 s, the reference 98 s, both at an even pace along a straight 1000-unit line
const path = (lapTime) => Array.from({ length: 11 }, (_, i) => ({ distance_pct: i / 10, x: i * 100, y: 0, time_s: lapTime * i / 10 }));
const mine = timeline(path(100)), theirs = timeline(path(98));

test('positions are interpolated in time and nothing is drawn outside the observed lap', () => {
  expect(positionAt(mine, 25)).toEqual({ x: 250, y: 0, pct: .25 });
  expect(positionAt(mine, 101)).toBeNull();
  expect(timeAtPct(theirs, .5)).toBeCloseTo(49);
});

test('points without a time or going back in time are left out', () => {
  const line = timeline([{ distance_pct: 0, x: 0, y: 0, time_s: 0 }, { distance_pct: .1, x: 1, y: 0, time_s: null },
    { distance_pct: .2, x: 2, y: 0, time_s: 5 }, { distance_pct: .3, x: 3, y: 0, time_s: 4 }]);
  expect(line.map(point => point.t)).toEqual([0, 5]);
  expect(positionAt(line, 2)).toBeNull();
});

test('the gap is how long ago the reference passed where the player is now', () => {
  expect(gapAt(mine, theirs, 50)).toBeCloseTo(1);          // at half a lap: 50 s against 49 s
});

test('the replay covers the whole lap, or a picked range from the first car in to the last car out', () => {
  expect(replaySpan(mine, theirs, null)).toEqual([0, 100]);
  const [start, end] = replaySpan(mine, theirs, [.2, .3]);
  expect(start).toBeCloseTo(19.6);
  expect(end).toBeCloseTo(30);
  expect(replaySpan(mine, [], null)).toBeNull();
});

test('a selected segment starts both laps together despite a large earlier time loss', () => {
  const target = timeline(path(100).map(p => ({ ...p, time_s: p.time_s + 35 })));
  const pair = replayPair(target, theirs, [.23, .37]);
  expect(pair.span[0]).toBe(0);
  expect(pair.span[1]).toBeCloseTo(14);
  expect(positionAt(pair.target, 0).pct).toBeCloseTo(.23);
  expect(positionAt(pair.reference, 0).pct).toBeCloseTo(.23);
  expect(positionAt(pair.target, 7).pct).toBeCloseTo(.30);
  expect(gapAt(pair.target, pair.reference, 7)).toBeCloseTo(.14);
  expect(heldPositionAt(pair.reference, 14).pct).toBeCloseTo(.37);
  expect(gapAt(pair.target, pair.reference, 14)).toBeCloseTo(.28);
  expect(target[0].t).toBe(35);
});

test('a faster target waits at segment end without increasing its final measured gap', () => {
  const pair = replayPair(theirs, mine, [.2, .3]);
  expect(heldPositionAt(pair.target, 10).pct).toBeCloseTo(.3);
  expect(gapAt(pair.target, pair.reference, 10)).toBeCloseTo(-.2);
});

test('segment replay requires common observations and does not interpolate through missing GPS', () => {
  expect(replayPair(mine, [], [.2, .3])).toBeNull();
  expect(replayPair(mine, theirs, [.5, .4])).toBeNull();
  expect(replayPair(mine, theirs, [null, .4])).toBeNull();
  const partial = mine.slice(2, 8);
  const pair = replayPair(partial, theirs, [.1, .4]);
  expect(positionAt(pair.target, 0).pct).toBe(.2);
  const gap = mine.map((p, i) => ({ ...p, gapBefore: i === 3 }));
  expect(positionAt(gap, 25)).toBeNull();
  expect(timeAtPct(gap, .25)).toBeNull();
  expect(replayPair(gap, theirs, [.2, .4])).toBeNull();
  expect(replayPair(mine, theirs, null).span).toEqual([0, 100]);
  const stopped = timeline([0, .1, .1, .2].map((pct, i) => ({ distance_pct:pct, x:pct * 1000, y:0, time_s:i * 10 })));
  const waiting = replayPair(stopped, mine, [.05, .2]);
  expect(positionAt(waiting.target, 10).pct).toBeCloseTo(.1);
  expect(waiting.span[1]).toBeCloseTo(25);
  expect(gapAt(waiting.target, waiting.reference, 25)).toBeCloseTo(10);
});
