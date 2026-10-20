import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RadioAudioPrepare from './RadioAudioPrepare';

const reply = (data) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });

test('starts the download, follows progress and stops polling when complete', async () => {
  jest.useFakeTimers();
  const idle = { state: 'IDLE', total: 559, ready: 40, missing: 519, characters_missing: 5000, failed: 0, message: '', spotter_voice: 'Aoede' };
  const fetchMock = jest.spyOn(global, 'fetch')
    .mockImplementationOnce(() => reply(idle))
    .mockImplementationOnce(() => reply({ ...idle, state: 'RUNNING' }))
    .mockImplementationOnce(() => reply({ ...idle, state: 'RUNNING', ready: 300, missing: 259 }))
    .mockImplementation(() => reply({ ...idle, state: 'COMPLETE', ready: 559, missing: 0, characters_missing: 0 }));
  render(<RadioAudioPrepare apiBase="http://api" />);
  expect(await screen.findByText('40 / 559개 준비됨 · 남은 519개, 약 5000자')).toBeInTheDocument();
  expect(screen.getByText(/지금 고른 스포터 목소리\(Aoede\)의 음성 수입니다/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '빠진 음성 합성' }));
  expect(fetchMock).toHaveBeenLastCalledWith('http://api/api/live-coach/radio-audio/prepare', { method: 'POST' });
  expect(await screen.findByRole('button', { name: '멈추기' })).toBeInTheDocument();
  await act(async () => { jest.advanceTimersByTime(1500); });
  expect(await screen.findByText(/300 \/ 559개 준비됨/)).toBeInTheDocument();
  await act(async () => { jest.advanceTimersByTime(1500); });
  await waitFor(() => expect(screen.getByRole('button', { name: '준비 완료' })).toBeDisabled());
  const calls = fetchMock.mock.calls.length;
  await act(async () => { jest.advanceTimersByTime(6000); });
  expect(fetchMock.mock.calls.length).toBe(calls);
});

test('shows why preparation stopped', async () => {
  jest.spyOn(global, 'fetch').mockImplementation(() => reply({ state: 'FAILED', total: 559, ready: 10, missing: 549, characters_missing: 5300, failed: 3, message: 'Google 서비스 인증·권한 또는 API 활성화 상태를 확인하세요.' }));
  render(<RadioAudioPrepare apiBase="http://api" />);
  expect(await screen.findByText('Google 서비스 인증·권한 또는 API 활성화 상태를 확인하세요.')).toBeInTheDocument();
  expect(screen.getByText(/중단됨/)).toBeInTheDocument();
});
