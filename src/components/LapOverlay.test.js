import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import LapOverlay, { defaultLaps, PanBar } from './LapOverlay';

const laps = [
  { lap_number: 1, lap_time_s: 100.2, status: 'VALID' },
  { lap_number: 2, lap_time_s: 99.0, status: 'OFF_TRACK' },
  { lap_number: 3, lap_time_s: 99.5, status: 'VALID' },
  { lap_number: 4, lap_time_s: null, status: 'INCOMPLETE' },
];
const corners = [{ id: 'C1', label: '자동 구간 1', entry_pct: 0.3, apex_pct: 0.31, exit_pct: 0.32 }];

function payload(body) {
  const distance = [body.start_pct, (body.start_pct + body.end_pct) / 2, body.end_pct];
  const series = Object.fromEntries(body.laps.map((lap, i) => [String(lap), { delta: [0, 0.1 * i, 0.2 * i], speed: [150, 100 + i, 160], throttle: [100, 0, 100] }]));
  return { base_lap: body.base_lap ?? 3, laps: body.laps.map((lap) => ({ lap, valid: lap !== 2 })), distance_pct: distance, track_length_m: 4000,
    channels: [{ key: 'delta', label: '시간 차이 (랩 3 기준, + 느림)', unit: 's' }, { key: 'speed', label: '속도', unit: 'km/h' }, { key: 'throttle', label: '스로틀', unit: '%' }],
    series };
}

test('starts with the reference and target laps, then the fastest valid ones', () => {
  expect(defaultLaps(laps, [1, 3])).toEqual([1, 3]);
  expect(defaultLaps(laps, [])).toEqual([3, 1]);
  expect(defaultLaps(laps, [4])).toEqual([3, 1]);     // an incomplete lap is never offered
});

test('loads the chosen laps, adds a lap and changes the base lap', async () => {
  const load = jest.fn((body) => Promise.resolve(payload(body)));
  render(<LapOverlay laps={laps} corners={corners} preferredLaps={[3]} load={load} />);
  await waitFor(() => expect(load).toHaveBeenCalled());
  expect(load.mock.calls[0][0]).toMatchObject({ laps: [3, 1], start_pct: 0, end_pct: 1 });
  expect(await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' })).toBeInTheDocument();
  expect(screen.getByText('무효')).toBeInTheDocument();
  fireEvent.click(screen.getByText('랩 2'));
  await waitFor(() => expect(load.mock.calls.at(-1)[0].laps).toEqual([3, 1, 2]));
  fireEvent.click(screen.getByLabelText('랩 1을 기준으로'));
  await waitFor(() => expect(load.mock.calls.at(-1)[0].base_key).toBe('1'));
});

test('a corner narrows the window around it and can go back to the whole lap', async () => {
  const load = jest.fn((body) => Promise.resolve(payload(body)));
  render(<LapOverlay laps={laps} corners={corners} load={load} />);
  await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  fireEvent.change(screen.getByLabelText('코너로 이동'), { target: { value: 'C1' } });
  // shown: 150 m before entry to 100 m after exit; asked for: that plus one window on each side
  const start = 0.3 - 150 / 4000;
  const end = 0.32 + 100 / 4000;
  await screen.findByText(`${Math.round(start * 4000)} m ~ ${Math.round(end * 4000)} m`);
  await waitFor(() => expect(load.mock.calls.at(-1)[0].start_pct).toBeCloseTo(start - (end - start)));
  expect(load.mock.calls.at(-1)[0].end_pct).toBeCloseTo(end + (end - start));
  fireEvent.click(screen.getByText('전체 랩으로'));
  await waitFor(() => expect(load.mock.calls.at(-1)[0]).toMatchObject({ start_pct: 0, end_pct: 1 }));
});

test('channels can be switched off', async () => {
  const load = jest.fn((body) => Promise.resolve(payload(body)));
  render(<LapOverlay laps={laps} load={load} />);
  await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  expect(screen.getAllByText('스로틀').length).toBeGreaterThan(1);       // button + lane label
  fireEvent.click(screen.getByRole('button', { name: '스로틀' }));
  expect(screen.getAllByText('스로틀')).toHaveLength(1);
});

test('expert steering uses a symmetric scale with zero at the lane centre', async () => {
  const load = jest.fn(async body => {
    const result = payload(body);
    result.channels.push({ key: 'steering', label: '조향', unit: '°' });
    Object.values(result.series).forEach(series => { series.steering = [-10, -30, -60]; });
    return result;
  });
  render(<LapOverlay laps={laps} load={load} />);
  const zero = await screen.findByTestId('overlay-steering-zero-guide');
  const lane = zero.parentElement;
  const background = lane.querySelector('rect');
  const middle = Number(background.getAttribute('y')) + Number(background.getAttribute('height')) / 2;
  expect(Number(zero.getAttribute('y1'))).toBe(middle);
  expect(lane).toHaveTextContent('60.0 °');
  expect(lane).toHaveTextContent('-60.0');
  for (const path of lane.querySelectorAll('path')) {
    const ys = [...path.getAttribute('d').matchAll(/[ML][\d.]+ ([\d.]+)/g)].map(match => Number(match[1]));
    expect(ys.every(value => value > middle)).toBe(true);
  }
});

test('a failed request is shown', async () => {
  render(<LapOverlay laps={laps} load={() => Promise.reject(new Error('연결 실패'))} />);
  expect(await screen.findByText('연결 실패')).toBeInTheDocument();
});

function densePayload(body) {
  const base = payload(body);
  const distance = Array.from({ length: 41 }, (_, i) => body.start_pct + ((body.end_pct - body.start_pct) * i) / 40);
  const series = Object.fromEntries(Object.keys(base.series).map((lap) => [lap, { delta: distance.map(() => 0), speed: distance.map((p) => 100 + p * 50) }]));
  return { ...base, distance_pct: distance, series };
}

test('dragging right zooms in, dragging left steps back out', async () => {
  const load = jest.fn((body) => Promise.resolve(densePayload(body)));
  render(<LapOverlay laps={laps} load={load} />);
  const svg = await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 400, x: 0, y: 0, right: 1000, bottom: 400 });
  const drag = async (from, to) => {
    fireEvent.mouseDown(svg, { clientX: from });
    fireEvent.mouseMove(svg, { clientX: (from + to) / 2 });
    fireEvent.mouseMove(svg, { clientX: to });
    fireEvent.mouseUp(svg, { clientX: to });
  };
  await drag(300, 800);                                   // left to right: zoom into that part
  expect(await screen.findByText('전체 랩으로')).toBeInTheDocument();
  expect(screen.getByText(/\d+ m ~ \d+ m/)).toBeInTheDocument();
  await drag(800, 300);                                   // right to left: back to the previous range
  await waitFor(() => expect(screen.queryByText('전체 랩으로')).toBeNull());
});

