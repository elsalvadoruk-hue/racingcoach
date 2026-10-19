import { colourRuns, lossColour, lossRates, lossScale, rateAt } from './lossColours';

// 5 km lap, a point every 10 m; the lap loses 0.2 s over 400-600 m and gains 0.1 s over 2000-2200 m
const rows = Array.from({ length: 501 }, (_, i) => {
  const metres = i * 10;
  const lost = Math.min(.2, Math.max(0, (metres - 400) / 200 * .2));
  const gained = Math.min(.1, Math.max(0, (metres - 2000) / 200 * .1));
  return { distance_pct: metres / 5000, delta: lost - gained };
});

test('the slope of the time difference is the loss in seconds per 100 m', () => {
  const rates = lossRates(rows, 5000);
  expect(rateAt(rates, 500 / 5000)).toBeCloseTo(.1, 5);       // 0.2 s over 200 m
  expect(rateAt(rates, 2100 / 5000)).toBeCloseTo(-.05, 5);
  expect(rateAt(rates, 1000 / 5000)).toBeCloseTo(0, 5);
  expect(lossScale(rates)).toBeGreaterThanOrEqual(.02);
});

test('red where time is lost, green where gained, plain colour where not measured', () => {
  expect(lossColour(.1, .1)).toBe('rgb(239,68,68)');
  expect(lossColour(-.1, .1)).toBe('rgb(34,197,94)');
  expect(lossColour(0, .1)).toBe('rgb(229,231,235)');
  expect(lossColour(null, .1)).toBeNull();
  const gapped = rows.filter(row => row.distance_pct < .3 || row.distance_pct > .5);   // 1 km without data
  const rates = lossRates(gapped, 5000);
  expect(rateAt(rates, .4)).toBeNull();
  expect(lossRates(rows, null)).toEqual([]);
});

test('neighbouring segments of one colour are one run and breaks in the line are kept', () => {
  const points = [0, .1, .2, .3, .4].map((distance_pct, i) => ({ distance_pct, x: i, y: 0, breakBefore: i === 3 }));
  const runs = colourRuns(points, pct => (pct < .15 ? 'a' : 'b'));
  expect(runs.map(run => [run.colour, run.points.length])).toEqual([['a', 2], ['b', 2], ['b', 2]]);
});
