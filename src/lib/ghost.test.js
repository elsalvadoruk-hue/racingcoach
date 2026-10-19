import { gapAt, positionAt, replaySpan, timeAtPct, timeline } from './ghost';

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
