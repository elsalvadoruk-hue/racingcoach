import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import CornerMap, { fitMapView, startFinish, zoomMapView } from './CornerMap';
import library from '../data/registeredTrackMaps.json';

const points = Array.from({ length: 101 }, (_, i) => ({ distance_pct: i / 100, x: i * 5, y: 80 + Math.sin(i / 10) * 60 }));
const corner = { id: 'C17', label: '자동 구간 17', entry_pct: .45, exit_pct: .55, apex_pct: .5 };
const camera = () => screen.getByTestId('comparison-map').getAttribute('viewBox').split(' ').map(Number);

test('segment ghosts discard earlier lap deficits, keep local pace and reset for an equal-duration different segment', () => {
  const target = points.map(p => ({ ...p, time_s: 100 * p.distance_pct + 35 }));
  const reference = points.map(p => ({ ...p, time_s: 98 * p.distance_pct }));
  const props = { projected: target, referenceProjected: reference, corners: [corner], selected: corner, onSelect: jest.fn(), onHover: jest.fn() };
  const { rerender } = render(<CornerMap {...props} selectedRange={[.4, .6]} />);
  fireEvent.click(screen.getByTestId('ghost-play'));
  expect(screen.getByTestId('ghost-time')).toHaveAttribute('min', '0');
  expect(Number(screen.getByTestId('ghost-target').getAttribute('cx'))).toBeCloseTo(200);
  expect(Number(screen.getByTestId('ghost-reference').getAttribute('cx'))).toBeCloseTo(200);
  fireEvent.change(screen.getByTestId('ghost-time'), { target: { value: '10' } });
  expect(Number(screen.getByTestId('ghost-target').getAttribute('cx'))).toBeCloseTo(250);
  expect(Number(screen.getByTestId('ghost-reference').getAttribute('cx'))).toBeGreaterThan(250);
  expect(screen.getByTestId('ghost-readout')).toHaveTextContent('기준보다 0.20초 느림');
  rerender(<CornerMap {...props} selectedRange={[.6, .8]} />);
  expect(screen.queryByTestId('ghost-target')).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId('ghost-play'));
  expect(Number(screen.getByTestId('ghost-target').getAttribute('cx'))).toBeCloseTo(300);
  expect(Number(screen.getByTestId('ghost-reference').getAttribute('cx'))).toBeCloseTo(300);
});

test('optional compact controls join playback and map actions, retain visible imagery credit and move explanations below the map', () => {
  const timed = points.map(point => ({ ...point, time_s: point.distance_pct * 100 }));
  render(<CornerMap projected={timed} referenceProjected={timed} geographic geographicMetersPerUnit={1} corners={[corner]} selected={corner} selectedRange={[.4, .6]} onSelect={jest.fn()} onHover={jest.fn()} compactControls />);
  const zoom = screen.getByRole('button', { name: '지도 확대' });
  expect(zoom.closest('[data-testid="ghost-button-row"]')).toContainElement(screen.getByTestId('ghost-play'));
  const source = screen.getByTestId('map-background-source');
  expect(source.closest('.compact-map-footer')).not.toBeNull();
  expect(source).toHaveTextContent('Esri');
  expect(screen.getByTestId('map-line-source').closest('details')).not.toHaveAttribute('open');
  const before = camera(); fireEvent.click(zoom); expect(camera()).not.toEqual(before);
});

test('LMU schematic removes satellite controls and every imagery layer even with geographic and registered map inputs', () => {
  const map = library.maps.find(m => m.trackId === 127);
  render(<CornerMap schematic projected={points} referenceProjected={points} trackMap={map} geographic geographicMetersPerUnit={1} corners={[corner]} onSelect={jest.fn()} onHover={jest.fn()} compactControls />);
  expect(screen.queryByRole('radio', { name: '위성 지도' })).not.toBeInTheDocument();
  expect(screen.queryByTestId('map-geographic-background')).not.toBeInTheDocument();
  expect(screen.queryByTestId('map-satellite-background')).not.toBeInTheDocument();
  expect(screen.queryByTestId('map-road-background')).not.toBeInTheDocument();
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('기록 좌표 개략도');
  expect(screen.getByTestId('map-background-source')).not.toHaveTextContent('Esri');
  const line = screen.getByTestId('map-reference-line').getAttribute('points');
  const before = camera();
  fireEvent.click(screen.getByRole('button', { name: '지도 확대' }));
  expect(camera()).not.toEqual(before);
  expect(screen.getByTestId('map-reference-line')).toHaveAttribute('points', line);
});

