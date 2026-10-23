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
  expect(screen.getByText('더 돌아감')).toHaveClass('text-muted-foreground', 'border-dashed');
  expect(screen.getByText('더 돌아감')).toHaveAttribute('title', expect.stringContaining('아직 근거가 약해서'));
});

test('detail reports measured frequency, cost and accompanying input without a cause assertion', () => {
  render(<HandlingDetail items={[item]} />);
  const observation = screen.getByRole('article', { name: '회전 · 덜 돌아감' });
  expect(observation).toHaveTextContent('12랩 중 5랩');
  expect(observation).toHaveTextContent('평소의 내 랩보다 차가 덜 돌았습니다');
  expect(observation).toHaveTextContent('평소보다 0.09초 느림');
  expect(observation).toHaveTextContent('이 때문인지는 확인되지 않았습니다');
  expect(observation).toHaveTextContent('그때 조작: 제동 중');
  expect(screen.getByText(/잘했다·못했다를 판정하는 표시가 아닙니다/)).toBeInTheDocument();
  expect(screen.getByText(/위에서 선택한 기준 랩·Garage61 비교와는 별개/)).toBeInTheDocument();
  expect(screen.getByText(/문턱은 검증 전 초기값/)).toBeInTheDocument();
});

test('LMU approximation, missing cost and observed-lap coverage stay explicit', () => {
  render(<HandlingDetail items={[{ ...item, approximate: true, time_cost_s: null, observed_laps: 7, with: 'lift' }]} />);
  expect(screen.getByRole('article', { name: '회전 · 덜 돌아감' })).not.toHaveTextContent('확인 필요');
  expect(screen.queryByText('나온 랩의 이 코너 시간')).not.toBeInTheDocument();
  expect(screen.getByText(/시간에 준 영향은 알 수 없습니다/)).toBeInTheDocument();
  expect(screen.getByText('일부 랩은 구간의 일부만 비교했습니다.')).toBeInTheDocument();
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
  render(<HandlingDetail items={[{ ...item, time_cost_s: null, laps: 12 }, { ...item, time_cost_s: -.03, phase: 'drive' }, { ...item, time_cost_s: 0 }]} />);
  const rows = screen.getByRole('region', { name: '차 거동' }).querySelectorAll('[data-handling-item]');
  expect(rows[0]).toHaveTextContent('평소와 같음');
  expect(rows[1]).toHaveTextContent('0.03초 빠름');
  expect(rows[1]).toHaveTextContent('이렇게 달리는 게 더 낫다는 뜻은 아닙니다');
  expect(rows[2]).toHaveTextContent('시간에 준 영향은 알 수 없습니다');
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
  expect(screen.getByRole('article', { name: '회전 · 반대 조향' })).toHaveTextContent('차가 도는 반대쪽으로 핸들을 돌린 순간');
  expect(screen.getByText('이 코너는 비슷하게 지난 내 다른 랩이 부족해서 평소와 비교하지 못했습니다(랩별 제동·회전·가속 5번 중 0번 비교).')).toBeInTheDocument();
  expect(screen.getByText(/문제가 있다는 뜻도, 없다는 뜻도 아닙니다/)).toBeInTheDocument();
});

test('oversteer is shown as a reference after better supported observations', () => {
  render(<HandlingDetail items={[{ ...item, kind: 'oversteer', time_cost_s: .5 }, { ...item, time_cost_s: null }]} />);
  const rows = screen.getByRole('region', { name: '차 거동' }).querySelectorAll('[data-handling-item]');
  expect(rows[0]).toHaveTextContent('덜 돌아감');
  expect(rows[1]).toHaveTextContent('더 돌아감 · 참고');
  expect(rows[1].querySelector('[data-evidence-note]')).toHaveTextContent('우연일 가능성이 큽니다');
  expect(screen.getByText(/더 돌아감은 참고로 표시합니다/)).toBeInTheDocument();
});

test('a reference repeat never pushes a supported repeat out of the top three', () => {
  render(<HandlingRepeats items={[...[3, 2, 1].map(cost => ({ ...item, kind: 'oversteer', files: 2, time_cost_s: cost, label: `참고 ${cost}` })),
    { ...item, files: 2, time_cost_s: .1, label: '덜 구간' }]} />);
  const rows = screen.getAllByRole('listitem');
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('덜 구간');
  expect(screen.queryByText(/참고 1/)).not.toBeInTheDocument();
});

test('partial and full comparison counts are written in plain words', () => {
  const { rerender } = render(<HandlingDetail items={[item]} coverage={[{ classifiable: 12, unknown: 18 }]} />);
  expect(screen.getByText('랩별 제동·회전·가속 30번 중 12번을 평소와 비교했습니다. 나머지 18번은 비슷하게 지난 랩이 부족했습니다.')).toBeInTheDocument();
  rerender(<HandlingDetail items={[item]} coverage={[{ classifiable: 30, unknown: 0 }]} />);
  expect(screen.getByText('랩별 제동·회전·가속 30번을 모두 평소와 비교했습니다.')).toBeInTheDocument();
  expect(screen.queryByText(/문제가 있다는 뜻도/)).not.toBeInTheDocument();
});

test('slower links take priority within evidence levels while oversteer keeps its reference', () => {
  render(<HandlingDetail items={[{ ...item, time_cost_s: .9 }, { ...item, phase: 'drive', time_link: 'slower', time_cost_s: -.1 },
    { ...item, kind: 'oversteer', time_link: 'slower' }]} />);
  const rows = screen.getByRole('region', { name: '차 거동' }).querySelectorAll('[data-handling-item]');
  expect(rows[0]).toHaveTextContent('가속 구간');
  expect(rows[0]).toHaveTextContent('이게 나온 랩은 이 코너에서 실제로 느렸습니다');
  expect(rows[0]).toHaveTextContent('느린 랩과 겹침');
  expect(rows[1]).toHaveTextContent('이 때문인지는 확인되지 않았습니다');
  expect(rows[2]).toHaveTextContent('더 돌아감 · 참고');
  expect(rows[2]).toHaveTextContent('이게 나온 랩은 이 코너에서 실제로 느렸습니다');
  expect(rows[2].querySelector('[data-evidence-note]')).toBeInTheDocument();
});

test.each(['faster', 'unclear', null, undefined])('non-slower time links stay unconfirmed (%s)', link => {
  render(<HandlingDetail items={[{ ...item, time_link: link }]} />);
  expect(screen.queryByText(/실제로 느렸습니다/)).not.toBeInTheDocument();
  expect(screen.queryByText('느린 랩과 겹침')).not.toBeInTheDocument();
});

test('a tendency seen on only one or two laps is read as possibly chance', () => {
  render(<HandlingDetail items={[{ ...item, kind: 'countersteer', laps: 1, of_laps: 10, time_cost_s: null }]} />);
  const card = screen.getByRole('article', { name: '회전 · 반대 조향' });
  expect(card).toHaveTextContent('10랩 중 1랩');
  expect(card).toHaveTextContent('한두 랩에서만 나온 일이라 우연일 수 있습니다');
  expect(card).not.toHaveTextContent('확인 필요');
});
