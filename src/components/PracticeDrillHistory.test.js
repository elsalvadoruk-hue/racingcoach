import { fireEvent, render, screen } from '@testing-library/react';
import PracticeDrillHistory from './PracticeDrillHistory';

afterEach(() => { delete global.fetch; });

test('loads saved attempts after reopening and distinguishes input success from pace', async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ runs: [{
    id: 'run1', at: 1700000000, plan: { label: '자동 구간 7', target: { unit: '%p' } },
    attempt_count: 1, outcome: 'INPUT_ACHIEVED', attempts: [{ n: 1, series: 1, origin_m: 300, time_s: 9.8, input_change: -6, goal_ok: true }],
  }] }) });
  render(<PracticeDrillHistory apiBase="" taskId="old" />);
  expect(global.fetch).not.toHaveBeenCalled();
  const details = screen.getByText('저장된 반복 연습 기록').closest('details');
  details.open = true;
  fireEvent(details, new Event('toggle'));
  expect(await screen.findByText('9.800 s')).toBeInTheDocument();
  expect(screen.getByText('-6.00 %p')).toBeInTheDocument();
  expect(screen.getByText(/입력 목표 달성 · 페이스는 다음 주행에서 확인/)).toBeInTheDocument();
  expect(global.fetch).toHaveBeenCalledWith('/api/practice/old/drills');
});

test('failed reads are visible instead of pretending there are no attempts', async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false });
  render(<PracticeDrillHistory apiBase="" taskId="old" />);
  const details = screen.getByText('저장된 반복 연습 기록').closest('details');
  details.open = true;
  fireEvent(details, new Event('toggle'));
  expect(await screen.findByRole('alert')).toHaveTextContent('반복 연습 기록을 읽지 못했습니다');
});
