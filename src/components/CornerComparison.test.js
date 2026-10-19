import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import CornerComparison, { prepareComparisonTrace, projectComparisonPaths } from './CornerComparison';

jest.mock('recharts', () => {
  const actual = jest.requireActual('recharts');
  const ReactModule = jest.requireActual('react');
  return { ...actual, ResponsiveContainer: ({ children }) => ReactModule.cloneElement(children, { width: 800, height: 140 }) };
});

const trace = [0, 0.18, 0.2, 0.25, 0.3, 0.32, 1].map((distance_pct) => ({
  distance_pct,
  target_speed_kph: 100,
  reference_speed_kph: 110,
  target_brake: 0.4,
  reference_brake: 0.5,
  target_throttle: 0.2,
  reference_throttle: 0.3,
  target_steering_deg: -15,
  reference_steering_deg: -12,
  target_gear: 3,
  reference_gear: 3,
  delta_s: distance_pct,
}));
const corners = [{ id: 'C1', label: 'Corner 1', entry_pct: 0.2, apex_pct: 0.25, exit_pct: 0.3, delta_s: 0.1 }];
const pathData = trace.map((point) => ({ distance_pct: point.distance_pct, lat: 37 + point.distance_pct / 100, lon: 127 + Math.sin(point.distance_pct * Math.PI) / 100 }));

test('both GPS paths use one projection so an actual coordinate offset remains visible', () => {
  const reference = pathData.map(point => ({ ...point, lon: point.lon + .00002 }));
  const { target, reference: projected } = projectComparisonPaths(pathData, reference);
  expect(projected).toHaveLength(target.length);
  target.forEach((point, index) => {
    expect(projected[index].x).toBeGreaterThan(point.x);
    expect(projected[index].y).toBeCloseTo(point.y);
    expect(projected[index].x - point.x).toBeCloseTo(projected[0].x - target[0].x);
  });
});

test('any selected corner receives real reference detail and changing context clears it', () => {
  const reference = pathData.map(point => ({ ...point, lon: point.lon + .00002 }));
  const source = { driver_name: 'Matthew lee', lap_time: '7:53.300', chart_basis: 'AGGREGATE' };
  const base = { trace, corners, pathData, referencePath: reference, referenceLineSource: source, trackLengthM: 5000, comparisonKey: 'garage-a' };
  const { rerender } = render(<CornerComparison {...base} />);
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  expect(screen.getByTestId('map-reference-line')).toBeInTheDocument();
  expect(screen.getByTestId('map-line-source')).toHaveTextContent('Matthew lee');
  expect(screen.getByTestId('map-line-source')).toHaveTextContent('지도: 대표 실제 랩 · 그래프: 여러 랩 종합 기준');
  const detail = [{ distance_pct: .21, lat: 37.0021, lon: 127.007 }, { distance_pct: .29, lat: 37.0029, lon: 127.008 }];
  rerender(<CornerComparison {...base} detailTrace={trace.slice(1, 6)} detailPath={detail} detailReferencePath={detail.map(p => ({ ...p, lon: p.lon + .00002 }))} />);
  expect(screen.getByTestId('map-reference-line').getAttribute('points').split(' ')).toHaveLength(2);
  expect(screen.getByTestId('map-reference-cursor')).toBeInTheDocument();
  rerender(<CornerComparison {...base} referencePath={[]} referenceLineSource={null} comparisonKey="local-b" />);
  expect(screen.queryByTestId('map-reference-line')).not.toBeInTheDocument();
  expect(screen.getByTestId('map-reference-unavailable')).toBeInTheDocument();
});

test('detail with missing reference GPS replaces overview in the selected range', () => {
  const base = { trace, corners, pathData, referencePath: pathData, trackLengthM: 5000 };
  const { rerender } = render(<CornerComparison {...base} />);
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  rerender(<CornerComparison {...base} detailTrace={trace} detailReferencePath={[]} />);
  expect(screen.queryByTestId('map-reference-line')).not.toBeInTheDocument();
  expect(screen.getByTestId('map-reference-unavailable')).toBeInTheDocument();
});

