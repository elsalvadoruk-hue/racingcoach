import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import RacePositionChart from './RacePositionChart';
import { pointAt, rankAt, lapTicks, positionPath, positionMarkers, positionX, positionY } from './racePositionGeometry';

const payload = { status: 'READY', source: 'IBT', x_axis: 'lap', start_lap: 1, end_lap: 3,
  classes: [{ id: 'gt3', label: 'GT3' }], reason: '관측 구간', series: [
    { id: '0', label: '#09', class_id: 'gt3', own: true, points: [[1, 2, 1, 100], [2, 3, 2, 150], [3, 3, 2, 200]] },
    { id: '1', label: '#31', class_id: 'gt3', own: false, points: [[1, 3, 2, 103], [2, 2, 1, 155], [3, 2, 1, 209]] },
    { id: '2', label: '#GTP', class_id: 'gtp', own: false, points: [[1, 1, 1], [3, 1, 1]] },
  ] };

const pointer = (target, type, clientX, clientY, buttons = 0) => fireEvent(target,
  Object.assign(new Event(type, { bubbles: true }), { clientX, clientY, buttons, button: 0, pointerId: 1 }));
const graphLayout = () => {
  const graph = screen.getByRole('img');
  graph.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 250 });
  return graph;
};

test('a pinned car stays highlighted while hovering another car for comparison', async () => {
  render(<RacePositionChart filename="race.ibt" load={async () => payload} />);
  const own = await screen.findByRole('button', { name: /#09/ });
  fireEvent.click(own);
  graphLayout();
  pointer(document.querySelector('[data-markers="1"]'), 'pointermove', positionX(2, [1, 3]), positionY(1));
  expect(own).toHaveAttribute('aria-pressed', 'true');
  expect(document.querySelector('[data-series="0"]')).toHaveAttribute('opacity', '1');
  expect(document.querySelector('[data-car="0"]')).toHaveAttribute('stroke-width', '1.5');
  expect(document.querySelector('[data-series="1"]')).toHaveAttribute('opacity', '1');
  expect(screen.getByText('2랩 · #31 · 1위')).toBeInTheDocument();
  expect(document.querySelector('[data-series="0"]')).toBe(document.querySelector('[data-series="0"]').parentElement.querySelector('g:last-of-type'));
});

test('only another car or all view replaces a pin, including after repeated clicks and Escape', async () => {
  render(<RacePositionChart filename="race.ibt" load={async () => payload} />);
  const own = await screen.findByRole('button', { name: /#09/ });
  const graph = graphLayout();
  fireEvent.click(own);
  fireEvent.click(own);
  expect(own).toHaveAttribute('aria-pressed', 'true');
  fireEvent.keyDown(screen.getByRole('region', { name: /순위 그래프/ }), { key: 'Escape' });
  pointer(graph, 'pointerdown', 500, 200, 1);
  pointer(graph, 'pointerup', 500, 200);
  fireEvent.pointerLeave(graph);
  expect(own).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: /#31/ }));
  expect(own).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByRole('button', { name: /#31/ })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '전체 보기' }));
  expect(screen.getByRole('button', { name: /#31/ })).toHaveAttribute('aria-pressed', 'false');
  expect(document.querySelector('[data-series="0"]')).toHaveAttribute('opacity', '1');
  expect(document.querySelector('[data-series="1"]')).toHaveAttribute('opacity', '0.72');
});

test('clicks pin the actual dot or line without a preceding hover and zoom retains the pin', async () => {
  render(<RacePositionChart filename="race.ibt" load={async () => payload} />);
  await screen.findByRole('button', { name: /#09/ });
  const graph = graphLayout();
  const rivalDot = document.querySelector('[data-markers="1"]');
  pointer(rivalDot, 'pointerdown', positionX(2, [1, 3]), positionY(1), 1);
  pointer(graph, 'pointerup', positionX(2, [1, 3]), positionY(1));
  expect(screen.getByRole('button', { name: /#31/ })).toHaveAttribute('aria-pressed', 'true');
  // At a crossing, the actual clicked path wins over nearest-series order.
  pointer(document.querySelector('[data-car="0"]'), 'pointerdown', positionX(1.5, [1, 3]), positionY(1.5), 1);
  pointer(graph, 'pointerup', positionX(1.5, [1, 3]), positionY(1.5));
  expect(screen.getByRole('button', { name: /#09/ })).toHaveAttribute('aria-pressed', 'true');
  pointer(graph, 'pointerdown', positionX(2.75, [1, 3]), positionY(2), 1);
  pointer(graph, 'pointerup', positionX(2.75, [1, 3]), positionY(2));
  expect(screen.getByRole('button', { name: /#09/ })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.pointerMove(rivalDot);
  pointer(graph, 'pointerdown', 200, 200, 1);
  pointer(graph, 'pointermove', 700, 200, 1);
  pointer(graph, 'pointerup', 700, 200);
  expect(screen.getByTestId('race-position-range')).not.toHaveTextContent('1랩 – 3랩');
  expect(screen.getByRole('button', { name: /#09/ })).toHaveAttribute('aria-pressed', 'true');
});

test('only own class appears even with an older server returning other classes', async () => {
  render(<RacePositionChart filename="race.ibt" onClose={() => {}} load={async () => ({ ...payload, source: 'IBT+LOCAL_TIMING' })} />);
  await screen.findByText('GT3 · 클래스 순위');
  expect(screen.getByText(/IBT \+ 이 PC의 경기 타이밍 기록/)).toBeInTheDocument();
  expect(screen.queryByText('#GTP')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /#31/ }));
  expect(screen.getByRole('button', { name: /#31/ })).toHaveAttribute('aria-pressed', 'true');
  expect(document.querySelector('[data-car="0"]').parentElement).toHaveAttribute('opacity', '0.16');
  expect(document.querySelector('[data-car="1"]')).toHaveAttribute('stroke-width', '1.5');
  expect(document.querySelector('[data-car="1"]').parentElement).toBe(document.querySelector('[data-car="0"]').parentElement.parentElement.querySelector('g:last-of-type'));
});

test('lap axis has one dot per observed crossing and thin diagonal segments', async () => {
  render(<RacePositionChart filename="race.ibt" onClose={() => {}} load={async () => payload} />);
  await screen.findByText('GT3 · 클래스 순위');
  expect(screen.getByTestId('race-position-range')).toHaveTextContent('1랩 – 3랩');
  expect(screen.getByRole('img')).toHaveAccessibleName(/완료 랩/);
  const own = document.querySelector('[data-car="0"]');
  expect(own.getAttribute('d')).toMatch(/L/);
  expect(own.getAttribute('d')).not.toMatch(/[HV]/);
  expect(own).toHaveAttribute('stroke-width', '1.2');
  expect(own.parentElement.querySelector('[data-markers]').getAttribute('d').match(/M/g)).toHaveLength(3);
  expect(document.querySelector('[data-car="1"]')).toHaveAttribute('stroke-width', '0.8');
});

test('a single confirmed crossing can be read by hovering its dot', async () => {
  const single = { ...payload, start_lap: 4, end_lap: 4,
    series: [{ ...payload.series[0], points: [[4, 2, 2, 200]] }] };
  render(<RacePositionChart filename="race.ibt" onClose={() => {}} load={async () => single} />);
  await screen.findByText('GT3 · 클래스 순위');
  fireEvent.pointerMove(document.querySelector('[data-markers="0"]'), { clientX: 10, clientY: 10 });
  expect(screen.getByText('4랩 · #09 · 내 차량 · 2위')).toBeInTheDocument();
});

test('endurance lap checkpoints do not create a DOM element per lap', async () => {
  const large = { ...payload, end_lap: 960,
    series: [{ ...payload.series[0], points: Array.from({ length: 960 }, (_, i) => [i + 1, 2, 2, i * 90]) }] };
  render(<RacePositionChart filename="endurance.ibt" load={async () => large} />);
  await screen.findByText('GT3 · 클래스 순위');
  expect(document.querySelector('[data-series="0"]').querySelectorAll('*').length).toBeLessThan(10);
});

test('hovering the edge of a checkpoint beside a missing lap still reads that checkpoint', async () => {
  const gaps = { ...payload, series: [{ ...payload.series[0], points: [[1, 2, 2], [1.5, null, null], [2, 1, 1], [3, 2, 2]] }] };
  render(<RacePositionChart filename="gaps.ibt" load={async () => gaps} />);
  await screen.findByText('GT3 · 클래스 순위');
  graphLayout();
  pointer(document.querySelector('[data-markers="0"]'), 'pointermove', positionX(1.998, [1, 3]), positionY(1));
  expect(screen.getByText('2랩 · #09 · 내 차량 · 1위')).toBeInTheDocument();
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
  expect(pointAt(points, 35)).toBeNull();
  expect(rankAt(points, 25)).toBeNull();
  expect(rankAt(points, 45)).toBeNull();
  expect(rankAt(points, 15)).toBe(1.5);
  expect(pointAt(points, 16)[2]).toBe(2);
  const path = positionPath(points, 2, [0, 70]);
  expect((path.match(/M/g) || []).length).toBe(2);
  expect(path).not.toContain('NaN');
  expect(positionMarkers(points, 2, [15, 55])).toBe(`M${positionX(20, [15, 55]).toFixed(2)},46.00h0M${positionX(50, [15, 55]).toFixed(2)},18.00h0`);
});

test('zoom clips the diagonal at the right rank without inventing a lap checkpoint', () => {
  const points = [[1, 3, 3], [2, 1, 1]], range = [1.25, 1.75];
  expect(positionPath(points, 2, range)).toBe(`M${positionX(1.25, range).toFixed(2)},${positionY(2.5).toFixed(2)}L${positionX(1.75, range).toFixed(2)},${positionY(1.5).toFixed(2)}`);
  expect(lapTicks([1.2, 6.8])).toEqual([2, 3, 4, 5, 6]);
  expect(lapTicks([1, 43]).every(Number.isInteger)).toBe(true);
});
