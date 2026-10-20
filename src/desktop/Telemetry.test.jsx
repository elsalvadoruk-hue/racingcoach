// SVG stroke, hit-testing and transform regressions need direct geometry access.
/* eslint-disable testing-library/no-container, testing-library/no-node-access */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { TelemetryChart, TrackMap } from './Telemetry';
import { createReplayMotion } from '../lib/replayMotion';

jest.mock('./sessionData', () => ({
  finite: Number.isFinite, length: 1000,
  number: value => Number.isFinite(value) ? String(value) : '—',
  signed: value => Number.isFinite(value) ? String(value) : '—',
}));

const rows = [.1, .2, .3, .35, .4, .5].map((pct, index) => ({
  distance_pct: pct, distance: pct * 1000, brake: 20 + index * 10, referenceBrake: 15 + index * 10,
  throttle: 15 + index * 15, referenceThrottle: 20 + index * 15,
}));
const signal = { available: true, coverage: [[0, 1]], events: [[.2, .3, .15]] };
const controls = { abs: { target: signal, reference: signal } };

test('the cursor readout stays unknown beyond observed samples within the visible axis', () => {
  const { container, rerender } = render(<TelemetryChart channel={{ key: 'brake', reference: 'referenceBrake', label: '브레이크', unit: '%' }} rows={rows} range={[0, 1000]} position={900} />);
  expect(container.querySelector('.channel-value').textContent).toBe('——');
  rerender(<TelemetryChart channel={{ key: 'brake', reference: 'referenceBrake', label: '브레이크', unit: '%' }} rows={rows} range={[0, 1000]} position={200} />);
  expect(container.querySelector('.channel-value').textContent).toBe('3025');
});
const rangeNote = { id: 1, key: 'full_throttle', channel: 'throttle', label: '풀스로틀', value: '50m 늦게',
  text: '실제 두 위치 비교', at: .35, refAt: .3, range: { from: .35, to: .3, type: 'comparison' } };

beforeAll(() => {
  window.PointerEvent = MouseEvent;
  Element.prototype.setPointerCapture = jest.fn();
  Element.prototype.hasPointerCapture = jest.fn(() => false);
  global.ResizeObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { this.callback([{ target, contentRect: { width: 1000, height: 300 } }]); }
    disconnect() {}
  };
});

function chart(channel, extra = {}) {
  return render(<TelemetryChart channel={channel} rows={rows} range={[100, 500]} position={250}
    onPosition={jest.fn()} controls={controls} {...extra} />);
}

test('steering zero remains centred for one-sided turns, reference peaks and zoomed ranges', () => {
  const channel = { key: 'steering', reference: 'referenceSteering', label: '조향', unit: '°' };
  const values = rows.map((row, i) => ({ ...row, steering: -10 - i * 10, referenceSteering: -20 - i * 12 }));
  const view = chart(channel, { rows: values });
  const middle = (8 + 280) / 2;
  expect(Number(screen.getByTestId('steering-zero-guide').getAttribute('y1'))).toBe(middle);
  const graph = screen.getByRole('img', { name: '조향 거리별 비교 그래프' });
  expect(graph).toHaveTextContent('-80080');
  for (const path of graph.querySelectorAll('[data-channel]')) {
    const ys = [...path.getAttribute('d').matchAll(/[ML][\d.]+,([\d.]+)/g)].map(match => Number(match[1]));
    expect(ys.every(value => value > middle && value < 280)).toBe(true);
  }
  view.rerender(<TelemetryChart channel={channel} rows={values.slice(2).map(row => ({ ...row, steering: 120, referenceSteering: -15 }))}
    range={[300, 500]} position={350} onPosition={jest.fn()} />);
  expect(Number(screen.getByTestId('steering-zero-guide').getAttribute('y1'))).toBe(middle);
  expect(graph).toHaveTextContent('-1200120');
});

test('ghost markers move between readout commits, stay aligned with the curve and clear on stop', () => {
  const motion = createReplayMotion();
  const channel = { key: 'brake', reference: 'referenceBrake', label: '브레이크', domain: [0, 100] };
  const { container, unmount } = chart(channel, { ghostMotion: motion, ghost: { target: 250, reference: 260 } });
  const marker = screen.getByTestId('graph-ghost-target'), dot = screen.getByTestId('graph-ghost-dot');
  const trace = container.querySelector('[data-channel="brake"]');
  const originalPath = trace.getAttribute('d');
  const positions = [];
  for (const distance of [251, 252, 253]) {
    act(() => motion.publish({ target: distance, reference: distance + 10 }));
    positions.push(Number(marker.getAttribute('x1')));
    expect(marker).toHaveAttribute('data-distance', String(distance));
    expect(dot.getAttribute('cx')).toBe(marker.getAttribute('x1'));
    expect(marker).toHaveAttribute('visibility', 'visible');
  }
  expect(positions[1]).toBeGreaterThan(positions[0]);
  expect(positions[2]).toBeGreaterThan(positions[1]);
  expect(Number(dot.getAttribute('cy'))).toBeCloseTo(280 - 35.3 / 100 * 272);
  expect(trace).toHaveAttribute('d', originalPath);
  act(() => motion.publish(null));
  expect(marker).toHaveAttribute('visibility', 'hidden');
  expect(dot).toHaveAttribute('visibility', 'hidden');
  unmount();
  expect(() => motion.publish({ target: 300, reference: 310 })).not.toThrow();
});

