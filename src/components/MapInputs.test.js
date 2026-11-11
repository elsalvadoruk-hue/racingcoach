import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import MapInputs from './MapInputs';

const rect = (x, y, width, height) => ({ x, y, left: x, top: y, right: x + width, bottom: y + height, width, height });
const rows = [{ distance_pct: 0, throttle: 0 }, { distance_pct: 1, throttle: 100 }];
let boundary, clip, resize, originalPointerEvent, originalObserver;
const settle = () => act(() => jest.advanceTimersByTime(30));
const position = panel => panel.getBoundingClientRect();
const drag = (panel, x, y, id = 1) => {
  const box = position(panel);
  fireEvent.pointerDown(panel, { button: 0, pointerId: id, clientX: box.x + 30, clientY: box.y + 30 });
  fireEvent.pointerMove(panel, { pointerId: id, clientX: x, clientY: y });
};

beforeEach(() => {
  jest.useFakeTimers();
  boundary = rect(100, 100, 800, 500);
  clip = rect(80, 80, 850, 550);
  originalPointerEvent = window.PointerEvent;
  originalObserver = global.ResizeObserver;
  window.PointerEvent = class extends MouseEvent {
    constructor(type, init) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  };
  global.ResizeObserver = class { constructor(callback) { resize = callback; } observe() {} disconnect() {} };
  jest.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function () {
    if (this.dataset.testid === 'input-map-boundary') return boundary;
    if (this.dataset.testid === 'input-clipping-boundary') return clip;
    if (this.dataset.testid === 'map-inputs') {
      const width = parseFloat(this.style.width) || 370;
      const height = Math.min(98, parseFloat(this.style.maxHeight) || 98);
      return rect(boundary.x + (parseFloat(this.style.left) || 0), boundary.y + (parseFloat(this.style.top) || 0), width, height);
    }
    return rect(0, 0, 0, 0);
  });
  Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: 768, configurable: true });
});

afterEach(() => {
  jest.restoreAllMocks();
  jest.useRealTimers();
  window.PointerEvent = originalPointerEvent;
  global.ResizeObserver = originalObserver;
});

function mount(handlers = {}) {
  render(<div data-testid="input-clipping-boundary" style={{ overflow: 'auto' }}>
    <div data-testid="input-map-boundary" {...handlers}>
      <MapInputs rows={rows} values={{ gear: 3 }} pct={.5} />
    </div>
  </div>);
  return screen.getByTestId('map-inputs');
}

test('dragging stays inside all four map edges and never triggers map gestures or clicks', () => {
  const gesture = jest.fn();
  const panel = mount({ onPointerDown: gesture, onPointerMove: gesture, onClick: gesture, onDoubleClick: gesture });
  const capture = jest.fn(), release = jest.fn();
  panel.setPointerCapture = capture; panel.hasPointerCapture = () => true; panel.releasePointerCapture = release;
  drag(panel, -2000, -2000);
  expect(position(panel)).toMatchObject({ left: 110, top: 110 });
  fireEvent.pointerMove(panel, { pointerId: 1, clientX: 5000, clientY: 5000 });
  expect(position(panel)).toMatchObject({ right: 890, bottom: 590 });
  fireEvent.pointerUp(panel, { pointerId: 1, clientX: 5000, clientY: 5000 });
  const stopped = position(panel);
  fireEvent.pointerMove(panel, { pointerId: 1, clientX: 100, clientY: 100 });
  fireEvent.click(panel); fireEvent.doubleClick(panel);
  expect(position(panel)).toEqual(stopped);
  expect(gesture).not.toHaveBeenCalled();
  expect(capture).toHaveBeenCalledWith(1);
  expect(release).toHaveBeenCalledWith(1);
});

test('resizing, clipping and scrolling recover the panel inside the map and visible viewport', () => {
  const panel = mount();
  drag(panel, 5000, 5000);
  fireEvent.pointerUp(panel, { pointerId: 1 });
  Object.defineProperty(window, 'innerWidth', { value: 500, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: 360, configurable: true });
  fireEvent(window, new Event('resize')); settle();
  expect(position(panel)).toMatchObject({ right: 490, bottom: 350 });
  clip = rect(80, 80, 360, 220);
  act(() => resize()); settle();
  expect(position(panel)).toMatchObject({ left: 110, right: 430, bottom: 290 });
  boundary = rect(-100, -100, 800, 500);
  fireEvent.scroll(screen.getByTestId('input-clipping-boundary')); settle();
  const moved = position(panel);
  expect(moved.left).toBeGreaterThanOrEqual(90);
  expect(moved.top).toBeGreaterThanOrEqual(90);
  expect(moved.right).toBeLessThanOrEqual(430);
  expect(moved.bottom).toBeLessThanOrEqual(290);
});

test('cancel, lost capture and focus loss stop dragging; keyboard can move and reset the panel', () => {
  const panel = mount();
  for (const event of ['pointerCancel', 'lostPointerCapture']) {
    drag(panel, 300, 300);
    const before = position(panel);
    fireEvent[event](panel, { pointerId: 1 });
    fireEvent.pointerMove(panel, { pointerId: 1, clientX: 800, clientY: 600 });
    expect(position(panel)).toEqual(before);
  }
  drag(panel, 300, 300);
  fireEvent(window, new Event('blur'));
  const before = position(panel);
  fireEvent.pointerMove(panel, { pointerId: 1, clientX: 800, clientY: 600 });
  expect(position(panel)).toEqual(before);
  fireEvent.keyDown(panel, { key: 'ArrowRight' });
  expect(position(panel).left).toBe(before.left + 10);
  fireEvent.keyDown(panel, { key: 'Home' });
  expect(position(panel)).toMatchObject({ right: 890, bottom: 590 });
});

test('insufficient map space hides the panel, cancels dragging and restores it when space returns', () => {
  const panel = mount();
  drag(panel, 400, 300);
  boundary = rect(100, 100, 250, 500);
  act(() => resize()); settle();
  expect(panel).not.toBeVisible();
  boundary = rect(100, 100, 800, 500);
  act(() => resize()); settle();
  expect(panel).toBeVisible();
  const restored = position(panel);
  fireEvent.pointerMove(panel, { pointerId: 1, clientX: 800, clientY: 600 });
  expect(position(panel)).toEqual(restored);
  Object.defineProperty(window, 'innerHeight', { value: 200, configurable: true });
  fireEvent(window, new Event('resize')); settle();
  expect(panel).not.toBeVisible();
  Object.defineProperty(window, 'innerHeight', { value: 768, configurable: true });
  fireEvent(window, new Event('resize')); settle();
  expect(panel).toBeVisible();
});
