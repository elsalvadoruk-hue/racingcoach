import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import RacePositionChart from './RacePositionChart';
import { pointAt, positionPath } from './racePositionGeometry';

const payload = { status: 'READY', source: 'IBT', start_s: 100, end_s: 200,
  classes: [{ id: 'gt3', label: 'GT3' }], reason: '관측 구간', series: [
    { id: '0', label: '#09', class_id: 'gt3', own: true, points: [[100, 2, 1], [150, 3, 2], [200, 3, 2]] },
    { id: '1', label: '#31', class_id: 'gt3', own: false, points: [[100, 3, 2], [150, 2, 1], [200, 2, 1]] },
    { id: '2', label: '#GTP', class_id: 'gtp', own: false, points: [[100, 1, 1], [200, 1, 1]] },
  ] };

test('only own class appears even with an older server returning other classes', async () => {
  render(<RacePositionChart filename="race.ibt" onClose={() => {}} load={async () => payload} />);
  await screen.findByText('GT3 · 클래스 순위');
  expect(screen.queryByText('#GTP')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /#31/ }));
  expect(screen.getByRole('button', { name: /#31/ })).toHaveAttribute('aria-pressed', 'true');
  expect(document.querySelector('[data-car="0"]')).toHaveAttribute('opacity', '0.16');
});

test('aborts a pending file request on unmount and never accepts stale data', async () => {
  let signal;
  const load = jest.fn((filename, requested) => { signal = requested; return new Promise(() => {}); });
  const view = render(<RacePositionChart filename="race.ibt" load={load} />);
  expect(signal.aborted).toBe(false);
  view.unmount();
  expect(signal.aborted).toBe(true);
});

test('error is retryable, and no data has a return action', async () => {
  const load = jest.fn().mockRejectedValueOnce(new Error('기록 접근 실패')).mockResolvedValueOnce({ status: 'UNAVAILABLE', reason: '기록 없음' });
  const close = jest.fn();
  render(<RacePositionChart filename="race.ibt" load={load} onClose={close} />);
  await screen.findByText('기록 접근 실패');
  fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));
  await screen.findByText('기록 없음');
  fireEvent.click(screen.getAllByRole('button', { name: /분석으로 돌아가기/ })[0]);
  expect(close).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

test('geometry breaks missing spans and does not extrapolate the recording', () => {
  const points = [[10, 2, 1], [20, 3, 2], [30, null, null], [50, 2, 1], [60, 2, 1]];
  expect(pointAt(points, 9)).toBeNull();
  expect(pointAt(points, 61)).toBeNull();
  expect(pointAt(points, 35)[2]).toBeNull();
  const path = positionPath(points, 2, [0, 70]);
  expect((path.match(/M/g) || []).length).toBe(2);
  expect(path).not.toContain('NaN');
});
