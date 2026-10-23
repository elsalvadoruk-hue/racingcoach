import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SessionLogAutoSend from './SessionLogAutoSend';

let state;
beforeEach(() => {
  state = { enabled: false, consent_version: 3, consented_at: null, available: true, sent: 0, recent: [] };
  global.fetch = jest.fn(async (url, options = {}) => {
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      state = { ...state, enabled: body.enabled, consented_at: body.enabled ? 1_000_000 : null };
    }
    return { ok: true, json: async () => state };
  });
});

test('turning it on opens the notice and only agreeing turns it on, with the notice version', async () => {
  render(<SessionLogAutoSend apiBase="" />);
  fireEvent.click(await screen.findByRole('switch', { name: '세션 로그 자동 보내기' }));
  expect(screen.getByRole('dialog', { name: '세션 로그를 자동으로 보낼까요?' })).toBeInTheDocument();
  expect(screen.getByText(/마이크로 한 질문과 답변/)).toBeInTheDocument();
  expect(screen.getByText(/국내외 데이터센터/)).toBeInTheDocument();
  expect(screen.getByText(/주행 파일을 분석해 계산한 측정값 요약/)).toBeInTheDocument();
  expect(screen.getByText(/최소 4개, 최대 8개/)).toHaveTextContent('기록 간격과 결측을 보존');
  expect(screen.getByText(/최소 4개, 최대 8개/)).toHaveTextContent('1.4MB 이하');
  expect(screen.getByText(/번호로만 표시되고 이름은 들어가지 않습니다/)).toBeInTheDocument();
  expect(screen.getByText(/주변 차 기록을 먼저 빼고/)).toBeInTheDocument();
  const accept = screen.getByRole('button', { name: '동의하고 켜기' });
  expect(accept).toBeDisabled();
  expect(global.fetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(accept);
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('켜짐'));
  const put = global.fetch.mock.calls.find(([, options]) => options?.method === 'PUT');
  expect(JSON.parse(put[1].body)).toEqual({ enabled: true, consent_version: 3 });
});

test('declining leaves it off and sends nothing', async () => {
  render(<SessionLogAutoSend apiBase="" />);
  fireEvent.click(await screen.findByRole('switch', { name: '세션 로그 자동 보내기' }));
  fireEvent.click(screen.getByRole('button', { name: '켜지 않기' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('switch', { name: '세션 로그 자동 보내기' })).not.toBeChecked();
  expect(global.fetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
});

test('turning it off needs no confirmation', async () => {
  state = { ...state, enabled: true, consented_at: 1_000_000 };
  render(<SessionLogAutoSend apiBase="" />);
  fireEvent.click(await screen.findByRole('switch', { name: '세션 로그 자동 보내기' }));
  await waitFor(() => expect(screen.getByRole('switch', { name: '세션 로그 자동 보내기' })).not.toBeChecked());
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('the top-bar toggle is a boxed button that lights up when on and asks before turning on', async () => {
  const { LogSendSwitch } = require('../desktop/RuntimePreview');
  render(<LogSendSwitch apiBase="" />);
  const toggle = await screen.findByRole('button', { name: '로그 자동 보내기' });
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  expect(toggle).toHaveTextContent('로그 보내기'); expect(toggle).not.toHaveClass('log-send-on');
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '동의하고 켜기' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '로그 자동 보내기' })).toHaveAttribute('aria-pressed', 'true'));
  expect(screen.getByRole('button', { name: '로그 자동 보내기' })).toHaveClass('log-send-on');
});