test('a lap of another drive file and a Garage61 lap can be added and removed', async () => {
  const load = jest.fn((body) => {
    const base = payload(body);
    const extraKeys = (body.extra || []).map((item) => (item.source === 'IBT' ? `ibt:${item.filename}#${item.lap}` : `g61:${item.filename}`));
    return Promise.resolve({ ...base,
      laps: [...base.laps.map((lap) => ({ ...lap, key: String(lap.lap) })), ...extraKeys.map((key) => ({ key, label: key.startsWith('g61') ? 'Garage61 · Someone' : '07-24 18:38 · 랩 3', lap_time_s: 99.1, source: key.startsWith('g61') ? 'GARAGE61' : 'IBT' }))],
      series: { ...base.series, ...Object.fromEntries(extraKeys.map((key) => [key, { delta: [0, 0.2, 0.4], speed: [150, 98, 160], throttle: [100, 0, 100] }])) } });
  });
  const loadLaps = jest.fn(() => Promise.resolve({ laps: [{ lap_number: 3, lap_time_s: 99.1, valid: true }] }));
  render(<LapOverlay laps={laps} load={load} loadLaps={loadLaps}
    otherFiles={[{ name: 'other 2026-07-24 18-38-59.ibt', label: '2026-07-24 18:38 · GT3' }]}
    references={[{ name: 'g61.csv', label: 'Someone · 1:39.100' }]} />);
  await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  fireEvent.change(screen.getByLabelText('다른 주행 파일'), { target: { value: 'other 2026-07-24 18-38-59.ibt' } });
  await waitFor(() => expect(loadLaps).toHaveBeenCalledWith('other 2026-07-24 18-38-59.ibt'));
  await screen.findByText(/랩 3 · 1:39.100/);
  fireEvent.change(screen.getByLabelText('그 파일의 랩'), { target: { value: '3' } });
  fireEvent.click(screen.getAllByText('이 랩 더하기')[0]);
  await waitFor(() => expect(load.mock.calls.at(-1)[0].extra).toEqual([{ source: 'IBT', filename: 'other 2026-07-24 18-38-59.ibt', lap: 3 }]));
  fireEvent.change(screen.getByLabelText('Garage61 랩'), { target: { value: 'g61.csv' } });
  fireEvent.click(screen.getAllByText('이 랩 더하기')[1]);
  await waitFor(() => expect(load.mock.calls.at(-1)[0].extra).toHaveLength(2));
  expect(await screen.findByText('Garage61 · Someone')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('+2을 기준으로'));
  await waitFor(() => expect(load.mock.calls.at(-1)[0].base_key).toBe('g61:g61.csv'));
  fireEvent.click(screen.getByLabelText('+1 빼기'));
  await waitFor(() => expect(load.mock.calls.at(-1)[0].extra).toEqual([{ source: 'GARAGE61', filename: 'g61.csv', lap: undefined }]));
});

