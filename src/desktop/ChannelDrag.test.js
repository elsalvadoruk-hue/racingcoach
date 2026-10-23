import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import CornerLab from './CornerLab';
import { activateSession, selectGame } from './sessionData';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./RuntimePreview', () => ({ useLabControls: () => ({}), RuntimeControls: () => null }));
jest.mock('../components/CornerMap', () => () => null);
jest.mock('./WorkspaceNotices', () => () => null);
jest.mock('../components/LapOverlay', () => ({ __esModule: true, default: () => null, PanBar: () => null }));

const corner = { id: 'C1', entry_pct: .1, apex_pct: .2, exit_pct: .3, target: {}, reference: {} };
const session = { filename: 'fixture.ibt', defaultTarget: 2, defaultReference: 1,
  session: { track_name: 'Fixture', track_length_m: 5000 }, laps: [], corners: [corner],
  initialComparison: { corners: [corner], trace: [], track_path: [], reference_track_path: [], controlEvents: {} } };

beforeAll(() => {
  window.PointerEvent = class extends MouseEvent {
    constructor(type, init = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  };
  Element.prototype.setPointerCapture = jest.fn();
  global.ResizeObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { this.callback([{ target, contentRect: { width: 1000, height: 100 } }]); }
    disconnect() {}
  };
});

async function workspace(game) {
  localStorage.clear(); window.history.replaceState(null, '', '#corners');
  selectGame(game); activateSession({ ...session, provider: game === 'lmu' ? 'LMU' : 'IRACING' });
  requestJson.mockResolvedValue({ files: [] });
  render(<CornerLab game={game} analysisWorkspace={{ game, adoptDesktopAnalysis: jest.fn() }} />);
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
}

afterEach(() => {
  selectGame('iracing'); window.history.replaceState(null, '', '/');
  jest.restoreAllMocks();
});

test.each(['iracing', 'lmu'])('a missed release cannot turn the next graph zoom into a channel reorder (%s)', async game => {
  await workspace(game);
  const label = screen.getByRole('button', { name: '브레이크 순서 이동' });
  const graph = screen.getByRole('img', { name: '스로틀 거리별 비교 그래프' });
  const row = graph.closest('[data-channel-row]');
  document.elementFromPoint = jest.fn(() => row);
  fireEvent.pointerDown(label, { button: 0, buttons: 1, pointerId: 1, clientY: 100 });
  expect(label).toHaveAttribute('aria-pressed', 'true');
  // The original release was not delivered; a new gesture must supersede it.
  fireEvent.pointerDown(graph, { button: 0, buttons: 1, pointerId: 1, clientX: 200, clientY: 200 });
  fireEvent.pointerMove(graph, { buttons: 1, pointerId: 1, clientX: 500, clientY: 200 });
  fireEvent.pointerUp(graph, { button: 0, pointerId: 1, clientX: 500, clientY: 200 });
  expect(screen.getByRole('button', { name: '브레이크 순서 이동' }).compareDocumentPosition(screen.getByRole('button', { name: '스로틀 순서 이동' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test.each(['iracing', 'lmu'])('a released button clears a stale channel move and a deliberate label drag still reorders (%s)', async game => {
  await workspace(game);
  const label = screen.getByRole('button', { name: '브레이크 순서 이동' });
  document.elementFromPoint = jest.fn(() => screen.getByRole('button', { name: '스로틀 순서 이동' }));
  fireEvent.pointerDown(label, { button: 0, buttons: 1, pointerId: 1, clientY: 100 });
  fireEvent.pointerMove(window, { buttons: 0, pointerId: 1, clientY: 200 });
  expect(label).toHaveAttribute('aria-pressed', 'false');
  expect(label.compareDocumentPosition(screen.getByRole('button', { name: '스로틀 순서 이동' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.pointerDown(label, { button: 0, buttons: 1, pointerId: 1, clientY: 100 });
  fireEvent.pointerMove(window, { buttons: 1, pointerId: 1, clientY: 200 });
  fireEvent.pointerUp(window, { pointerId: 1 });
  expect(label.compareDocumentPosition(screen.getByRole('button', { name: '스로틀 순서 이동' })) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  expect(label).toHaveAttribute('aria-pressed', 'false');
});
