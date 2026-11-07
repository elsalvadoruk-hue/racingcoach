// Direct geometry assertions verify cursor placement, wheel direction and pedal heights.
/* eslint-disable testing-library/no-node-access, testing-library/no-container */
import React, { useRef } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import InputHover from './InputHover';

const samples = [
  { distance: 100, steering: 0, referenceSteering: 0, brake: 0, referenceBrake: 100, throttle: 100, referenceThrottle: 0 },
  { distance: 200, steering: 36, referenceSteering: 29, brake: 40, referenceBrake: 60, throttle: 70, referenceThrottle: 80 },
  { distance: 300, steering: -45, referenceSteering: null, brake: 100, referenceBrake: 0, throttle: 0, referenceThrottle: 100 },
  { distance: 500, steering: 450, referenceSteering: -10, brake: null, referenceBrake: 25, throttle: null, referenceThrottle: null },
];
function Harness({ channelKey = 'steering', unit = '°', rows = samples, range = [100, 500] }) {
  const host = useRef(null);
  return <><div ref={host} data-testid="hover-source"><svg data-testid="hover-plot"><g role="button" data-testid="hover-note" /></svg></div>
    <InputHover host={host} channelKey={channelKey} unit={unit} rows={rows} range={range} width={1000} height={100} /></>;
}
function setup(props = {}, { left = 20, top = 40 } = {}) {
  const view = render(<Harness {...props} />);
  jest.spyOn(screen.getByTestId('hover-source'), 'getBoundingClientRect').mockReturnValue({ left, top, width: 1000, height: 100 });
  const move = (distance = 200, extras = {}) => {
    fireEvent.pointerMove(screen.getByTestId('hover-plot'), { clientX: left + 40 + (distance - 100) / 400 * 948, clientY: top + 20, ...extras });
    act(() => jest.advanceTimersByTime(20));
  };
  return { ...view, move };
}
beforeAll(() => { window.PointerEvent = MouseEvent; });
beforeEach(() => {
  jest.useFakeTimers();
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 900 });
});
afterEach(() => { cleanup(); jest.useRealTimers(); jest.restoreAllMocks(); });

test('small wheel appears only over its graph and rotates left for positive iRacing angles', () => {
  const { move } = setup();
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  move();
  const popup = screen.getByRole('img', { name: '조향 내 랩 36°, 비교 기준 29°' });
  expect(popup).toHaveStyle({ width: '70px', height: '70px' });
  expect(screen.getByTestId('hover-wheel-image')).toHaveAttribute('transform', 'rotate(-36 35 35)');
  const reference = screen.getByTestId('hover-steering-reference');
  expect(Number(reference.getAttribute('x1'))).toBeLessThan(35);
  expect(Number(reference.getAttribute('y1'))).toBeLessThan(35);
  const left = parseFloat(popup.style.left);
  move(300);
  expect(parseFloat(popup.style.left)).toBeGreaterThan(left);
  expect(screen.getByTestId('hover-wheel-image')).toHaveAttribute('transform', 'rotate(45 35 35)');
  expect(screen.queryByTestId('hover-steering-reference')).not.toBeInTheDocument();
  fireEvent.pointerLeave(screen.getByTestId('hover-source'));
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
});

test('zero and multiple revolutions retain measured degrees instead of normalizing the number', () => {
  const { move } = setup();
  move(100);
  expect(screen.getByTestId('hover-steering')).toHaveTextContent('0°');
  expect(screen.getByTestId('hover-steering-reference')).toBeInTheDocument();
  move(500);
  expect(screen.getByTestId('hover-steering')).toHaveTextContent('450°');
  expect(screen.getByTestId('hover-wheel-image')).toHaveAttribute('transform', 'rotate(-450 35 35)');
});

test.each(['brake', 'throttle'])('%s bar fills upward and its reference stays independent', channelKey => {
  const { move } = setup({ channelKey, unit: '%' });
  move(100);
  expect(screen.getByTestId('input-hover').querySelectorAll('svg')).toHaveLength(1);
  expect(screen.getByTestId(`hover-${channelKey}-fill`)).toHaveAttribute('height', channelKey === 'brake' ? '0' : '54');
  expect(screen.getByTestId(`hover-${channelKey}-reference`)).toHaveAttribute('y1', channelKey === 'brake' ? '3' : '57');
  move(300);
  expect(screen.getByTestId(`hover-${channelKey}-fill`)).toHaveAttribute('height', channelKey === 'brake' ? '54' : '0');
  expect(screen.getByTestId(`hover-${channelKey}-reference`)).toHaveAttribute('y1', channelKey === 'brake' ? '57' : '3');
});