test('a lap the server could not draw shows why, and the rest are still drawn', async () => {
  const load = jest.fn((body) => Promise.resolve({ ...payload(body), skipped: (body.extra || []).map((item) => ({ key: `g61:${item.filename}`, reason: '같은 트랙·레이아웃의 Garage61 랩이 아닙니다.' })) }));
  render(<LapOverlay laps={laps} load={load} references={[{ name: 'g61.csv', label: 'Someone · 1:39.100' }]} />);
  await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  fireEvent.change(screen.getByLabelText('Garage61 랩'), { target: { value: 'g61.csv' } });
  fireEvent.click(screen.getByText('이 랩 더하기'));
  expect(await screen.findByText(/그리지 못함: 같은 트랙/)).toBeInTheDocument();
  expect(screen.getByRole('img', { name: '여러 랩 겹쳐 보기 그래프' })).toBeInTheDocument();
});

describe('the scroll bar under the chart', () => {
  const setup = (range) => {
    const onChange = jest.fn();
    const utils = render(<PanBar range={range} onChange={onChange} />);
    const bar = screen.getByRole('scrollbar', { name: '그래프 좌우 이동' });
    bar.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 16, x: 0, y: 0, right: 1000, bottom: 16 });
    return { ...utils, bar, onChange };
  };
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  test('the mouse wheel moves the window by part of its width at once', () => {
    const { bar, onChange } = setup([0.4, 0.5]);
    fireEvent.wheel(bar, { deltaY: 100 });
    const [start, end] = onChange.mock.calls[0][0];
    expect(start).toBeCloseTo(0.415);                        // one notch: 15 % of a 0.1 window
    expect(end - start).toBeCloseTo(0.1);
    fireEvent.wheel(bar, { deltaY: -100 });
    expect(onChange.mock.calls[1][0][0]).toBeCloseTo(0.385);
  });

  test('it stops at the ends of the lap and does nothing for the whole lap', () => {
    const { bar, onChange, unmount } = setup([0.88, 0.98]);
    fireEvent.wheel(bar, { deltaY: 600 });                   // a big step stops at the end of the lap
    expect(onChange.mock.calls[0][0][1]).toBeCloseTo(1);
    unmount();
    const whole = setup([0, 1]);
    fireEvent.wheel(whole.bar, { deltaY: 100 });
    expect(whole.onChange).not.toHaveBeenCalled();
  });

  test('dragging the thumb moves the chart as it goes and a click beside it jumps there', () => {
    const { bar, onChange } = setup([0.2, 0.3]);
    // jsdom has no PointerEvent: a MouseEvent under the pointer event's name carries the position.
    const pointer = (type, clientX) => act(() => { bar.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX, button: 0, buttons: type === 'pointerup' ? 0 : 1 })); });
    pointer('pointerdown', 250);                                  // on the thumb (200-300)
    pointer('pointermove', 350);
    expect(onChange.mock.calls[0][0][0]).toBeCloseTo(0.3);       // while still held
    pointer('pointerup', 450);
    expect(onChange.mock.calls.at(-1)[0][0]).toBeCloseTo(0.4);
    const before = onChange.mock.calls.length;
    pointer('pointerdown', 800);                                  // beside it: centred on the click
    expect(onChange.mock.calls[before][0][0]).toBeCloseTo(0.75);
  });

  test('race chart pan bar leaves the wheel to vertical scrolling', () => {
    const onChange = jest.fn();
    render(<PanBar range={[.2, .4]} onChange={onChange} wheelPan={false} controls="race-position-chart" />);
    const bar = screen.getByRole('scrollbar');
    fireEvent.wheel(bar, { deltaY: 150 });
    expect(onChange).not.toHaveBeenCalled();
    expect(bar).toHaveAttribute('aria-controls', 'race-position-chart');
    fireEvent.keyDown(bar, { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});

test('the wheel over a zoomed chart moves it left and right without asking the server again', async () => {
  const load = jest.fn((body) => Promise.resolve(densePayload(body)));
  render(<LapOverlay laps={laps} corners={corners} load={load} />);
  await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  fireEvent.change(screen.getByLabelText('코너로 이동'), { target: { value: 'C1' } });
  const first = await screen.findByText(/\d+ m ~ \d+ m/);
  await waitFor(() => expect(load.mock.calls.at(-1)[0].start_pct).toBeGreaterThan(0.1));
  const calls = load.mock.calls.length;
  const before = first.textContent;
  fireEvent.wheel(screen.getByRole('img', { name: '여러 랩 겹쳐 보기 그래프' }), { deltaY: 100 });
  await waitFor(() => expect(screen.getByText(/\d+ m ~ \d+ m/).textContent).not.toBe(before));
  await new Promise((resolve) => { setTimeout(resolve, 200); });
  expect(load.mock.calls.length).toBe(calls);                // still inside the stretch already fetched
});

test('compact readout groups keep every selected lap visible at the shared cursor', async () => {
  const load = jest.fn((body) => Promise.resolve(payload(body)));
  render(<LapOverlay laps={laps} load={load} readoutGroupSize={1} fillHeight />);
  const svg = await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 400 });
  fireEvent.mouseMove(svg, { clientX: 600 });
  const readout = screen.getByTestId('lap-overlay-readout');
  expect(readout.querySelectorAll('table')).toHaveLength(2);
  const headings = [...readout.querySelectorAll('thead th')].map(element => element.textContent);
  expect(headings).toEqual(expect.arrayContaining(['3★', '1']));
  expect(readout.querySelectorAll('tbody tr')).toHaveLength(6);
});

