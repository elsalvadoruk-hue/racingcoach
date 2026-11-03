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

// Minimal field projections of five saved 2.4.5 report snapshots. Source/hash
// mappings remain in the local verification evidence, outside the product tree.
const savedComparisons = [
  { label: 'unconfirmed traffic with no reference', corners: 26, references: 0,
    last: { number: 26, lap: 2, delta_s: null, comparison_eligible: false, traffic_context: 'UNKNOWN' }, lap: null,
    expected: '26번 구간 · 비교 보류' },
  { label: 'observed traffic with no reference', corners: 26, references: 0,
    last: { number: 2, lap: 3, delta_s: null, comparison_eligible: false, traffic_context: 'TRAFFIC_PRESENT' }, lap: null,
    expected: '2번 구간 · 비교 제외' },
  { label: 'observed traffic with five compared segments', corners: 15, references: 12,
    last: { number: 13, lap: 7, delta_s: null, comparison_eligible: false, traffic_context: 'TRAFFIC_PRESENT' },
    lap: { lap: 5, compared: 5, losses: [], total_loss_s: 0 }, expected: '13번 구간 · 비교 제외' },
  { label: 'observed traffic with one compared segment', corners: 15, references: 12,
    last: { number: 15, lap: 11, delta_s: null, comparison_eligible: false, traffic_context: 'TRAFFIC_PRESENT' },
    lap: { lap: 9, compared: 1, losses: [], total_loss_s: 0 }, expected: '15번 구간 · 비교 제외' },
  { label: 'later traffic retains the last usable comparison', corners: 15, references: 12,
    last: { number: 15, lap: 13, delta_s: null, comparison_eligible: false, traffic_context: 'TRAFFIC_PRESENT' },
    lap: { lap: 9, compared: 1, losses: [], total_loss_s: 0 }, expected: '15번 구간 · 비교 제외' },
];

test.each(savedComparisons)('keeps the recorded comparison scope: $label', ({ corners, references, last, lap, expected }) => {
  render(<CornerCoach connected coach={{ available: true, corner_count: corners, reference_corners: references,
    last_corner: last, last_lap: lap }} />);
  expect(screen.getByText(expected)).toBeInTheDocument();
  expect(screen.queryByText(/번 구간 · 첫 기록/)).not.toBeInTheDocument();
  expect(screen.queryByText('모든 구간이 최고와 0.1초 이내')).not.toBeInTheDocument();
  const value = lap ? `${lap.lap}랩 · ${lap.compared}개 구간 비교` : '비교 대기';
  const note = lap ? `비교한 ${lap.compared}개 구간에서 0.1초 이상 손실 없음` : '비교할 완료 랩을 기다립니다.';
  expect(screen.getByText(value)).toBeInTheDocument();
  expect(screen.getByText(note)).toBeInTheDocument();
});

test.each([
  ['generic exclusion', { comparison_eligible: false }, '3번 구간 · 비교 제외', '이번 통과는 단독 주행 기준과 비교하지 않았습니다.'],
  ['unknown traffic without the newer eligibility flag', { traffic_context: 'UNKNOWN' }, '3번 구간 · 비교 보류', '주변 차량 관측이 확인되지 않아 비교를 보류했습니다.'],
  ['observed traffic without the newer eligibility flag', { traffic_context: 'TRAFFIC_PRESENT' }, '3번 구간 · 비교 제외', '주변 차량이 있어 단독 주행 기준과 비교하지 않았습니다.'],
])('explains %s without calling it a first reference pass', (_, status, expected, note) => {
  render(<CornerCoach connected coach={{ available: true, corner_count: 9, reference_corners: 5,
    last_corner: { number: 3, delta_s: null, ...status } }} />);
  expect(screen.getByText(expected)).toBeInTheDocument();
  expect(screen.getByText(note)).toBeInTheDocument();
  expect(screen.queryByText(/번 구간 · 첫 기록/)).not.toBeInTheDocument();
});

test('keeps an eligible first pass distinct from no observation and a measured comparison', () => {
  const coach = { available: true, corner_count: 9, reference_corners: 0 };
  const { rerender } = render(<CornerCoach connected coach={coach} />);
  expect(screen.getByText('--')).toBeInTheDocument();
  expect(screen.getByText('비교 대기')).toBeInTheDocument();
  rerender(<CornerCoach connected coach={{ ...coach, last_corner: { number: 3, delta_s: null,
    comparison_eligible: true, traffic_context: 'SOLO' } }} />);
  expect(screen.getByText('3번 구간 · 첫 기록')).toBeInTheDocument();
  rerender(<CornerCoach connected coach={{ ...coach, reference_corners: 1, last_corner: { number: 3,
    delta_s: .2, findings: [] } }} />);
  expect(screen.getByText('3번 구간 +0.20초')).toBeInTheDocument();
  expect(screen.queryByText(/번 구간 · 첫 기록/)).not.toBeInTheDocument();
});

test.each([
  [0, '4랩 · 0개 구간 비교', '비교한 구간이 없어 이번 랩의 손실을 판단하지 않습니다.'],
  [undefined, '4랩 · 비교 범위 미확인', '비교 구간 수가 확인되지 않아 전체 랩의 손실을 판단하지 않습니다.'],
])('does not claim a complete comparison when the count is %s', (compared, value, note) => {
  render(<CornerCoach connected coach={{ available: true, corner_count: 15, reference_corners: 12,
    last_lap: { lap: 4, compared, losses: [], total_loss_s: 0 } }} />);
  expect(screen.getByText(value)).toBeInTheDocument();
  expect(screen.getByText(note)).toBeInTheDocument();
  expect(screen.queryByText('모든 구간이 최고와 0.1초 이내')).not.toBeInTheDocument();
});

test('a fully compared lap still limits the no-loss statement to its measured segments', () => {
  render(<CornerCoach connected coach={{ available: true, corner_count: 9, reference_corners: 9,
    last_lap: { lap: 6, compared: 9, losses: [], total_loss_s: 0 } }} />);
  expect(screen.getByText('6랩 · 9개 구간 비교')).toBeInTheDocument();
  expect(screen.getByText('비교한 9개 구간에서 0.1초 이상 손실 없음')).toBeInTheDocument();
});
