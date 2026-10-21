import { GameContext } from '../lib/gameContext';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import OverlaySettings from './OverlaySettings';

const base = { supported: true, running: true, ready: true, goal: 'abs', scene: 'during',
  settings: { width: 121, height: 122, x: 300, y: 100, content_opacity: 100, background_opacity: 85, fixed: false } };
const reply = (data, ok = true) => Promise.resolve({ ok, json: () => Promise.resolve(data) });
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });

test('LMU requests carry the selected game and only the known car can be reset', async () => {
  let view = { ...base, settings_target: { game: 'lmu', car_key: 'porsche-963', car_name: 'Porsche 963' } };
  const mock = jest.spyOn(global, 'fetch').mockImplementation((url, options) => {
    expect(new URL(url).searchParams.get('game')).toBe('lmu');
    if (options.method === 'DELETE') view = { ...view, settings: { ...view.settings, width: 100 } };
    return reply(view);
  });
  render(<GameContext.Provider value={{ selectedGame: 'lmu', activeGame: 'lmu' }}><OverlaySettings apiBase="http://api" /></GameContext.Provider>);
  expect(await screen.findByText('적용 대상: LMU · Porsche 963 (이 차량 설정)')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '이 차량 설정 지우기' }));
  expect(mock).toHaveBeenLastCalledWith('http://api/api/overlay/settings/car?game=lmu', { method: 'DELETE' });
  await waitFor(() => expect(screen.getByLabelText('가로 크기 (%)')).toHaveValue(100));
  expect(screen.getByText('이 차량 설정을 지웠습니다. 게임 기본값을 사용합니다.')).toBeVisible();
});

test('unknown car shows game defaults and no car deletion control', async () => {
  jest.spyOn(global, 'fetch').mockImplementation(() => reply({ ...base, settings_target: { game: 'iracing', car_key: null } }));
  render(<OverlaySettings apiBase="http://api" />);
  expect(await screen.findByText('적용 대상: iRacing · 게임 기본값')).toBeVisible();
  expect(screen.queryByRole('button', { name: '이 차량 설정 지우기' })).not.toBeInTheDocument();
});

test('reports the actual task, measured progress and pinned reference and clears it on session change', async () => {
  jest.useFakeTimers();
  let practice = { title: 'ABS 개입 23% 이하', corner: '5번 코너', recent_ok: 3, phase: 'WARMUP' };
  jest.spyOn(global, 'fetch').mockImplementation(() => reply({ ...base, practice, auto_show: false }));
  render(<OverlaySettings apiBase="http://api" />);
  expect(await screen.findByTestId('overlay-active-practice')).toHaveTextContent('ABS 개입 23% 이하 · 5번 코너 · 3/5 · 워밍업 중');
  expect(screen.getByText(/과제에서 선택한 내 랩 또는 Garage61 종합 기준/)).toBeInTheDocument();
  expect(screen.getByText(/연습·퀄리·레이스의 기본 화면/)).toHaveTextContent('상단 글자 없이 그래프를 넓게');
  expect(screen.getByText(/연습·퀄리·레이스의 기본 화면/)).toHaveTextContent('직전 코너 시간 차이·제동 시작 위치 차이');
  expect(screen.getByText(/세션 연결 후 차량·트랙·노면 조건/)).toHaveTextContent('로컬 Garage61 CSV 캐시를 먼저');
  practice = null;
  await act(async () => { jest.advanceTimersByTime(1200); });
  expect(screen.queryByTestId('overlay-active-practice')).not.toBeInTheDocument();
  expect(screen.getByRole('switch', { name: '주행 중 자동 표시' })).not.toBeChecked();
});

test('reports the real Garage61 reference and switches to a useful missing-reference state on poll', async () => {
  jest.useFakeTimers();
  let reference = { state: 'READY', message: 'Garage61 기준 연결됨', lap_time_s: 99.123 };
  jest.spyOn(global, 'fetch').mockImplementation(() => reply({ ...base, reference }));
  render(<OverlaySettings apiBase="http://api" />);
  expect(await screen.findByRole('status')).toHaveTextContent('Garage61 기준 연결됨 · 기준 1:39.123');
  expect(screen.getByText(/내 입력은 형광 실선/)).toBeInTheDocument();
  reference = { state: 'TOKEN_REQUIRED', message: '설정에서 Garage61 토큰을 등록하세요' };
  await act(async () => { jest.advanceTimersByTime(1200); });
  expect(screen.getByRole('status')).toHaveTextContent('설정에서 Garage61 토큰을 등록하세요');
  expect(screen.getByRole('status')).not.toHaveTextContent('1:39.123');
});