test('a long lap list keeps the selected laps and base visible separately and retains the eight-lap limit', async () => {
  const many = Array.from({ length: 89 }, (_, index) => ({ lap_number: index + 2, lap_time_s: 110 - index / 100, status: 'VALID' }));
  const load = jest.fn(async body => ({ ...payload(body), base_key: body.base_key || String(body.laps[0]) }));
  render(<LapOverlay laps={many} preferredLaps={[88]} load={load} fillHeight />);
  await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
  const list = screen.getByRole('group', { name: '겹쳐 볼 랩 목록' });
  const summary = screen.getByLabelText('선택한 비교 랩');
  expect(within(list).getByText('랩 2')).toBeInTheDocument();
  expect(within(list).getByText('랩 90')).toBeInTheDocument();
  expect(summary).toHaveTextContent('88★');
  expect(summary).toHaveTextContent('전체 89랩');
  for (let lap = 2; lap <= 10; lap += 1) fireEvent.click(within(list).getByText(`랩 ${lap}`, { exact: true }));
  await waitFor(() => expect(load.mock.calls.at(-1)[0].laps).toHaveLength(8));
  expect(summary).toHaveTextContent('선택 8/8');
  expect(summary).toHaveTextContent('88★');
  expect(within(list).getAllByRole('button', { pressed: true })).toHaveLength(8);
});

test('the stretched chart follows its visible pixel dimensions and cleans up its resize observer', async () => {
  const previous = global.ResizeObserver;
  let resize;
  const disconnect = jest.fn();
  global.ResizeObserver = class { constructor(callback) { resize = callback; } observe() {} disconnect() { disconnect(); } };
  try {
    const load = jest.fn(async body => payload(body));
    const view = render(<LapOverlay laps={laps} load={load} fillHeight />);
    const svg = await screen.findByRole('img', { name: '여러 랩 겹쳐 보기 그래프' });
    Object.defineProperties(svg, { clientWidth: { configurable: true, value: 1440 }, clientHeight: { configurable: true, value: 400 } });
    act(() => resize());
    expect(svg).toHaveAttribute('viewBox', '0 0 1440 400');
    expect(svg.querySelector('text')).toHaveAttribute('font-size', '11');
    view.unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
  } finally { global.ResizeObserver = previous; }
});
