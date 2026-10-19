import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SessionLogExport from './SessionLogExport';

const session = { run_id: 'f090160a-59fc-479c-b866-a4e2cfe4c1e9', started_at: 1791799000, ended_at: 1791801000, minutes: 33.2, calls: 405,
  completed: 190, reports: 8, questions: 2, track: 'Circuit des 24 Heures du Mans', track_path: 'lemans full', car: 'McLaren 720S GT3 EVO', session_type: 'Race' };
afterEach(() => jest.restoreAllMocks());

test('lists the driven sessions and downloads the chosen one as a file', async () => {
  const blob = new Blob(['zip']);
  const fetchMock = jest.spyOn(global, 'fetch')
    .mockImplementationOnce(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ sessions: [session] }) }))
    .mockImplementationOnce(() => Promise.resolve({ ok: true, blob: () => Promise.resolve(blob),
      headers: { get: (name) => (name === 'X-Log-File-Name' ? 'coach-log-lemans-full-20261012-2005.zip' : null) } }));
  URL.createObjectURL = jest.fn(() => 'blob:log');
  URL.revokeObjectURL = jest.fn();
  const clicked = [];
  jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() { clicked.push([this.download, this.href]); });
  render(<SessionLogExport apiBase="http://api" />);
  expect(await screen.findByText(/Circuit des 24 Heures du Mans/)).toBeInTheDocument();
  expect(screen.getByText('McLaren 720S GT3 EVO · Race · 33.2분 · 안내 기록 405건 · 보고 8건 · 질문 2건')).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText('마이크로 한 질문과 답변도 포함'));
  await userEvent.click(screen.getByRole('button', { name: /주행 로그 파일 받기/ }));
  await waitFor(() => expect(clicked).toEqual([['coach-log-lemans-full-20261012-2005.zip', 'blob:log']]));
  expect(fetchMock).toHaveBeenLastCalledWith(`http://api/api/session-logs/${session.run_id}/export?questions=1`);
  expect(screen.getByRole('status')).toHaveTextContent('coach-log-lemans-full-20261012-2005.zip 파일을 내려받았습니다.');
});

test('says so when nothing was driven yet', async () => {
  jest.spyOn(global, 'fetch').mockImplementation(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ sessions: [] }) }));
  render(<SessionLogExport apiBase="http://api" />);
  expect(await screen.findByText(/아직 기록된 주행이 없습니다/)).toBeInTheDocument();
});

test('sending opens a window for the note, sends only on its button, and can be cancelled', async () => {
  const fetchMock = jest.spyOn(global, 'fetch')
    .mockImplementationOnce(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ sessions: [session], send_available: true }) }))
    .mockImplementationOnce(() => Promise.resolve({ ok: false, json: () => Promise.resolve({ detail: '로그를 보내지 못했습니다. 인터넷 연결을 확인하거나 파일을 받아서 직접 보내 주세요.' }) }))
    .mockImplementationOnce(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ sent: true, name: 'coach-log-lemans-full-20261012-2005.zip' }) }));
  render(<SessionLogExport apiBase="http://api" />);
  expect(await screen.findByText(/앱을 만든 사람의 Google Drive에 전달됩니다/)).toBeInTheDocument();
  expect(screen.queryByLabelText('함께 보낼 메모')).toBeNull();                 // no note box until a session is chosen
  // Cancel: the window closes and nothing is sent.
  await userEvent.click(screen.getByRole('button', { name: /주행 로그 보내기/ }));
  expect(screen.getByRole('dialog')).toHaveTextContent('Circuit des 24 Heures du Mans');
  await userEvent.click(screen.getByRole('button', { name: '취소' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(fetchMock).toHaveBeenCalledTimes(1);                                   // listing only
  // Send: a failure keeps the window and the note for another try.
  await userEvent.click(screen.getByRole('button', { name: /주행 로그 보내기/ }));
  await userEvent.type(screen.getByLabelText('함께 보낼 메모'), '12랩쯤 연료 안내가 달랐음');
  await userEvent.click(screen.getByRole('button', { name: '보내기' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('로그를 보내지 못했습니다.');
  expect(screen.getByLabelText('함께 보낼 메모')).toHaveValue('12랩쯤 연료 안내가 달랐음');
  await userEvent.click(screen.getByRole('button', { name: '보내기' }));
  expect(await screen.findByRole('status')).toHaveTextContent('주행의 로그를 보냈습니다.');
  expect(fetchMock).toHaveBeenLastCalledWith(`http://api/api/session-logs/${session.run_id}/send`, expect.objectContaining({
    method: 'POST', body: JSON.stringify({ questions: false, note: '12랩쯤 연료 안내가 달랐음' }) }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

test('offers no send button when no receiving address is set up', async () => {
  jest.spyOn(global, 'fetch').mockImplementation(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ sessions: [session], send_available: false }) }));
  render(<SessionLogExport apiBase="http://api" />);
  expect(await screen.findByRole('button', { name: /주행 로그 파일 받기/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /주행 로그 보내기/ })).toBeNull();
  expect(screen.queryByLabelText('함께 보낼 메모')).toBeNull();
});