test('combined pedals show brake and throttle side by side using their separate samples', () => {
  const { move } = setup({ channelKey: 'pedals', unit: '%' });
  move();
  const popup = screen.getByTestId('input-hover');
  expect(popup).toHaveAccessibleName('브레이크 내 랩 40%, 비교 기준 60% · 스로틀 내 랩 70%, 비교 기준 80%');
  expect(popup.querySelectorAll('[data-pedal]')).toHaveLength(2);
  expect(screen.getByTestId('hover-brake-fill')).toHaveAttribute('height', '21.6');
  expect(Number(screen.getByTestId('hover-throttle-fill').getAttribute('height'))).toBeCloseTo(37.8);
});

test('missing samples are unknown, reference-only bars survive and no extrapolation is shown', () => {
  const { move, rerender } = setup({ channelKey: 'pedals', unit: '%' });
  move(500);
  expect(screen.getByTestId('input-hover')).toHaveAccessibleName(/브레이크 내 랩 확인 불가, 비교 기준 25%/);
  expect(screen.queryByTestId('hover-brake-fill')).not.toBeInTheDocument();
  expect(screen.getByTestId('hover-brake-reference')).toBeInTheDocument();
  expect(screen.queryByTestId('hover-throttle-reference')).not.toBeInTheDocument();
  rerender(<Harness channelKey="steering" rows={samples.map(row => ({ ...row, steering: null, referenceSteering: null }))} />);
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  move();
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  rerender(<Harness rows={samples.slice(1)} />);
  move(100);
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
});

test.each(['speed', 'gear', 'delta'])('%s does not acquire an input indicator', channelKey => {
  const { move } = setup({ channelKey });
  move();
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
});

test('dragging, graph notes, axes and touch do not activate the indicator', () => {
  const { move } = setup();
  move();
  fireEvent.pointerDown(screen.getByTestId('hover-plot'), { button: 0 });
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  move(200, { buttons: 1 });
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  fireEvent.pointerMove(screen.getByTestId('hover-note'), { clientX: 300, clientY: 60 });
  act(() => jest.advanceTimersByTime(20));
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  move(200, { clientX: 25 });
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  move(200, { clientY: 135 });
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  const touch = new MouseEvent('pointermove', { bubbles: true, clientX: 300, clientY: 60 });
  Object.defineProperty(touch, 'pointerType', { value: 'touch' });
  fireEvent(screen.getByTestId('hover-plot'), touch);
  act(() => jest.advanceTimersByTime(20));
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
});

test('Escape, scroll, focus loss, resize and changing the graph clear stale indicators', () => {
  const { move, rerender, unmount } = setup();
  for (const clear of [() => fireEvent.keyDown(document, { key: 'Escape' }), () => fireEvent.scroll(window),
    () => fireEvent.blur(window), () => fireEvent.resize(window), () => fireEvent.focusIn(document.body), () => fireEvent(document, new Event('visibilitychange'))]) {
    move();
    expect(screen.getByTestId('input-hover')).toBeInTheDocument();
    clear();
    expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  }
  move();
  rerender(<Harness range={[200, 500]} />);
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
  move();
  unmount();
  act(() => jest.advanceTimersByTime(30));
  expect(screen.queryByTestId('input-hover')).not.toBeInTheDocument();
});

test('the indicator flips away from viewport edges without changing its small size', () => {
  const { move } = setup({}, { left: 200, top: 825 });
  move(500);
  const popup = screen.getByTestId('input-hover');
  expect(parseFloat(popup.style.left)).toBeLessThan(1188);
  expect(parseFloat(popup.style.top)).toBeLessThan(845);
  expect(parseFloat(popup.style.left) + 70).toBeLessThanOrEqual(1200);
  expect(parseFloat(popup.style.top) + 70).toBeLessThanOrEqual(900);
});

test('LMU percent input is not invented as a physical wheel angle', () => {
  const { move } = setup({ unit: '%' });
  move();
  expect(screen.getByTestId('input-hover')).toHaveAccessibleName(/36%.*핸들 회전각 확인 불가/);
  expect(screen.getByTestId('hover-wheel-image')).toHaveAttribute('transform', 'rotate(0 35 35)');
  expect(screen.queryByTestId('hover-steering-reference')).not.toBeInTheDocument();
});
