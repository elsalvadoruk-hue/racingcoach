import { fireEvent, render, screen } from '@testing-library/react';
import CornerConsistency, { summarize } from './CornerConsistency';
import LapRangeFilter, { lapsInRange } from './LapRangeFilter';

const passes = (times) => times.map((time_s, index) => ({ lap: index + 1, time_s }));

test('the spread and the target lap against the three fastest passes come from the picked laps', () => {
  const stats = summarize(passes([5.0, 5.1, 5.2, 5.3, 6.5]), null, 4);
  expect(stats.count).toBe(5);
  expect(stats.median).toBeCloseTo(5.2);
  expect(stats.best3).toBeCloseTo(5.1);
  expect(stats.targetVsBest3).toBeCloseTo(0.2);
  expect(stats.outliers).toEqual([6.5]);
  const picked = summarize(passes([5.0, 5.1, 5.2, 5.3, 6.5]), new Set([1, 2, 3, 4]), 4);
  expect(picked.count).toBe(4);
  expect(summarize(passes([5.0, 5.1, 5.2]), new Set([1, 2]), 1)).toBeNull();
});

test('corners are listed with the most inconsistent first, and track order on request', () => {
  const consistency = { available: true, target_lap: 1, corners: [
    { id: 'C1', label: '1번 코너', apex_pct: 0.1, passes: passes([3.0, 3.01, 3.02, 3.03]) },
    { id: 'C2', label: '2번 코너', apex_pct: 0.2, passes: passes([4.0, 4.5, 4.9, 5.2]) },
  ] };
  render(<CornerConsistency consistency={consistency} allowedLaps={null} />);
  const labels = () => screen.getAllByTestId(/consistency-C/).map((node) => node.textContent);
  expect(labels()).toEqual(['2번 코너', '1번 코너']);
  fireEvent.click(screen.getByText('트랙 순서'));
  expect(labels()).toEqual(['1번 코너', '2번 코너']);
});

test('the lap filter keeps the valid laps inside the chosen lap-time range', () => {
  const laps = [80, 81, 85, 90].map((time, index) => ({ lap_number: index + 1, status: 'VALID', lap_time_s: time }));
  expect([...lapsInRange(laps, null)]).toEqual([1, 2, 3, 4]);
  expect([...lapsInRange(laps, [80, 85])]).toEqual([1, 2, 3]);
  const changes = [];
  render(<LapRangeFilter laps={laps} value={null} onChange={(value) => changes.push(value)} />);
  fireEvent.change(screen.getByLabelText('포함할 가장 느린 랩타임'), { target: { value: '85' } });
  expect(changes[0]).toEqual([80, 85]);
});
