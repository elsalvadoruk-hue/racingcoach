import { render, screen } from '@testing-library/react';
import LapDistribution from './LapDistribution';

const laps = (times) => times.map((time_s, index) => ({ lap: index + 2, time_s }));

test('a change bigger than the lap-to-lap spread is called out', () => {
  render(<LapDistribution distribution={{ baseline: laps([80.0, 80.1, 80.2, 80.3]), candidate: laps([79.4, 79.5, 79.6, 79.7]) }} />);
  expect(screen.getByTestId('distribution-verdict')).toHaveTextContent('B의 중앙값이 A보다 0.600초 빠릅니다');
  expect(screen.getByTestId('distribution-verdict')).toHaveTextContent('흔들림보다 큽니다');
});

test('a change inside the spread is not called a difference', () => {
  render(<LapDistribution distribution={{ baseline: laps([80.0, 80.5, 81.0, 81.5]), candidate: laps([80.1, 80.6, 81.1, 81.4]) }} />);
  expect(screen.getByTestId('distribution-verdict')).toHaveTextContent('차이라고 보기 어렵습니다');
});

test('too few laps say so', () => {
  render(<LapDistribution distribution={{ baseline: laps([80, 81]), candidate: laps([80, 81, 82]) }} />);
  expect(screen.getByText(/3개 이상/)).toBeInTheDocument();
});
