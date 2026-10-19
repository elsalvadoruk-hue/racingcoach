import { act, render } from '@testing-library/react';
import GeographicMapTiles from './GeographicMapTiles';

const origin = { x: 0, y: 0 };
const viewport = { width: 600, height: 360 };
const hrefs = (container) => [...container.querySelectorAll('image')].map((image) => image.getAttribute('href'));

test('a camera moving every 100 ms (the ghost replay following the car) still gets the new tiles', () => {
  jest.useFakeTimers();
  const view = (x) => ({ x, y: 0, width: 600, height: 360 });
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  document.body.appendChild(svg);
  const { container, rerender } = render(<GeographicMapTiles view={view(0)} viewport={viewport} origin={origin} onStatus={() => {}} />, { container: svg });
  const first = hrefs(container);
  // move 30 m every 100 ms for 6 s: the request must not wait for the camera to stop
  for (let step = 1; step <= 60; step += 1) {
    rerender(<GeographicMapTiles view={view(step * 30)} viewport={viewport} origin={origin} onStatus={() => {}} />);
    act(() => { jest.advanceTimersByTime(100); });
  }
  const later = hrefs(container);
  expect(later).not.toEqual(first);
  expect(later.some((href) => !first.includes(href))).toBe(true);
  jest.useRealTimers();
  svg.remove();
});
