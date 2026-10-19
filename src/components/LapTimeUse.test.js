import { render, screen } from '@testing-library/react';
import LapTimeUse from './LapTimeUse';

const lap = (number, full, best = false) => ({
  lap_number: number, status: 'VALID', lap_time: `1:2${number}.000`, is_file_best: best,
  time_use: { share_pct: { brake: 25, coast: 5, part_throttle: 70 - full, full_throttle: full, corner: 50 }, seconds: {}, counted_s: 80 },
});

test('each valid lap shows where its time went, with the averages', () => {
  render(<LapTimeUse laps={[lap(1, 50), lap(2, 60, true), { lap_number: 3, status: 'OUT_LAP', time_use: null }]} />);
  expect(screen.getByTestId('time-use-lap-1')).toBeInTheDocument();
  expect(screen.getByTestId('time-use-lap-2')).toBeInTheDocument();
  expect(screen.queryByTestId('time-use-lap-3')).toBeNull();
  expect(screen.getByLabelText('풀스로틀 60.0%')).toBeInTheDocument();
  expect(screen.getByText(/풀스로틀 평균 55.0%/)).toBeInTheDocument();
  expect(screen.getAllByText('코너 50.0%')).toHaveLength(2);
});

test('without pedal data it says so instead of drawing anything', () => {
  render(<LapTimeUse laps={[{ lap_number: 1, status: 'VALID', time_use: null }]} />);
  expect(screen.getByText(/브레이크·스로틀 기록이 없어/)).toBeInTheDocument();
});
