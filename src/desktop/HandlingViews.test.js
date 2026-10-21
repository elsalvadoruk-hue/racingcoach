import React from 'react';
import { render, screen } from '@testing-library/react';
import { HandlingBadges, HandlingDetail, HandlingRepeats } from './HandlingViews';

const item = { corner_id: 'C1', label: '측정 구간', phase: 'rotation', kind: 'understeer',
  with: 'braking', laps: 5, of_laps: 12, observed_laps: 12, time_cost_s: .09 };

test('badges reuse the existing blue and orange tokens and deduplicate phases', () => {
  render(<HandlingBadges items={[item, { ...item, phase: 'drive' }, { ...item, kind: 'oversteer' }]} />);
  expect(screen.getAllByText('평소보다 회전 적음')).toHaveLength(1);
  expect(screen.getByText('평소보다 회전 적음')).toHaveClass('text-cyan-400');
  expect(screen.getByText('평소보다 회전 많음')).toHaveClass('text-chart-reference');
});

test('detail reports measured frequency, cost and accompanying input without a cause assertion', () => {
  render(<HandlingDetail items={[item]} />);
  expect(screen.getByRole('region', { name: '차 거동' })).toHaveTextContent('회전 중 평소보다 회전 적음 (제동 중) · 12랩 중 5랩 · 나온 랩이 0.09초 느림');
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
  const rows = screen.getByRole('region', { name: '차 거동' }).querySelectorAll('p');
  expect(rows[0]).toHaveTextContent('시간 차이 0.00초');
  expect(rows[1]).toHaveTextContent('0.03초 빠름');
  expect(rows[2]).toHaveTextContent('시간 차이 확인 필요');
});

test('countersteering is separate from the personal baseline and missing comparisons remain explicit', () => {
  render(<><HandlingBadges items={[{ ...item, kind: 'countersteer' }]} /><HandlingDetail items={[{ ...item, kind: 'countersteer', time_cost_s: null }]} coverage={[{ classifiable: 0, unknown: 5 }]} /></>);
  expect(screen.getByText('반대 조향')).toHaveClass('text-foreground');
  expect(screen.getByText(/개인 기준과 별개로 관측/)).toBeInTheDocument();
  expect(screen.getByText('개인 기준 전 구간 비교 0/5회 · 5회 확인 필요')).toBeInTheDocument();
});