test('does not poll while hidden and provides the controls from Settings independently of analysis', async () => {
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation(() => reply(base));
  const { rerender } = render(<OverlaySettings apiBase="http://api" visible={false} />);
  expect(fetchMock).not.toHaveBeenCalled();
  rerender(<OverlaySettings apiBase="http://api" visible />);
  expect(await screen.findByLabelText('가로 크기 (%)')).toHaveValue(121);
  expect(screen.getByLabelText('세로 크기 (%)')).toHaveValue(122);
  expect(screen.getByRole('slider', { name: '표시 불투명도' })).toHaveValue('100');
  expect(screen.getByRole('slider', { name: '배경 불투명도' })).toHaveValue('85');
  expect(screen.getByRole('button', { name: '미리보기 끄기' })).toBeEnabled();
});

test('automatic driving display is distinct from the explicit sample preview', async () => {
  jest.spyOn(global, 'fetch').mockImplementation(() => reply({ ...base, preview: false, visible: false, auto_show: true }));
  render(<OverlaySettings apiBase="http://api" />);
  expect(await screen.findByRole('switch', { name: '주행 중 자동 표시' })).toBeChecked();
  expect(screen.getByText('주행 대기 · 숨김')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '미리보기 켜기' })).toBeEnabled();
  screen.getAllByRole('slider').forEach((slider) => expect(slider).toBeDisabled());
});

test('an older backend does not pretend automatic display is on or offer an unsupported switch', async () => {
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation(() => reply(base));
  render(<OverlaySettings apiBase="http://api" />);
  const automatic = await screen.findByRole('switch', { name: '주행 중 자동 표시' });
  expect(automatic).toBeDisabled();
  expect(automatic).not.toBeChecked();
  expect(screen.getByText('실행 중인 앱 서버에 자동 표시 기능이 없습니다. 앱을 완전히 종료한 뒤 다시 실행해 주세요.')).toBeInTheDocument();
  await userEvent.click(automatic);
  expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true);
});

test('a missing visibility route explains the server mismatch without claiming the change was saved', async () => {
  jest.spyOn(global, 'fetch').mockImplementation((url) => new URL(url).pathname.endsWith('/visibility')
    ? Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ detail: 'Not Found' }) })
    : reply({ ...base, auto_show: true }));
  render(<OverlaySettings apiBase="http://api" />);
  const automatic = await screen.findByRole('switch', { name: '주행 중 자동 표시' });
  await userEvent.click(automatic);
  expect(await screen.findByRole('alert')).toHaveTextContent('앱을 완전히 종료한 뒤 다시 실행해 주세요.');
  expect(screen.getByRole('alert')).not.toHaveTextContent('Not Found');
  expect(automatic).toBeChecked();
  expect(screen.queryByText('주행 중 자동 표시를 껐습니다.')).not.toBeInTheDocument();
});

test('automatic-display preference saves independently without erasing pending appearance edits', async () => {
  let view = { ...base, preview: true, auto_show: true };
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation((url, options) => {
    if (new URL(url).pathname.endsWith('/visibility')) view = { ...view, auto_show: JSON.parse(options.body).enabled };
    return reply(view);
  });
  render(<OverlaySettings apiBase="http://api" />);
  const width = await screen.findByLabelText('가로 크기 (%)');
  fireEvent.change(width, { target: { value: '147' } });
  await userEvent.click(screen.getByRole('switch', { name: '주행 중 자동 표시' }));
  await waitFor(() => expect(screen.getByRole('switch', { name: '주행 중 자동 표시' })).not.toBeChecked());
  expect(width).toHaveValue(147);
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/overlay/visibility?game=iracing', expect.objectContaining({
    method: 'PUT', body: JSON.stringify({ enabled: false }),
  }));
});