test('normalizes fractions and distance while preserving explicitly unavailable readings', () => {
  const rows = prepareComparisonTrace([
    { distance_pct: 0.5, brake: 0.8, target_brake: null, throttle: 0.2, target_steering_deg: -20, reference_brake: 0, target_gear: 0 },
    { distance_pct: 0.1, brake: 0.3, steering_deg: 10 },
    { distance_pct: null, target_speed_kph: 50 },
  ], 5000);
  expect(rows).toHaveLength(2);
  expect(rows[0]).toMatchObject({ distance: 500, brake: 30, steering: 10, gear: null });
  expect(rows[1]).toMatchObject({ distance: 2500, brake: null, referenceBrake: 0, throttle: 20, steering: -20, gear: 0, referenceSteering: null, delta: null });
});

test('selecting a corner on the map zooms the shared range and the reset restores the lap', () => {
  render(<CornerComparison trace={trace} corners={corners} pathData={pathData} trackLengthM={5000} referenceLabel="Garage61 비교랩" />);
  expect(screen.getByTestId('comparison-range')).toHaveTextContent('0–5000 m');
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1 확대' }));
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('Corner 1 비교');
  expect(screen.getByTestId('comparison-range')).toHaveTextContent('925–1575 m');
  expect(screen.getByRole('button', { name: 'Corner 1', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('구간 시간 차이: +0.100초')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '전체 랩 보기' }));
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('전체 랩 비교');
  expect(screen.getByTestId('comparison-range')).toHaveTextContent('0–5000 m');
  const fullMap = screen.getByTestId('comparison-map').getAttribute('viewBox');
  fireEvent.click(screen.getByRole('button', { name: '지도 확대' }));
  expect(screen.getByTestId('comparison-map')).not.toHaveAttribute('viewBox', fullMap);
  fireEvent.click(screen.getByRole('button', { name: '전체 랩 보기' }));
  expect(screen.getByTestId('comparison-map')).toHaveAttribute('viewBox', fullMap);
});


test('the whole lap is one click away next to the corners and on the map', () => {
  render(<CornerComparison trace={trace} corners={corners} pathData={pathData} trackLengthM={5000} referenceLabel="Garage61 비교랩" />);
  expect(screen.getByRole('button', { name: '전체 랩', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('button', { name: '전체 랩으로' })).toBeNull();            // nothing to leave yet
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  expect(screen.getByRole('button', { name: '전체 랩', exact: true })).toHaveAttribute('aria-pressed', 'false');
  fireEvent.click(screen.getByRole('button', { name: '전체 랩으로' }));                 // from the map toolbar
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('전체 랩 비교');
  expect(screen.getByTestId('comparison-range')).toHaveTextContent('0–5000 m');
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '전체 랩', exact: true }));        // from the corner buttons
  expect(screen.getByTestId('comparison-range')).toHaveTextContent('0–5000 m');
});

