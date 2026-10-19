import { render, screen, waitFor } from '@testing-library/react';
import CornerTrace, { cornerNotes } from './CornerTrace';

const L = 4000;   // metres: 0.001 of a lap = 4 m
const corner = {
  id: 'C5', label: '5번 코너', entry_pct: 0.2, apex_pct: 0.21, exit_pct: 0.22,
  target: { brake_start_pct: 0.1955, brake_release_pct: 0.2080, full_throttle_pct: 0.2160, min_speed_kph: 98.2, exit_speed_kph: 150.0 },
  reference: { brake_start_pct: 0.1950, brake_release_pct: 0.2050, full_throttle_pct: 0.2130, min_speed_kph: 101.0, exit_speed_kph: 150.4 },
};

function rows(start, end) {
  const out = [];
  for (let p = start; p <= end; p += 0.0005) {
    out.push({ distance_pct: p, target_speed_kph: 150 - Math.abs(p - 0.21) * 2000, reference_speed_kph: 152 - Math.abs(p - 0.21) * 2000,
      target_brake: p > 0.1955 && p < 0.208 ? 0.8 : 0, reference_brake: p > 0.195 && p < 0.205 ? 0.8 : 0,
      target_throttle: p > 0.216 ? 1 : 0.2, reference_throttle: p > 0.213 ? 1 : 0.2 });
  }
  return out;
}

test('notes say how far and which way each point moved, small differences left out', () => {
  const notes = cornerNotes(corner, L);
  expect(notes.map((n) => n.key)).toEqual(['brake_release', 'min_speed', 'full_throttle']);
  expect(notes[0].text).toBe('브레이크 놓음: 기준보다 12m 늦게 놓았습니다.');
  expect(notes[1].text).toBe('최저 속도: 기준보다 2.8km/h 낮습니다.');
  expect(notes[2].text).toBe('풀스로틀: 기준보다 12m 늦게 열었습니다.');
});

test('no reference lap means no notes', () => {
  expect(cornerNotes({ ...corner, reference: null }, L)).toEqual([]);
});

test('a corner across the start line is measured the short way round', () => {
  const across = { ...corner, entry_pct: 0.995, apex_pct: 0.999, exit_pct: 0.003,
    target: { full_throttle_pct: 0.002 }, reference: { full_throttle_pct: 0.998 } };
  expect(cornerNotes(across, L)[0].text).toBe('풀스로틀: 기준보다 16m 늦게 열었습니다.');
});

test('draws the window, numbers the notes and marks the practice focus', async () => {
  const load = jest.fn((a, b) => Promise.resolve(rows(a, b)));
  render(<CornerTrace corner={corner} trackLength={L} load={load} focusEvent="full_throttle" referenceLabel="기준 랩 7" />);
  expect(await screen.findByTestId('corner-trace')).toBeInTheDocument();
  const [start, end] = load.mock.calls[0];
  expect(start).toBeCloseTo(0.2 - 120 / L);
  expect(end).toBeCloseTo(0.22 + 80 / L);
  expect(screen.getByText(/점선 = 기준 랩 7/)).toBeInTheDocument();
  expect(screen.getByText(/늦게 열었습니다. ← 오늘의 과제/)).toBeInTheDocument();
});

test('asks for two pieces when the window crosses the start line', async () => {
  const load = jest.fn((a, b) => Promise.resolve(rows(a, b)));
  render(<CornerTrace corner={{ ...corner, entry_pct: 0.01, apex_pct: 0.015, exit_pct: 0.02 }} trackLength={L} load={load} />);
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(load.mock.calls[0][0]).toBeCloseTo(1 - 0.02);
  expect(load.mock.calls[0][1]).toBe(1);
  expect(load.mock.calls[1][0]).toBe(0);
});

test('a failed request is shown, not drawn', async () => {
  render(<CornerTrace corner={corner} trackLength={L} load={() => Promise.reject(new Error('연결 실패'))} />);
  expect(await screen.findByText('연결 실패')).toBeInTheDocument();
});
