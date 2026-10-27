import React from 'react';
import { renderHook } from '@testing-library/react';
import { useDriveEndNavigation } from './useDriveEndNavigation';

const idle = { lifecycle_id: 'backend-1', completed_serial: 0, phase: 'IDLE', active_drive: false, last_completed: null };
const ended = (serial = 1, game = 'iracing') => ({ ...idle, completed_serial: serial, phase: 'ENDED',
  last_completed: { serial, game, reason: 'OFF_TRACK', drive_id: `drive-${serial}` } });
let navigate;
beforeEach(() => { navigate = jest.fn(); window.addEventListener('coach-navigate', navigate); });
afterEach(() => window.removeEventListener('coach-navigate', navigate));

function Navigation({ status, game }) { return useDriveEndNavigation(status, game); }
function useNavigation(status = idle, game = 'iracing') {
  return renderHook(Navigation, { initialProps: { status, game } });
}

test('a newly confirmed drive end navigates once through the existing desktop route', () => {
  const { rerender } = useNavigation();
  rerender({ status: { ...idle, phase: 'DRIVING', active_drive: true }, game: 'iracing' });
  rerender({ status: { ...idle, phase: 'ENDING', active_drive: true }, game: 'iracing' });
  expect(navigate).not.toHaveBeenCalled();
  rerender({ status: ended(), game: 'iracing' });
  expect(navigate).toHaveBeenCalledTimes(1);
  expect(navigate.mock.calls[0][0].detail).toBe('summary');
  rerender({ status: ended(), game: 'iracing' });
  expect(navigate).toHaveBeenCalledTimes(1);
  rerender({ status: ended(2), game: 'iracing' });
  expect(navigate).toHaveBeenCalledTimes(2);
});

test('opening or remounting the app never replays an earlier completion', () => {
  const view = useNavigation(ended(3));
  expect(navigate).not.toHaveBeenCalled();
  view.unmount();
  useNavigation(ended(3));
  expect(navigate).not.toHaveBeenCalled();
});

test('unknown, disconnected and replay states without a completion cannot move the page', () => {
  const { rerender } = useNavigation();
  for (const phase of ['DRIVING', 'ENDING', 'UNKNOWN', 'IDLE']) {
    rerender({ status: { ...idle, phase, active_drive: true }, game: 'iracing' });
  }
  rerender({ status: undefined, game: 'iracing' });
  expect(navigate).not.toHaveBeenCalled();
  rerender({ status: ended(), game: 'iracing' });
  expect(navigate).toHaveBeenCalledTimes(1);
});

test('another game completion is consumed without interrupting the selected game', () => {
  const { rerender } = useNavigation();
  rerender({ status: ended(1, 'lmu'), game: 'iracing' });
  rerender({ status: ended(1, 'lmu'), game: 'lmu' });
  expect(navigate).not.toHaveBeenCalled();
  rerender({ status: ended(2, 'lmu'), game: 'lmu' });
  expect(navigate).toHaveBeenCalledTimes(1);
});

test('backend restarts, stale responses and a new active drive do not replay old ends', () => {
  const { rerender } = useNavigation(ended(3));
  rerender({ status: ended(1), game: 'iracing' });
  rerender({ status: ended(3), game: 'iracing' });
  rerender({ status: { ...ended(4), active_drive: true, phase: 'UNKNOWN' }, game: 'iracing' });
  rerender({ status: ended(4), game: 'iracing' });
  expect(navigate).not.toHaveBeenCalled();
  const restarted = { ...ended(1), lifecycle_id: 'backend-2' };
  rerender({ status: restarted, game: 'iracing' });
  expect(navigate).not.toHaveBeenCalled();
  rerender({ status: { ...restarted, ...ended(2), lifecycle_id: 'backend-2' }, game: 'iracing' });
  expect(navigate).toHaveBeenCalledTimes(1);
});

test('StrictMode and repeated status renders deliver the completion once', () => {
  const { rerender } = renderHook(({ status }) => useDriveEndNavigation(status, 'iracing'), {
    initialProps: { status: idle }, wrapper: ({ children }) => <React.StrictMode>{children}</React.StrictMode>,
  });
  rerender({ status: ended() });
  rerender({ status: ended() });
  expect(navigate).toHaveBeenCalledTimes(1);
});

test('malformed observations leave the valid baseline intact and mismatched ends are consumed', () => {
  const { rerender } = useNavigation();
  for (const status of [null, {}, { ...ended(), completed_serial: NaN }, { ...ended(), completed_serial: -1 },
    { ...ended(), completed_serial: 1.5 }, { ...ended(), lifecycle_id: null }]) {
    rerender({ status, game: 'iracing' });
  }
  expect(navigate).not.toHaveBeenCalled();
  rerender({ status: ended(), game: 'iracing' });
  expect(navigate).toHaveBeenCalledTimes(1);
  rerender({ status: { ...ended(2), last_completed: { serial: 1, game: 'iracing' } }, game: 'iracing' });
  rerender({ status: ended(2), game: 'iracing' });
  rerender({ status: { ...ended(3), active_drive: undefined }, game: 'iracing' });
  rerender({ status: ended(3), game: 'iracing' });
  expect(navigate).toHaveBeenCalledTimes(1);
});
