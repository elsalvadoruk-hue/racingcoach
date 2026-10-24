import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import RacePositionChart from './RacePositionChart';

const payload = { status: 'READY', source: 'IBT', x_axis: 'lap', start_lap: 1, end_lap: 4, own_class_id: 'gt3',
  classes: [{ id: 'gt3', label: 'GT3' }], series: [
    { id: '0', label: '#64', own: true, class_id: 'gt3', points: [[1, 2, 2], [2, 2, 2], [3, 1, 1], [4, 1, 1]],
      events: [{ kind: 'incident', x: 1.2, lap: 2, at_s: 120, delta: 2 }, { kind: 'invalid', x: 2, lap: 2, at_s: 140 },
        { kind: 'personal', x: 3, lap: 3, at_s: 160, time_s: 89 }, { kind: 'fastest', x: 4, lap: 4, at_s: 180, time_s: 88 }] },
    { id: '1', label: '#31', own: false, class_id: 'gt3', points: [[1, 1, 1], [2, 1, 1], [3, 2, 2], [4, 2, 2]],
      events: [{ kind: 'pit_in', x: 2.2, end_x: 2.8, lap: 3, at_s: 150, duration_s: 30 }, { kind: 'pit_out', x: 2.8, lap: 3, at_s: 180 }] },
  ] };

test('only the pinned car gets annotations, without a detail panel or extra fetch', async () => {
  const load = jest.fn(async () => payload);
  render(<RacePositionChart filename="race.ibt" load={load} />);
  const own = await screen.findByRole('button', { name: /#64/ });
  expect(document.querySelector('.race-event-labels')).not.toBeInTheDocument();
  fireEvent.click(own);
  expect(screen.getByLabelText('#64 주행 사건과 랩 기록')).toBeInTheDocument();
  expect(document.querySelector('[data-event-trace=incident]')).toBeInTheDocument();
  expect(document.querySelector('.race-event-segment.is-invalid')).toBeInTheDocument();
  expect(document.querySelector('linearGradient')).toHaveAttribute('gradientUnits', 'userSpaceOnUse');
  expect(document.querySelector('[data-event-trace=fastest]')).toBeInTheDocument();
  expect(document.querySelector('.detail')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /#31/ }));
  expect(document.querySelector('[data-event-trace=incident]')).not.toBeInTheDocument();
  expect(document.querySelector('.race-event-pit-span')).toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '전체 보기' }));
  expect(document.querySelector('.race-event-labels')).not.toBeInTheDocument();
});

test('event and lap-record toggles independently hide guides and lap colors', async () => {
  render(<RacePositionChart filename="race.ibt" load={async () => payload} />);
  fireEvent.click(await screen.findByRole('button', { name: /#64/ }));
  fireEvent.click(screen.getByRole('button', { name: '주행 사건 표시' }));
  expect(document.querySelector('[data-event-trace=incident]')).not.toBeInTheDocument();
  expect(document.querySelector('[data-event-trace=invalid]')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '랩 기록 표시' }));
  expect(document.querySelector('.race-event-guide')).not.toBeInTheDocument();
  expect(document.querySelector('.race-event-segment')).not.toBeInTheDocument();
});

test('a gap stays disconnected under a colored segment and dense labels group', async () => {
  const row = payload.series[0];
  const events = Array.from({ length: 1000 }, (_, i) => ({ kind: 'incident', x: 1.2 + i * .00001, lap: 2, at_s: i, delta: 1 }));
  render(<RacePositionChart filename="race.ibt" load={async () => ({ ...payload,
    series: [{ ...row, points: [[1, 2, 2], [1.5, null, null], [2, 1, 1]], events: [...events, { kind: 'invalid', x: 2, lap: 2 }] }] })} />);
  fireEvent.click(await screen.findByRole('button', { name: /#64/ }));
  expect(document.querySelector('.race-event-segment')).not.toBeInTheDocument();
  expect(document.querySelectorAll('.race-event-label').length).toBeLessThan(10);
  expect(document.querySelector('[data-event-kind=incident]')).toHaveAttribute('title', expect.stringContaining('인시던트 +1'));
});

test('selecting a car reveals its first visible-range rank without overriding later scrolling', async () => {
  const own = { ...payload.series[0], points: [[1, 20, 20], [2, 20, 20], [3, 19, 19], [4, 19, 19]] };
  render(<RacePositionChart filename="race.ibt" load={async () => ({ ...payload, series: [own] })} />);
  const button = await screen.findByRole('button', { name: /#64/ });
  const scroll = screen.getByRole('region', { name: /순위 그래프/ });
  Object.defineProperty(scroll, 'clientHeight', { value: 150 });
  fireEvent.click(button);
  expect(scroll.scrollTop).toBeGreaterThan(400);
  scroll.scrollTop = 80;
  fireEvent.pointerLeave(screen.getByRole('img'));
  expect(scroll.scrollTop).toBe(80);
});