test('applies pending edits before saving and disables every appearance field until unlocked', async () => {
  let view = JSON.parse(JSON.stringify(base));
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation((url, options) => {
    if (options.method === 'PUT') view = { ...view, settings: { ...view.settings, ...JSON.parse(options.body) } };
    if (new URL(url).pathname.endsWith('/fixed')) view = { ...view, settings: { ...view.settings, fixed: JSON.parse(options.body).fixed } };
    return reply(view);
  });
  render(<OverlaySettings apiBase="http://api" />);
  const width = await screen.findByLabelText('가로 크기 (%)');
  fireEvent.change(width, { target: { value: '150' } });
  await userEvent.click(screen.getByRole('button', { name: '현재 설정 저장·고정' }));
  expect(await screen.findByRole('button', { name: '고정 해제·편집' })).toBeEnabled();
  expect(fetchMock.mock.calls.filter(([, options]) => options.method !== 'GET').map(([url]) => url))
    .toEqual(['http://api/api/overlay/settings?game=iracing', 'http://api/api/overlay/fixed?game=iracing']);
  expect(width).toHaveValue(150);
  screen.getAllByRole('spinbutton').forEach((input) => expect(input).toBeDisabled());
  screen.getAllByRole('slider').forEach((input) => expect(input).toBeDisabled());
  await userEvent.click(screen.getByRole('button', { name: '고정 해제·편집' }));
  await waitFor(() => expect(width).toBeEnabled());
  screen.getAllByRole('slider').forEach((input) => expect(input).toBeEnabled());
});

test('dragging both sliders updates percentages immediately and applies only opacity live', async () => {
  jest.useFakeTimers();
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation((url, options) => reply(options.method === 'PUT'
    ? { ...base, settings: { ...base.settings, ...JSON.parse(options.body) } } : base));
  render(<OverlaySettings apiBase="http://api" />);
  const width = await screen.findByLabelText('가로 크기 (%)');
  fireEvent.change(width, { target: { value: '145' } });
  const content = screen.getByRole('slider', { name: '표시 불투명도' });
  const background = screen.getByRole('slider', { name: '배경 불투명도' });
  fireEvent.change(content, { target: { value: '63' } });
  expect(content).toHaveAttribute('aria-valuetext', '63%');
  expect(screen.getByText('63%')).toBeInTheDocument();
  fireEvent.change(content, { target: { value: '0' } });
  fireEvent.change(background, { target: { value: '100' } });
  await act(async () => { jest.advanceTimersByTime(80); });
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/overlay/settings?game=iracing', expect.objectContaining({
    method: 'PUT', body: JSON.stringify({ content_opacity: 0, background_opacity: 100 }),
  }));
  expect(content).toHaveValue('0');
  expect(background).toHaveValue('100');
  expect(width).toHaveValue(145);
  expect(content).toBeEnabled();
});

test('saving during a slow drag waits for the latest value without resetting newer edits', async () => {
  jest.useFakeTimers();
  let view = JSON.parse(JSON.stringify(base));
  const releases = [];
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation((url, options) => {
    if (options.method === 'PUT') return new Promise((resolve) => releases.push(() => {
      view = { ...view, settings: { ...view.settings, ...JSON.parse(options.body) } };
      resolve({ ok: true, json: () => Promise.resolve(view) });
    }));
    if (new URL(url).pathname.endsWith('/fixed')) view = { ...view, settings: { ...view.settings, fixed: JSON.parse(options.body).fixed } };
    return reply(view);
  });
  render(<OverlaySettings apiBase="http://api" />);
  const width = await screen.findByLabelText('가로 크기 (%)');
  const content = screen.getByRole('slider', { name: '표시 불투명도' });
  fireEvent.change(content, { target: { value: '80' } });
  await act(async () => { jest.advanceTimersByTime(80); });
  fireEvent.change(content, { target: { value: '62' } });
  fireEvent.change(content, { target: { value: '37' } });
  fireEvent.change(width, { target: { value: '146' } });
  expect(content).toBeEnabled();
  await act(async () => { releases.shift()(); });
  expect(content).toHaveValue('37');
  expect(width).toHaveValue(146);
  fireEvent.click(screen.getByRole('button', { name: '현재 설정 저장·고정' }));
  expect(fetchMock.mock.calls.some(([url]) => new URL(url).pathname.endsWith('/fixed'))).toBe(false);
  await act(async () => { releases.shift()(); });
  await act(async () => { releases.shift()(); });
  expect(await screen.findByRole('button', { name: '고정 해제·편집' })).toBeEnabled();
  const writes = fetchMock.mock.calls.filter(([, options]) => options.method !== 'GET');
  expect(writes.map(([url, options]) => [new URL(url).pathname.split('/').pop(), JSON.parse(options.body)]))
    .toEqual([
      ['settings', { content_opacity: 80 }], ['settings', { content_opacity: 37 }],
      ['settings', { width: 146, height: 122, x: 300, y: 100, content_opacity: 37, background_opacity: 85 }],
      ['fixed', { fixed: true }],
    ]);
  await act(async () => { jest.advanceTimersByTime(1200); });
  expect(view.settings.content_opacity).toBe(37);
  expect(view.settings.fixed).toBe(true);
});

