import React from 'react';
import { render, screen, within } from '@testing-library/react';
import CornerPhases, { DeviationLine } from './CornerPhases';
import Insights from './Insights';
import DrivingSummary from './DrivingSummary';

jest.mock('./Telemetry', () => ({ TrackMap: () => <div /> }));
jest.mock('../components/PracticeCoach', () => () => <div />);

const deviation = { event: 'brake_release', delta_m: 9, threshold_m: 3,
  then: [{ event: 'min_speed', delta_m: 1 }, { event: 'throttle_first', delta_m: 7 }] };
const corner = { id: 'C1', label: '측정 구간', delta_s: .1, target: {}, reference: {}, first_deviation: deviation,
  phases: { approach: { delta_s: .02 }, braking: { delta_s: .12 }, rotation: { delta_s: -.05 },
    drive: { delta_s: .03 }, straight_after: { delta_s: .2 } } };

test('phase bar distinguishes outside intervals, signs, gain and loss', () => {
  const { container } = render(<CornerPhases corner={corner} />);
  expect(screen.getByText('+0.12초')).toHaveClass('loss');
  expect(screen.getByText('−0.05초')).toHaveClass('gain');
  expect(container.querySelector('[data-phase=approach]')).toHaveClass('phase-outside');
  expect(container.querySelector('[data-phase=straight_after]')).toHaveClass('phase-outside');
  expect(container.querySelector('[data-phase=braking]')).not.toHaveClass('phase-outside');
  expect(screen.getByText(/코너 합계에는 포함하지 않음/)).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('탈출 속도 차이가 직선에서 더 크게 이어졌습니다.');
  expect(screen.getByText(/처음 달라진 조작:/)).toHaveTextContent('브레이크 놓음 9 m 늦음 · 그 뒤 가속 시작 7 m 늦음');
  expect(screen.queryByText(/최저 속도 지점/)).not.toBeInTheDocument();
});

test('missing phases are unknown while a measured zero remains zero', () => {
  const { container } = render(<CornerPhases corner={{ ...corner, phases: { braking: null, rotation: { delta_s: 0 }, drive: { delta_s: -.02 }, straight_after: null } }} />);
  expect(screen.getAllByText('—')).toHaveLength(2);
  expect(screen.getByText('+0.00초')).not.toHaveClass('gain', 'loss');
  expect(screen.getByText('−0.02초')).toHaveClass('gain');
  expect(container.querySelector('[data-phase=approach]')).toBeNull();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('no phase data does not invent bars or deviation text', () => {
  const { container } = render(<CornerPhases corner={{ id: 'C1' }} />);
  expect(container).toBeEmptyDOMElement();
});

test('deviation line uses specified early and minimum-speed wording and event uncertainty', () => {
  render(<DeviationLine deviation={{ event: 'brake_start', delta_m: -8,
    then: [{ event: 'min_speed', delta_m: -5 }, { event: 'full_throttle', delta_m: 6, threshold_m: 10 }] }} />);
  expect(screen.getByText(/처음 달라진 조작:/)).toHaveTextContent('제동 시작 8 m 이름 · 그 뒤 최저 속도 지점 5 m 앞');
  expect(screen.queryByText(/풀스로틀/)).not.toBeInTheDocument();
});

test('comparison sidebar renders phases for its selected corner', () => {
  render(<Insights corner={corner} corners={[corner]} names={{}} notes={[]} onNote={jest.fn()} onPractice={jest.fn()} />);
  expect(screen.getByRole('region', { name: '코너 단계별 시간 차이' })).toBeInTheDocument();
});

test('today task card shows deviation only for the matching focus corner', () => {
  const comparison = { corners: [corner], rows: [], paths: { target: [] }, practiceSummary: { options: [{
    focus: { root_cause: 'BRAKE_RELEASE_LATE', root_cause_label: '릴리즈 확인', corner_id: 'C1', success_criteria: [] }, corner_ids: ['C1'],
  }] } };
  render(<DrivingSummary comparison={comparison} names={{}} />);
  const card = screen.getByRole('heading', { name: /오늘의 연습 과제/ }).closest('.summary-task');
  expect(within(card).getByText(/처음 달라진 조작:/)).toHaveTextContent('브레이크 놓음 9 m 늦음');
});
