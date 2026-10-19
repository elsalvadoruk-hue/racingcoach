import { render, screen } from '@testing-library/react';
import { CornerCoach } from './LiveCoach';

const loss = { id: 'C5', number: 5, delta_s: .31, findings: ['제동을 약 12미터 일찍 시작했습니다', '최저속도가 4킬로미터 낮았습니다'] };

test('explains why no comparison is available', () => {
  const { rerender } = render(<CornerCoach coach={null} connected={false} />);
  expect(screen.getByText('차량 연결 대기')).toBeInTheDocument();
  rerender(<CornerCoach coach={{ available: false, reason: '구간 정의가 없습니다.' }} connected />);
  expect(screen.getByText('구간 정의가 없습니다.')).toBeInTheDocument();
});

test('shows measured losses, the gain and the next reminder', () => {
  render(<CornerCoach connected coach={{ available: true, corner_count: 9, reference_corners: 8,
    last_lap: { lap: 6, compared: 8, losses: [loss], total_loss_s: .31, best_gain: { id: 'C2', number: 2, delta_s: -.08, findings: [] } },
    last_corner: { number: 7, delta_s: null }, focus: { text: '6랩 5번 구간에서 구간 최고보다 0.31초 느렸습니다.' } }} />);
  expect(screen.getByText('8 / 9')).toBeInTheDocument();
  expect(screen.getByText('6랩 · 8개 구간 비교')).toBeInTheDocument();
  expect(screen.getByText('5번 구간 +0.31초 · 제동을 약 12미터 일찍 시작했습니다 · 최저속도가 4킬로미터 낮았습니다')).toBeInTheDocument();
  expect(screen.getByText('가장 줄인 구간: 2번 구간 -0.08초')).toBeInTheDocument();
  expect(screen.getByText('7번 구간 · 첫 기록')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('다음 통과 전 안내 예정: 6랩 5번 구간');
});