test('corner buttons work without GPS and missing reference inputs are identified', () => {
  render(<CornerComparison trace={[{ distance_pct: 0.25, target_speed_kph: 80 }]} corners={corners} />);
  expect(screen.getByText(/GPS 좌표가 없어/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  expect(screen.getByTestId('comparison-range')).toHaveTextContent('18.0–32.0 %');
  expect(screen.getAllByText('측정 불가', { exact: false }).length).toBeGreaterThan(0);
  expect(screen.getByText(/기준 주행이 없거나 시간 데이터가 없어/)).toBeInTheDocument();
});

test('an empty analysis gives a usable explanation', () => {
  render(<CornerComparison />);
  expect(screen.getByText(/비교할 거리별 데이터가 없습니다/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '전체 랩 보기' })).toBeEnabled();
});

test('a summary corner request selects the matching segment and a new analysis clears that request', () => {
  const request = { id: 'C1' };
  const { rerender } = render(<CornerComparison trace={trace} corners={corners} trackLengthM={5000} comparisonKey="run-a" requestedCorner={request} />);
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('Corner 1 비교');
  fireEvent.click(screen.getByRole('button', { name: '전체 랩 보기' }));
  const repeatedRequest = { id: 'C1' };
  rerender(<CornerComparison trace={trace} corners={corners} trackLengthM={5000} comparisonKey="run-a" requestedCorner={repeatedRequest} />);
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('Corner 1 비교');
  rerender(<CornerComparison trace={trace} corners={[...corners]} trackLengthM={5000} comparisonKey="run-b" requestedCorner={repeatedRequest} />);
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('전체 랩 비교');
});

test('refreshing analysis keeps the selected segment and requests its new detail', () => {
  const oldLoader = jest.fn();
  const newLoader = jest.fn();
  const { rerender } = render(<CornerComparison trace={trace} corners={corners} trackLengthM={5000} comparisonKey="same-laps" onRangeChange={oldLoader} />);
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  rerender(<CornerComparison trace={trace} corners={corners} trackLengthM={5000} comparisonKey="same-laps" onRangeChange={newLoader} />);
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('Corner 1 비교');
  expect(newLoader).toHaveBeenLastCalledWith([.185, .315]);
});

test('corner selection requests 75-metre detail padding and uses returned measurements', () => {
  const onRangeChange = jest.fn();
  const { rerender } = render(<CornerComparison trace={trace} corners={corners} trackLengthM={5000} onRangeChange={onRangeChange} />);
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  expect(onRangeChange).toHaveBeenLastCalledWith([.185, .315]);
  rerender(<CornerComparison trace={trace} corners={corners} trackLengthM={5000} onRangeChange={onRangeChange} detailTrace={[{ distance_pct: .25, target_speed_kph: 123.45 }]} detailQuality={{ analysis_spacing_m: 2, target_coverage_ratio: .98 }} />);
  expect(screen.getByText('123.5 km/h')).toBeInTheDocument();
  expect(screen.getByText(/분석 간격 약 2.0m · 유효 관측 98%/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '전체 랩 보기' }));
  expect(onRangeChange).toHaveBeenLastCalledWith(null);
});

test('reference changes clear selection and aggregate uncertainty remains explicit', () => {
  const statisticalCorners = [{ ...corners[0], reference_consensus: { loss_p25_s: .02, loss_p75_s: .13, loss_agreement_ratio: .75, reference_count: 4 } }];
  const { rerender } = render(<CornerComparison trace={trace} corners={statisticalCorners} comparisonKey="local" />);
  fireEvent.click(screen.getByRole('button', { name: 'Corner 1', exact: true }));
  expect(screen.getByText(/손실 동의율 75% · 관측 기준 4랩/)).toBeInTheDocument();
  expect(screen.getByText(/공식 트랙 턴 번호가 아닙니다/)).toBeInTheDocument();
  rerender(<CornerComparison trace={trace} corners={statisticalCorners} comparisonKey="garage61" />);
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('전체 랩 비교');
});

test('long tracks show useful segments first while every segment remains selectable', () => {
  const manyCorners = Array.from({ length: 20 }, (_, index) => ({ id: `C${index + 1}`, label: `구간 ${index + 1}`, entry_pct: index / 21, apex_pct: (index + .5) / 21, exit_pct: (index + .8) / 21, delta_s: index / 10 }));
  render(<CornerComparison corners={manyCorners} />);
  expect(screen.getByRole('button', { name: '구간 20', exact: true })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '구간 1', exact: true })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '전체 20구간 보기' }));
  fireEvent.click(screen.getByRole('button', { name: '구간 1', exact: true }));
  expect(screen.getByTestId('comparison-selection')).toHaveTextContent('구간 1 비교');
});

test('the map line is coloured by where the lap loses time and the colouring can be switched off', () => {
  window.localStorage.removeItem('iracing.map.lossColour');
  const dense = Array.from({ length: 201 }, (_, i) => ({ distance_pct: i / 200, target_speed_kph: 100, reference_speed_kph: 100,
    delta_s: Math.min(.3, Math.max(0, (i - 40) / 20 * .3)) }));   // loses 0.3 s between 20% and 30% of the lap
  const path = dense.map((point) => ({ distance_pct: point.distance_pct, lat: 37 + point.distance_pct / 100, lon: 127 + Math.sin(point.distance_pct * Math.PI) / 100 }));
  render(<CornerComparison trace={dense} corners={corners} pathData={path} referencePath={path} trackLengthM={5000} />);
  const colours = screen.getAllByTestId('map-loss-run').map(run => run.getAttribute('data-colour'));
  expect(colours).toContain('rgb(239,68,68)');
  expect(colours).toContain('rgb(229,231,235)');
  expect(screen.getByTestId('map-loss-legend')).toHaveTextContent('빨강 = 잃은 곳');
  fireEvent.click(screen.getByTestId('map-loss-toggle'));
  expect(screen.queryAllByTestId('map-loss-run')).toHaveLength(0);
  expect(window.localStorage.getItem('iracing.map.lossColour')).toBe('off');
  window.localStorage.removeItem('iracing.map.lossColour');
});

