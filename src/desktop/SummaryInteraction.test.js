import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import DrivingSummary from './DrivingSummary';
import { activateSession } from './sessionData';

jest.mock('./Telemetry', () => ({ TrackMap: () => <div /> }));
jest.mock('../components/PracticeCoach', () => () => <div />);

test.each([['analysis', '비교·분석'], ['next', '다음 주행에서 할 일']])('task selection keeps %s expanded until the tasks background is clicked', (column, label) => {
  activateSession({ filename: 'fixture.ibt', session: { track_length_m: 5000 }, laps: [], corners: [] });
  const corners = [1, 2, 3].map(i => ({ id: `C${i}`, label: `구간 ${i}`, delta_s: i / 10,
    target: { min_speed_kph: 80 + i }, reference: { min_speed_kph: 90 }, entry_pct: i / 10, exit_pct: i / 10 + .05 }));
  const options = corners.map(corner => ({ corner_ids: [corner.id], focus: { root_cause: corner.id,
    root_cause_label: `과제 ${corner.id}`, corner_id: corner.id, total_observed_loss_s: corner.delta_s, success_criteria: [] },
    next_run: { mode: 'DRIVING_FOCUS', lock: [], success_criteria: [], required_valid_laps: 2, recommended_valid_laps: 3 } }));
  const { container } = render(<DrivingSummary names={{}} comparison={{ corners, rows: [], paths: { target: [] }, practiceSummary: { options } }} />);
  fireEvent.click(screen.getByRole('button', { name: `${label} 넓게 보기` }));
  fireEvent.click(screen.getByRole('radio', { name: '이번 연습: 과제 C2' }));
  expect(container.querySelector('.driving-summary')).toHaveAttribute('data-expanded', column);
  expect(within(container.querySelector('#summary-analysis')).getByText(/C2 · 구간 2/)).toBeInTheDocument();
  if (column === 'next') expect(within(container.querySelector('#summary-next')).getByRole('heading', { name: '과제 C2' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('heading', { name: /오늘의 연습 과제/ }));
  expect(container.querySelector('.driving-summary')).toHaveAttribute('data-expanded', 'tasks');
  expect(screen.getByRole('radio', { name: '이번 연습: 과제 C2' })).toHaveAttribute('data-state', 'on');
});
