import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UpdateCenter, { UpdateSettings } from './UpdateCenter';

const idleJob = { state: 'IDLE', done_bytes: 0, total_bytes: 0, files_done: 0, files_total: 0, message: '' };
const voice = (extra = {}) => ({ version: '1.4.0', total: 51982, missing: 0, obsolete_files: 0, obsolete_bytes: 0, error: '',
  scanning: false, job: idleJob, drive_url: 'https://drive.example/voice', ...extra });
const app = (extra = {}) => ({ current: '1.4.1', available: false, checking: false, checked_at: 1792477000, error: '', latest: null, mode: 'zip',
  staged: null, download: { state: 'IDLE', done: 0, total: 0, message: '' }, restart_supported: true, ...extra });
const newer = { version: '1.5.0', title: '1.5.0 — 자동 업데이트', notes: '## 변경 내용\r\n- **업데이트 창** 추가 [자세히](https://github.example/notes)\r\n1. 앱 종료', url: 'https://github.example/v1.5.0', published: '', prebuilt: true };
const reply = (data, ok = true) => Promise.resolve({ ok, json: () => Promise.resolve(data) });
const fetcherFor = (...answers) => {
  const fetcher = jest.fn();
  answers.forEach((answer) => fetcher.mockImplementationOnce(() => reply(answer)));
  fetcher.mockImplementation(() => reply(answers[answers.length - 1]));
  return fetcher;
};
const settle = () => act(async () => {});
beforeEach(() => window.localStorage.clear());

test('stays closed when the program and the voice are current', async () => {
  const fetcher = fetcherFor({ app: app(), voice: voice() });
  render(<UpdateCenter apiBase="http://api" fetcher={fetcher} />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('http://api/api/update', { method: 'GET' }));
  await settle();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('waits for the check that runs when the app starts, then opens', async () => {
  jest.useFakeTimers();
  try {
    const fetcher = fetcherFor({ app: app({ checking: true, checked_at: null }), voice: voice({ scanning: true }) },
      { app: app({ available: true, latest: newer }), voice: voice() });
    render(<UpdateCenter apiBase="http://api" fetcher={fetcher} />);
    await settle();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(screen.getByRole('dialog')).toHaveTextContent('새 버전 v1.5.0');
  } finally { jest.useRealTimers(); }
});

test('a version or missing voice found while the app runs never opens the window', async () => {
  jest.useFakeTimers();
  try {
    const fetcher = fetcherFor({ app: app(), voice: voice() },
      { app: app({ available: true, latest: newer }), voice: voice({ missing: 5 }) });
    render(<UpdateCenter apiBase="http://api" fetcher={fetcher} />);
    await settle();
    await act(async () => { jest.advanceTimersByTime(600001); });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  } finally { jest.useRealTimers(); }
});

test('offers a new version with its notes, downloads it and restarts to apply', async () => {
  const fetcher = fetcherFor({ app: app({ available: true, latest: newer }), voice: voice() },
    { app: app({ available: true, latest: newer, staged: '1.5.0', download: { state: 'STAGED', done: 9, total: 9, message: '' } }), voice: voice() },
    { restarting: true });
  render(<UpdateCenter apiBase="http://api" fetcher={fetcher} />);
  expect(await screen.findByRole('dialog')).toHaveTextContent('새 버전 v1.5.0');
  const notes = screen.getByTestId('release-notes');
  expect(notes).toHaveTextContent('변경 내용업데이트 창 추가 자세히앱 종료');   // drawn, not shown as ## or **
  expect(notes.querySelector('strong')).toHaveTextContent('업데이트 창');
  expect(notes.querySelector('ul li a')).toHaveAttribute('href', 'https://github.example/notes');
  expect(notes.querySelector('ol li')).toHaveTextContent('앱 종료');
  await userEvent.click(screen.getByRole('button', { name: '업데이트 받기' }));
  expect(fetcher).toHaveBeenLastCalledWith('http://api/api/update/app/download', { method: 'POST' });
  await userEvent.click(await screen.findByRole('button', { name: '지금 다시 시작해서 적용' }));
  expect(fetcher).toHaveBeenLastCalledWith('http://api/api/update/app/restart', { method: 'POST' });
  expect(await screen.findByRole('status')).toHaveTextContent('다시 시작하는 중');
});

test('a skipped version does not open the window again', async () => {
  const status = { app: app({ available: true, latest: newer }), voice: voice() };
  const { unmount } = render(<UpdateCenter apiBase="http://api" fetcher={fetcherFor(status)} />);
  await userEvent.click(await screen.findByRole('button', { name: '이 버전 건너뛰기' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  unmount();
  const fetcher = fetcherFor(status);
  render(<UpdateCenter apiBase="http://api" fetcher={fetcher} />);
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  await settle();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('missing voice opens the window with the download, the Drive link and the cleanup', async () => {
  const fetcher = fetcherFor({ app: app(), voice: voice({ missing: 120, obsolete_files: 3, obsolete_bytes: 2500000 }) });
  render(<UpdateCenter apiBase="http://api" fetcher={fetcher} />);
  expect(await screen.findByRole('dialog')).toHaveTextContent('음성 51,982개 중 120개가 없습니다.');
  expect(screen.getByRole('link', { name: 'Google Drive 음성 폴더' })).toHaveAttribute('href', 'https://drive.example/voice');
  await userEvent.click(screen.getByRole('button', { name: '빠진 음성 받기' }));
  expect(fetcher).toHaveBeenLastCalledWith('http://api/api/update/voice/download', { method: 'POST' });
  await userEvent.click(screen.getByRole('button', { name: /이전 버전 음성 정리/ }));
  expect(fetcher).toHaveBeenLastCalledWith('http://api/api/update/voice/cleanup', { method: 'POST' });
  await userEvent.click(screen.getByRole('button', { name: '나중에' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a git checkout is told to pull instead of downloading', async () => {
  render(<UpdateSettings apiBase="http://api" fetcher={fetcherFor({ app: app({ available: true, latest: newer, mode: 'git' }), voice: voice() })} />);
  expect(await screen.findByText(/Git으로 받은 폴더입니다/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '업데이트 받기' })).not.toBeInTheDocument();
});

test('voice progress is shown while downloading and can be stopped', async () => {
  jest.useFakeTimers();
  try {
    const running = { app: app(), voice: voice({ missing: 10, job: { ...idleJob, state: 'RUNNING', done_bytes: 2e6, total_bytes: 4e6, message: '음성 10개 받는 중' } }) };
    const fetcher = fetcherFor(running);
    render(<UpdateSettings apiBase="http://api" fetcher={fetcher} />);
    expect(await screen.findByText('음성 10개 받는 중 · 2 MB / 4 MB')).toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(fetcher.mock.calls.length).toBeGreaterThanOrEqual(2);
    await act(async () => { screen.getByRole('button', { name: '멈추기' }).click(); });
    expect(fetcher).toHaveBeenLastCalledWith('http://api/api/update/voice/stop', { method: 'POST' });
  } finally { jest.useRealTimers(); }
});
