import { act, renderHook, waitFor } from '@testing-library/react';
import useAudioHost from './audioHost';

// A minimal exclusive Web Locks manager: one holder, the rest wait in order.
function fakeLocks() {
  let held = false;
  const queue = [];
  const grant = () => {
    if (held || !queue.length) return;
    held = true;
    const { callback, resolve } = queue.shift();
    Promise.resolve(callback()).then((value) => { held = false; resolve(value); grant(); });
  };
  return { request: (name, callback) => new Promise((resolve) => { queue.push({ callback, resolve }); grant(); }) };
}

describe('one tab speaks', () => {
  const original = navigator.locks;
  afterEach(() => { Object.defineProperty(navigator, 'locks', { value: original, configurable: true }); });

  test('the second tab stands by and takes over when the first closes', async () => {
    Object.defineProperty(navigator, 'locks', { value: fakeLocks(), configurable: true });
    const first = renderHook(() => useAudioHost());
    await waitFor(() => expect(first.result.current).toBe(true));
    const second = renderHook(() => useAudioHost());
    await act(async () => {});
    expect(second.result.current).toBe(false);
    first.unmount();
    await waitFor(() => expect(second.result.current).toBe(true));
  });

  test('without the lock API every tab speaks, as before', () => {
    Object.defineProperty(navigator, 'locks', { value: undefined, configurable: true });
    const { result } = renderHook(() => useAudioHost());
    expect(result.current).toBe(true);
  });
});