test('global imagery failure preserves both actual GPS lines and can be removed without changing the camera', () => {
  render(<CornerMap projected={points} referenceProjected={points.map(p => ({ ...p, y: p.y + 10 }))} geographic geographicMetersPerUnit={1} corners={[corner]} selected={corner} selectedRange={[.4, .6]} onSelect={jest.fn()} onHover={jest.fn()} />);
  const tileImages = screen.getByTestId('map-geographic-background').querySelectorAll('image');
  expect(tileImages.length).toBeGreaterThan(0);
  const before = camera();
  const line = screen.getByTestId('map-reference-line').getAttribute('points');
  tileImages.forEach(image => fireEvent.error(image));
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('위성 배경 연결 실패');
  expect(screen.getByTestId('map-reference-line')).toHaveAttribute('points', line);
  fireEvent.click(screen.getByRole('radio', { name: 'GPS 라인 지도' }));
  expect(screen.queryByTestId('map-geographic-background')).not.toBeInTheDocument();
  expect(camera()).toEqual(before);
  expect(screen.getByTestId('map-reference-line')).toHaveAttribute('points', line);
});

test('official course diagrams use their own camera and never invent a GPS overlay', () => {
  const course = { trackId: 262, displayName: 'VLN', viewBox: [0, 0, 1920, 1080], assets: { active: '/track-maps/official/262/active.svg' } };
  render(<CornerMap projected={points} referenceProjected={points} courseDiagram={course} corners={[corner]} selected={corner} selectedRange={[.4, .6]} onSelect={jest.fn()} onHover={jest.fn()} />);
  const before = camera();
  const line = screen.getByTestId('map-reference-line').getAttribute('points');
  fireEvent.click(screen.getByRole('radio', { name: '전체 코스 안내 보기' }));
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('GPS 미정렬');
  expect(screen.getByTestId('map-course-active')).toHaveAttribute('href', course.assets.active);
  expect(screen.queryByTestId('map-reference-line')).not.toBeInTheDocument();
  expect(screen.getByTestId('comparison-map').querySelector('polyline')).toBeNull();
  expect(camera()[2]).toBeGreaterThan(before[2]);
  fireEvent.click(screen.getByRole('radio', { name: '주행선 비교 보기' }));
  expect(camera()).toEqual(before);
  expect(screen.getByTestId('map-reference-line')).toHaveAttribute('points', line);
});

test('official course diagrams can be viewed even when telemetry has no GPS', () => {
  const course = { trackId: 262, displayName: 'VLN', viewBox: [0, 0, 1920, 1080], assets: { active: '/track-maps/official/262/active.svg' } };
  render(<CornerMap projected={[]} courseDiagram={course} corners={[corner]} onSelect={jest.fn()} onHover={jest.fn()} />);
  expect(screen.getByTestId('map-course-active')).toBeInTheDocument();
  expect(screen.getByRole('radio', { name: '주행선 비교 보기' })).toBeDisabled();
  expect(screen.queryByTestId('map-distance-scale')).not.toBeInTheDocument();
});

test('registered road and satellite layers share one frame and switching layers leaves both driving lines intact', () => {
  const map = library.maps.find(m => m.trackId === 127);
  render(<CornerMap projected={points} referenceProjected={points.map(p => ({ ...p, y: p.y + 10 }))} trackMap={map} corners={[corner]} selected={corner} selectedRange={[.4, .6]} onSelect={jest.fn()} onHover={jest.fn()} />);
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('DZ replay');
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('7.4 m');
  const road = screen.getByTestId('map-road-background');
  expect(road).toHaveAttribute('x', String(map.viewBox[0]));
  expect(road).toHaveAttribute('width', String(map.viewBox[2]));
  const originalCamera = camera();
  const line = screen.getByTestId('map-reference-line').getAttribute('points');
  fireEvent.click(screen.getByRole('radio', { name: '위성 지도' }));
  expect(screen.getByTestId('map-satellite-background')).toHaveAttribute('href', map.assets.background);
  expect(camera()).toEqual(originalCamera);
  expect(screen.getByTestId('map-reference-line')).toHaveAttribute('points', line);
  expect(screen.getByTestId('map-distance-scale')).toBeInTheDocument();
  fireEvent.error(screen.getByTestId('map-satellite-background'));
  expect(screen.queryByTestId('map-satellite-background')).not.toBeInTheDocument();
  expect(road).toBeInTheDocument();
  fireEvent.error(road);
  expect(screen.queryByTestId('map-road-background')).not.toBeInTheDocument();
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('GPS 라인 비교');
  expect(screen.getByTestId('map-reference-line')).toHaveAttribute('points', line);
});

