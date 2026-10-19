import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import CornerMap, { fitMapView, zoomMapView } from './CornerMap';
import library from '../data/registeredTrackMaps.json';

const points = Array.from({ length: 101 }, (_, i) => ({ distance_pct: i / 100, x: i * 5, y: 80 + Math.sin(i / 10) * 60 }));
const corner = { id: 'C17', label: '자동 구간 17', entry_pct: .45, exit_pct: .55, apex_pct: .5 };
const camera = () => screen.getByTestId('comparison-map').getAttribute('viewBox').split(' ').map(Number);

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
  [...points, ...reference].filter(p => p.distance_pct >= .45 && p.distance_pct <= .55).forEach(p => {
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
