import { render, screen } from '@testing-library/react';
import GGDiagram from './GGDiagram';

const run = (brake) => ({ available: true, bin_deg: 90, envelope_g: [1.5, 0.8, 1.5, brake], points: [[0.5, 0.2], [-1, -0.5]],
  max_brake_g: brake, max_accel_g: 0.8, max_lateral_g: 1.5, mean_combined_g: 0.9 });

test('one run draws its points, grip line and maxima', () => {
  const { container } = render(<GGDiagram current={run(1.8)} />);
  expect(screen.getByRole('img', { name: 'G-G 그림' })).toBeInTheDocument();
  expect(container.querySelectorAll('path')).toHaveLength(1);
  expect(screen.getByText('최대 제동 1.8 g')).toBeInTheDocument();
});

test('A and B are drawn over each other', () => {
  const { container } = render(<GGDiagram baseline={run(1.8)} candidate={run(1.9)} />);
  expect(container.querySelectorAll('path')).toHaveLength(2);
  expect(screen.getByTestId('gg-stats-A · 변경 전')).toBeInTheDocument();
  expect(screen.getByText('최대 제동 1.9 g')).toBeInTheDocument();
});

test('a file without acceleration says so', () => {
  render(<GGDiagram current={{ available: false, reason: 'NO_ACCELERATION_CHANNELS' }} />);
  expect(screen.getByText(/가속도 기록이 없습니다/)).toBeInTheDocument();
});