test('unsupported layouts explain the missing background while preserving GPS comparison', () => {
  render(<CornerMap projected={points} referenceProjected={points} corners={[corner]} onSelect={jest.fn()} onHover={jest.fn()} />);
  expect(screen.getByTestId('map-background-source')).toHaveTextContent('등록된 도로 지도가 없습니다');
  expect(screen.queryByRole('radio', { name: '위성 지도' })).not.toBeInTheDocument();
  expect(screen.getByTestId('map-reference-line')).toBeInTheDocument();
});

test('fits both actual lines and never draws across reference GPS holes', () => {
  const reference = points.map((point, index) => ({ ...point, y: point.y + 80, breakBefore: index === 51 }));
  render(<CornerMap projected={points} referenceProjected={reference} corners={[corner]} selected={corner} selectedRange={[.4, .6]} activePct={.5} onSelect={jest.fn()} onHover={jest.fn()} />);
  const [x, y, width, height] = camera();
  [...points, ...reference].filter(p => p.distance_pct >= .4 && p.distance_pct <= .6).forEach(p => {
    expect(p.x).toBeGreaterThanOrEqual(x);
    expect(p.x).toBeLessThanOrEqual(x + width);
    expect(p.y).toBeGreaterThanOrEqual(y);
    expect(p.y).toBeLessThanOrEqual(y + height);
  });
  expect(screen.getAllByTestId('map-reference-line')).toHaveLength(2);
  expect(screen.getByTestId('map-reference-cursor')).toHaveAttribute('cy', String(reference[50].y));
});

test('selected corner fills the map and manual zoom survives new detail until refitted', () => {
  const base = { projected: points, corners: [corner], onSelect: jest.fn(), onHover: jest.fn(), selectedRange: [.4, .6] };
  const { rerender } = render(<CornerMap {...base} />);
  const whole = camera();
  rerender(<CornerMap {...base} selected={corner} />);
  const fit = camera();
  expect(fit[2]).toBeLessThan(whole[2] / 3);
  fireEvent.click(screen.getByRole('button', { name: '지도 확대' }));
  const zoomed = camera();
  expect(zoomed[2]).toBeLessThan(fit[2]);
  rerender(<CornerMap {...base} projected={points.map(p => ({ ...p }))} selected={corner} />);
  expect(camera()).toEqual(zoomed);
  fireEvent.click(screen.getByRole('button', { name: '선택 구간 맞춤' }));
  expect(camera()).toEqual(fit);
  fireEvent.click(screen.getByRole('button', { name: '전체 지도' }));
  expect(camera()).toEqual(whole);
  rerender(<CornerMap {...base} selected={corner} focusVersion={1} />);
  expect(camera()).toEqual(fit);
});

test('zoom holds the cursor at the same screen position and clamps extremes', () => {
  const view = { x: 10, y: 20, width: 600, height: 360 };
  const anchor = { x: 190, y: 110 };
  const zoom = zoomMapView(view, .5, anchor, 600);
  expect((anchor.x - zoom.x) / zoom.width).toBeCloseTo((anchor.x - view.x) / view.width);
  expect((anchor.y - zoom.y) / zoom.height).toBeCloseTo((anchor.y - view.y) / view.height);
  expect(zoomMapView(view, 1e-12, anchor, 600).width).toBeGreaterThan(0);
  expect(zoomMapView(view, 1e12, anchor, 600).width).toBe(2400);
});

test('portrait maps retain their aspect and keyboard navigation can recover a moved camera', () => {
  const view = fitMapView(points, .8);
  expect(view.width / view.height).toBeCloseTo(.8);
  render(<CornerMap projected={points} corners={[corner]} selected={corner} selectedRange={[.4, .6]} onSelect={jest.fn()} onHover={jest.fn()} />);
  const fit = camera();
  const map = screen.getByTestId('comparison-map');
  fireEvent.keyDown(map, { key: 'ArrowRight' });
  expect(camera()[0]).toBeGreaterThan(fit[0]);
  fireEvent.keyDown(map, { key: 'Home' });
  expect(camera()).toEqual(fit);
});

test('a chequered start/finish mark with the direction of travel covers where the lap line starts and ends', () => {
  const straight = [0, .001, .002, .005, .5].map((distance_pct, i) => ({ distance_pct, x: [0, 2, 4, 10, 300][i], y: 0 }));
  expect(startFinish(straight)).toEqual({ x: 0, y: 0, angle: 0 });
  const north = straight.map((point) => ({ ...point, x: 0, y: -point.x }));
  expect(startFinish(north).angle).toBeCloseTo(-90);
  expect(startFinish(straight.slice(4))).toBeNull();             // the lap's start was not measured
  render(<CornerMap projected={points} geographic geographicMetersPerUnit={1} corners={[]} onSelect={jest.fn()} onHover={jest.fn()} />);
  expect(screen.getByTestId('map-start-finish').getAttribute('transform')).toMatch(/^translate\(0 80\) rotate\(/);
});
