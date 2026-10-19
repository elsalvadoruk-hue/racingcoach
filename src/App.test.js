import { fireEvent, render, screen } from '@testing-library/react';
import App from './App';

beforeEach(() => {
  localStorage.clear();
  Element.prototype.scrollIntoView = jest.fn();
  global.EventSource = class { close() {} };
  global.fetch = jest.fn(async (url) => ({
    ok: true,
    json: async () => url.endsWith('/api/files') ? { files: [] }
      : url.includes('/api/memory/sessions') ? { sessions: [] }
      : url.endsWith('/api/status') ? { telemetry: null, ai_enabled: false }
      : { enabled: true, counts: { sessions: 0 } },
  }));
});

test('the top hover-help switch restores its saved state after remount', async () => {
  const view = render(<App />);
  await screen.findByRole('heading', { name: '분석할 주행을 선택하세요.' });
  const toggle = screen.getByRole('switch', { name: '마우스 오버 설명' });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  expect(toggle).not.toBeChecked();
  view.unmount();
  render(<App />);
  await screen.findByRole('heading', { name: '분석할 주행을 선택하세요.' });
  expect(screen.getByRole('switch', { name: '마우스 오버 설명' })).not.toBeChecked();
});

afterEach(() => {
  delete global.fetch;
  delete global.EventSource;
});

test('an empty telemetry folder renders an explanation instead of staying in loading', async () => {
  render(<App />);
  expect(await screen.findByRole('heading', { name: '분석할 주행을 선택하세요.' })).toBeInTheDocument();
  expect(screen.queryByText('IBT 채널과 랩 구조 분석 중')).not.toBeInTheDocument();
});

test('a disconnected backend exposes its failure and leaves the app usable', async () => {
  global.fetch.mockRejectedValue(new Error('Backend unavailable for test'));
  render(<App />);
  expect(await screen.findByText('Backend unavailable for test')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'iRacing 주행 코치' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '처음 사용 가이드', exact: true }));
  expect(screen.getByRole('article', { name: '사용 매뉴얼' })).toBeVisible();
  expect(screen.getByRole('heading', { name: '5. 변경 전 A → 변경 후 B 실험하기' })).toBeVisible();
  expect(screen.getByRole('button', { name: '증상 입력 화면 열기' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '연결 상태 확인' }));
  expect(screen.getByRole('heading', { name: '연결과 데이터 상태' })).toBeVisible();
});

test('manual opens with an empty telemetry folder and can return to the starting screen', async () => {
  render(<App />);
  await screen.findByRole('heading', { name: '분석할 주행을 선택하세요.' });
  fireEvent.click(screen.getByRole('button', { name: '처음 사용 가이드 열기' }));
  expect(screen.getByRole('navigation', { name: '매뉴얼 목차' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: '분석할 주행을 선택하세요.' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '주행 요약', exact: true }));
  expect(screen.getByRole('heading', { name: '분석할 주행을 선택하세요.' })).toBeVisible();
});