test('ghost markers hide outside the observed window and do not bridge missing channel samples', () => {
  const motion = createReplayMotion();
  chart({ key: 'brake', label: '브레이크', domain: [0, 100] }, {
    ghostMotion: motion, rows: rows.map(row => ({ ...row, brake: row.distance === 300 ? null : row.brake })),
  });
  act(() => motion.publish({ target: 250, reference: 600 }));
  expect(screen.getByTestId('graph-ghost-target')).toHaveAttribute('visibility', 'visible');
  expect(screen.getByTestId('graph-ghost-reference')).toHaveAttribute('visibility', 'hidden');
  expect(screen.getByTestId('graph-ghost-dot')).toHaveAttribute('visibility', 'hidden');
});

test('target ABS follows the brake curve, reference keeps its shade, and throttle never shows ABS', () => {
  const { container, rerender } = chart({ key: 'brake', reference: 'referenceBrake', label: '브레이크', domain: [0, 100] });
  expect(screen.getByTestId('target-abs-trace').getAttribute('d')).toBe(container.querySelector('[data-channel="brake"]').getAttribute('d'));
  expect(container.querySelectorAll('.abs-target-line')).toHaveLength(2);
  expect(container.querySelector('[data-control-side="target"] .control-shade')).toBeNull();
  expect(container.querySelector('[data-control-side="reference"] .control-shade')).toBeInTheDocument();
  expect(container.querySelectorAll('[data-testid="pedal-quarter-guide"]')).toHaveLength(2);
  rerender(<TelemetryChart channel={{ key: 'pedals', label: '페달', domain: [0, 100] }} rows={rows} range={[100, 500]}
    position={250} onPosition={jest.fn()} controls={controls} />);
  expect(screen.getByTestId('target-abs-trace').getAttribute('d')).toBe(container.querySelector('[data-channel="brake"]').getAttribute('d'));
  expect(screen.getByTestId('target-abs-trace').getAttribute('d')).not.toBe(container.querySelector('[data-channel="throttle"]').getAttribute('d'));
  rerender(<TelemetryChart channel={{ key: 'throttle', reference: 'referenceThrottle', label: '스로틀', domain: [0, 100] }}
    rows={rows} range={[100, 500]} position={250} onPosition={jest.fn()} controls={controls} />);
  expect(screen.queryByTestId('target-abs-trace')).not.toBeInTheDocument();
  expect(container.querySelector('[data-control-kind]')).toBeNull();
});

test('range decorations preserve drag zoom and expose separate target/reference landmarks', () => {
  const onRange = jest.fn(), onNote = jest.fn();
  const { container } = chart({ key: 'throttle', reference: 'referenceThrottle', label: '스로틀', domain: [0, 100] },
    { notes: [rangeNote], onRange, onNote });
  const range = screen.getByTestId('comparison-range');
  expect(range).toHaveAttribute('pointer-events', 'none');
  expect(range.querySelector('.note-range-target')).toBeInTheDocument();
  expect(range.querySelector('.note-range-reference')).toBeInTheDocument();
  const graph = screen.getByRole('img', { name: '스로틀 거리별 비교 그래프' });
  fireEvent.pointerDown(graph, { button: 0, clientX: 200, pointerId: 1 });
  fireEvent.pointerUp(graph, { button: 0, clientX: 500, pointerId: 1 });
  expect(onRange).toHaveBeenCalledTimes(1);
  expect(onRange.mock.calls[0][0][1]).toBeGreaterThan(onRange.mock.calls[0][0][0]);
  expect(onNote).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '1. 풀스로틀: 50m 늦게' }));
  expect(onNote).toHaveBeenCalledWith(rangeNote);
  expect(container.querySelectorAll('[data-note-range]')).toHaveLength(1);
});

test('map rotation changes GPS positions while labels stay upright and fixed in screen size when zoomed', () => {
  const points = [
    { x: 0, y: 0, distance_pct: 0 }, { x: 20, y: 10, distance_pct: .25 },
    { x: 10, y: 40, distance_pct: .5 }, { x: -10, y: 15, distance_pct: .75 },
  ];
  const corners = [{ id: 'C1', entry_pct: .1, apex_pct: .25, exit_pct: .4 }];
  const onSelect = jest.fn();
  const { container } = render(<TrackMap points={points} corners={corners} onSelect={onSelect} large rotatable />);
  const map = screen.getByRole('group', { name: '자동 구간 번호 선택 지도' });
  const pin = screen.getByRole('button', { name: 'C1 선택' });
  const before = pin.getAttribute('transform');
  fireEvent.pointerDown(map, { button: 0, ctrlKey: true, clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent.pointerMove(map, { button: 0, ctrlKey: true, clientX: 100, clientY: 40, pointerId: 1 });
  fireEvent.pointerUp(map, { button: 0, pointerId: 1 });
  expect(container.querySelector('.track-map')).toHaveAttribute('data-rotation', '-30');
  expect(pin.getAttribute('transform')).not.toBe(before);
  expect(pin.getAttribute('transform')).not.toMatch(/rotate/);
  expect(pin.querySelector('text')).toHaveTextContent('1');
  expect(pin.querySelector('text')).not.toHaveAttribute('transform');
  fireEvent.click(screen.getByRole('button', { name: '자동 구간 지도 확대' }));
  const width = Number(map.getAttribute('viewBox').split(' ')[2]);
  expect(width).toBeLessThan(1000);
  expect(pin.getAttribute('transform')).toContain(`scale(${width / 1000})`);
  fireEvent.keyDown(pin, { key: 'Enter' });
  expect(onSelect).toHaveBeenCalledWith('C1');
});
