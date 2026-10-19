import { render, screen } from '@testing-library/react';
import DamperHistogram from './DamperHistogram';

const run = (fast) => ({
  available: true, edges_mm_s: [-20, -10, 0, 10, 20], slow_limit_mm_s: 50, sample_hz: 60, laps: [2],
  wheels: { LF: { share_pct: [10, 40, 40, 10], fast_bump_pct: fast, fast_rebound_pct: 5, slow_bump_pct: 40, slow_rebound_pct: 45, p95_mm_s: 80 } },
});

test('one run shows each wheel with its fast and slow shares', () => {
  render(<DamperHistogram current={run(10)} />);
  expect(screen.getByTestId('damper-LF')).toBeInTheDocument();
  expect(screen.queryByTestId('damper-RF')).toBeNull();
  expect(screen.getByText('빠른 범프 10.0%')).toBeInTheDocument();
});

test('A and B are laid over each other with the change', () => {
  render(<DamperHistogram baseline={run(10)} candidate={run(14)} />);
  expect(screen.getByText('빠른 범프 10.0% → 14.0% (+4.0%p)')).toBeInTheDocument();
  expect(screen.getByText('┄ A · 변경 전')).toBeInTheDocument();
});

test('a file without shock channels says so', () => {
  render(<DamperHistogram current={{ available: false, reason: 'NO_SHOCK_CHANNELS' }} />);
  expect(screen.getByText(/쇼크 속도 기록이 없습니다/)).toBeInTheDocument();
});