test('the ghost replay puts both laps on the map at the same time since their lap start', () => {
  const timed = (lapTime, lonShift = 0) => pathData.map((point) => ({ ...point, lon: point.lon + lonShift, time_s: lapTime * point.distance_pct }));
  const onHover = jest.fn();
  render(<CornerComparison trace={trace} corners={corners} pathData={timed(100)} referencePath={timed(98, .00002)} trackLengthM={5000} />);
  expect(screen.queryByTestId('ghost-target')).toBeNull();
  fireEvent.click(screen.getByTestId('ghost-play'));
  fireEvent.click(screen.getByTestId('ghost-play'));                      // pause right away
  fireEvent.change(screen.getByTestId('ghost-time'), { target: { value: '50' } });
  expect(screen.getByTestId('ghost-target')).toBeInTheDocument();
  expect(screen.getByTestId('ghost-reference')).toBeInTheDocument();
  expect(screen.getByTestId('ghost-readout')).toHaveTextContent('0:50.0');
  expect(screen.getByTestId('ghost-readout')).toHaveTextContent('기준보다 1.00초 느림');
  fireEvent.click(screen.getByText('고스트 끄기'));
  expect(screen.queryByTestId('ghost-target')).toBeNull();
});

test('without times on the map points there is no ghost replay', () => {
  render(<CornerComparison trace={trace} corners={corners} pathData={pathData} referencePath={pathData} trackLengthM={5000} />);
  expect(screen.queryByTestId('ghost-controls')).toBeNull();
});

test('the graph cursor is one thin line laid over the graphs at the picked distance', () => {
  render(<CornerComparison trace={trace} corners={corners} pathData={pathData} trackLengthM={5000} />);
  expect(screen.queryAllByTestId('chart-cursor')).toHaveLength(0);
  fireEvent.click(screen.getAllByText('Corner 1').find((node) => node.tagName === 'BUTTON'));
  const lines = screen.getAllByTestId('chart-cursor');
  expect(lines.length).toBeGreaterThan(0);
  expect(lines[0]).toHaveAttribute('aria-hidden', 'true');
});

test('while the ghost replay runs a panel over the map shows both cars speed, gear and pedals', () => {
  const timed = (lapTime, lonShift = 0) => pathData.map((point) => ({ ...point, lon: point.lon + lonShift, time_s: lapTime * point.distance_pct }));
  const rows = [0, .25, .5, .75, 1].map((distance_pct) => ({ distance_pct, target_speed_kph: 100 + distance_pct * 100, reference_speed_kph: 110 + distance_pct * 100,
    target_throttle: 1, reference_throttle: .5, target_brake: 0, reference_brake: .2, target_gear: 4, reference_gear: 5, delta_s: distance_pct }));
  render(<CornerComparison trace={rows} corners={corners} pathData={timed(100)} referencePath={timed(98, .00002)} trackLengthM={5000} />);
  expect(screen.queryByTestId('ghost-panel')).toBeNull();
  fireEvent.click(screen.getByTestId('ghost-play'));
  fireEvent.click(screen.getByTestId('ghost-play'));
  fireEvent.change(screen.getByTestId('ghost-time'), { target: { value: '50' } });
  const panel = screen.getByTestId('ghost-panel');
  expect(panel).toHaveTextContent('내 랩150km/h4단');               // the player at half a lap
  expect(panel).toHaveTextContent('5단');
  expect(panel).toHaveTextContent('기준보다 1.00초 느림');
});