test('closing flushes the final slider value before stopping the preview', async () => {
  jest.useFakeTimers();
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation(() => reply(base));
  render(<OverlaySettings apiBase="http://api" />);
  await screen.findByLabelText('가로 크기 (%)');
  fireEvent.change(screen.getByRole('slider', { name: '배경 불투명도' }), { target: { value: '24' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '미리보기 끄기' })); });
  expect(fetchMock.mock.calls.filter(([, options]) => options.method !== 'GET').map(([url, options]) => [url, options.method]))
    .toEqual([['http://api/api/overlay/settings?game=iracing', 'PUT'], ['http://api/api/overlay/preview?game=iracing', 'DELETE']]);
});

test('unmounting discards queued opacity changes and prevents later requests', async () => {
  jest.useFakeTimers();
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation(() => reply(base));
  const { unmount } = render(<OverlaySettings apiBase="http://api" />);
  await screen.findByLabelText('가로 크기 (%)');
  fireEvent.change(screen.getByRole('slider', { name: '표시 불투명도' }), { target: { value: '52' } });
  unmount();
  await act(async () => { jest.advanceTimersByTime(1200); });
  expect(fetchMock.mock.calls.filter(([, options]) => options.method !== 'GET')).toHaveLength(0);
});

test('a failed live update keeps the chosen percentage editable and prevents a premature save', async () => {
  jest.useFakeTimers();
  let release;
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation((url, options) => options.method === 'PUT'
    ? new Promise((resolve) => { release = () => resolve({ ok: false, json: () => Promise.resolve({ detail: '연결을 확인하세요.' }) }); })
    : reply(base));
  render(<OverlaySettings apiBase="http://api" />);
  await screen.findByLabelText('가로 크기 (%)');
  const content = screen.getByRole('slider', { name: '표시 불투명도' });
  fireEvent.change(content, { target: { value: '56' } });
  await act(async () => { jest.advanceTimersByTime(80); });
  fireEvent.click(screen.getByRole('button', { name: '현재 설정 저장·고정' }));
  await act(async () => { release(); });
  expect(screen.getByRole('alert')).toHaveTextContent('연결을 확인하세요.');
  expect(content).toHaveValue('56');
  expect(content).toBeEnabled();
  expect(fetchMock.mock.calls.some(([url]) => new URL(url).pathname.endsWith('/fixed'))).toBe(false);
});

test('polling does not erase an unsaved edit and failures do not claim a saved layout', async () => {
  jest.useFakeTimers();
  jest.spyOn(global, 'fetch').mockImplementation((url, options) => options.method === 'GET'
    ? reply(base) : reply({ detail: '파일을 저장하지 못했습니다.' }, false));
  render(<OverlaySettings apiBase="http://api" />);
  const width = await screen.findByLabelText('가로 크기 (%)');
  fireEvent.change(width, { target: { value: '145' } });
  await act(async () => { jest.advanceTimersByTime(1200); });
  expect(width).toHaveValue(145);
  fireEvent.click(screen.getByRole('button', { name: '현재 설정 저장·고정' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('파일을 저장하지 못했습니다.');
  expect(screen.queryByRole('button', { name: '고정 해제·편집' })).not.toBeInTheDocument();
});

test('closing the preview and checking celebration are explicit actions', async () => {
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation(() => reply(base));
  render(<OverlaySettings apiBase="http://api" />);
  await screen.findByLabelText('가로 크기 (%)');
  await userEvent.click(screen.getByText('샘플 장면과 축하 효과'));
  await userEvent.click(screen.getByRole('button', { name: '5/5 축하 보기' }));
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/overlay/demo?game=iracing', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ celebrate: true }),
  }));
  await waitFor(() => expect(screen.getByRole('button', { name: '미리보기 끄기' })).toBeEnabled());
  await act(async () => { await userEvent.click(screen.getByRole('button', { name: '미리보기 끄기' })); });
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/overlay/preview?game=iracing', { method: 'DELETE' });
});
