import React from 'react';
import { render, screen } from '@testing-library/react';
import { HandlingBadges, HandlingDetail, HandlingRepeats } from './HandlingViews';

const item = { corner_id: 'C1', label: '측정 구간', phase: 'rotation', kind: 'understeer',
  with: 'braking', laps: 5, of_laps: 12, observed_laps: 12, time_cost_s: .09 };

test('compact badges explain personal reactions and deduplicate phases', () => {
  render(<HandlingBadges items={[item, { ...item, phase: 'drive' }, { ...item, kind: 'oversteer' }]} />);
  expect(screen.getAllByText('덜 돌아감')).toHaveLength(1);
  expect(screen.getByText('덜 돌아감')).toHaveClass('text-cyan-400');
  expect(screen.getByText('덜 돌아감')).toHaveAttribute('title', expect.stringContaining('내 다른 랩보다'));
  expect(screen.getByText('더 돌아감')).toHaveClass('text-chart-reference');
});

test('detail reports measured frequency, cost and accompanying input without a cause assertion', () => {
  render(<HandlingDetail items={[item]} />);
  const observation = screen.getByRole('article', { name: '회전 · 덜 돌아감' });
  expect(observation).toHaveTextContent('12랩 중 5랩');
  expect(observation).toHaveTextContent('나온 랩이 0.09초 느림');
  expect(observation).toHaveTextContent('함께 관측된 조작: 제동 중');
  expect(screen.getByText(/위에서 선택한 기준 랩·Garage61 비교와는 별개/)).toBeInTheDocument();
  expect(screen.getByText(/문턱은 검증 전 초기값/)).toBeInTheDocument();
});

test('LMU approximation, missing cost and observed-lap coverage stay explicit', () => {
  render(<HandlingDetail items={[{ ...item, approximate: true, time_cost_s: null, observed_laps: 7, with: 'lift' }]} />);
  expect(screen.getByText(/시간 차이 확인 필요/)).toBeInTheDocument();
  expect(screen.getByText(/전 구간 비교 가능 7랩/)).toBeInTheDocument();
  expect(screen.getByText('참고용(LMU는 회전량을 GPS로 추정)')).toBeInTheDocument();
  expect(screen.getByText(/스로틀을 뗀 직후/)).toBeInTheDocument();
});

test('repeated card uses only multiple files, descending costs and at most three rows', () => {
  render(<HandlingRepeats items={[{ ...item, files: 1, label: '한 파일' }, ...[1, 4, 2, 3].map(cost => ({ ...item, files: 2, time_cost_s: cost, label: `구간 ${cost}` }))]} />);
  expect(screen.queryByText(/한 파일/)).not.toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
  expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('구간 4 · 지난 2개 주행에서 반복');
  expect(screen.queryByText(/구간 1/)).not.toBeInTheDocument();
});

test('unknown or absent observations do not invent a handling verdict', () => {
  const { container } = render(<><HandlingDetail /><HandlingRepeats /></>);
  expect(container).toBeEmptyDOMElement();
});

test('known time differences precede frequency-only observations even when the difference is negative', () => {
  render(<HandlingDetail items={[{ ...item, time_cost_s: null, laps: 12 }, { ...item, time_cost_s: -.03, kind: 'oversteer' }, { ...item, time_cost_s: 0 }]} />);
  const rows = screen.getByRole('region', { name: '차 거동' }).querySelectorAll('[data-handling-item]');
  expect(rows[0]).toHaveTextContent('시간 차이 0.00초');
  expect(rows[1]).toHaveTextContent('0.03초 빠름');
  expect(rows[2]).toHaveTextContent('시간 차이 확인 필요');
});

test('both directions have an explanation without presenting them as absolute understeer or oversteer', () => {
  render(<HandlingDetail items={[item, { ...item, kind: 'oversteer' }]} />);
  expect(screen.getByText('표시 뜻과 비교 범위').closest('details')).not.toHaveAttribute('open');
  expect(screen.getByText(/서로 다른 랩이나 단계에서 두 표시가 함께/)).toBeInTheDocument();
  expect(screen.getByText(/절대적인 언더·오버 판정이나 원인·셋업 효과를 확정하지 않습니다/)).toBeInTheDocument();
});

test('countersteering is separate from the personal baseline and missing comparisons remain explicit', () => {
  render(<><HandlingBadges items={[{ ...item, kind: 'countersteer' }]} /><HandlingDetail items={[{ ...item, kind: 'countersteer', time_cost_s: null }]} coverage={[{ classifiable: 0, unknown: 5 }]} /></>);
  expect(screen.getByLabelText('내 다른 랩 대비 차 거동').querySelector('[data-kind=countersteer]')).toHaveClass('text-foreground');
  expect(screen.getByRole('article', { name: '회전 · 반대 조향' })).toHaveTextContent('개인 기준과 별개로 관측했습니다.');
  expect(screen.getByText('개인 기준 전 구간 비교 0/5회 · 5회 확인 필요')).toBeInTheDocument();
});
