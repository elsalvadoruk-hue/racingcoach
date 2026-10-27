import { fireEvent, render, screen } from '@testing-library/react';
import App from './App';

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, '', '/');
  Element.prototype.scrollIntoView = jest.fn();
  global.EventSource = class { close() {} };
  global.fetch = jest.fn(async (url) => ({
    ok: true,
    json: async () => url.endsWith('/api/files') ? { files: [] }
      : url.includes('/api/driver-report?') ? { schema_version: 1, game: new URL(url).searchParams.get('game'), period_days: 30, state: 'EMPTY',
        overview: { title: '나의 주행 이야기를 시작해요' }, career: {}, pace: {}, recent_drives: [] }
      : url.includes('/api/overlay') ? { enabled: false, available: false, settings: {}, rows: [], auto_show: false, voices: {} }
      : url.endsWith('/api/settings') ? { version: '1.2.0', settings: { telemetry_dirs: [] }, google: {}, garage61: {}, audio: { radio_clips: 0, spotter_clips: 0 }, telemetry_folders: [] }
      : url.includes('/api/memory/sessions') ? { sessions: [] }
      : url.endsWith('/api/status') ? { telemetry: null, ai_enabled: false }
      : { enabled: true, counts: { sessions: 0 } },
  }));
});

test('the hover-help preference survives the desktop migration and remount', async () => {
  const view = render(<App />);
  await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' });
  fireEvent.click(screen.getByRole('button', { name: '설정', exact: true }));
  const toggle = screen.getByRole('switch', { name: '마우스 오버 설명' });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  expect(toggle).not.toBeChecked();
  view.unmount();
  render(<App />);
  expect(await screen.findByRole('switch', { name: '마우스 오버 설명' })).not.toBeChecked();
});

afterEach(() => {
  delete global.fetch;
  delete global.EventSource;
});

test('an empty telemetry folder renders an explanation instead of staying in loading', async () => {
  render(<App />);
  expect(await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '드라이버', exact: true })).toHaveAttribute('aria-current', 'page');
  fireEvent.click(screen.getByRole('button', { name: '주행 분석', exact: true }));
  expect(await screen.findByRole('heading', { name: '분석할 주행을 선택하세요.' })).toBeInTheDocument();
  expect(screen.queryByText('IBT 채널과 랩 구조 분석 중')).not.toBeInTheDocument();
});

test('a disconnected backend exposes its failure and leaves the app usable', async () => {
  global.fetch.mockRejectedValue(new Error('Backend unavailable for test'));
  render(<App />);
  expect((await screen.findAllByText('Backend unavailable for test')).length).toBeGreaterThan(0);
  expect(screen.getByRole('heading', { name: 'iRacing 주행 코치' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '사용 가이드', exact: true }));
  expect(screen.getByRole('article', { name: '사용 매뉴얼' })).toBeVisible();
  expect(screen.getByRole('heading', { name: '5. 변경 전 A → 변경 후 B 실험하기' })).toBeVisible();
  expect(screen.getByRole('button', { name: '증상 입력 화면 열기' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '설정', exact: true }));
  expect(screen.getByText('설치 설정')).toBeVisible();
});

test('manual opens with an empty telemetry folder and can return to the starting screen', async () => {
  render(<App />);
  await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' });
  fireEvent.click(screen.getByRole('button', { name: '주행 분석', exact: true }));
  await screen.findByRole('heading', { name: '분석할 주행을 선택하세요.' });
  fireEvent.click(screen.getByRole('button', { name: '처음 사용 가이드 열기' }));
  expect(screen.getByRole('navigation', { name: '매뉴얼 목차' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: '분석할 주행을 선택하세요.' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '주행 분석', exact: true }));
  expect(screen.getByRole('heading', { name: '분석할 주행을 선택하세요.' })).toBeVisible();
});

test('an explicit analysis deep link keeps its destination', async () => {
  window.history.replaceState(null, '', '/#summary');
  render(<App />);
  expect(await screen.findByRole('heading', { name: '분석할 주행을 선택하세요.' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '주행 분석', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('LMU also starts on its driver report without changing the saved game', async () => {
  localStorage.setItem('coach.selected-game.v1', 'lmu');
  render(<App />);
  expect(await screen.findByRole('region', { name: 'LMU 드라이버 리포트' })).toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: '나의 주행 이야기를 시작해요' })).toBeInTheDocument();
  expect(localStorage.getItem('coach.selected-game.v1')).toBe('lmu');
});
