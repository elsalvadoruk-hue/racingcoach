import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SessionLogAutoSend from './SessionLogAutoSend';

let state;
beforeEach(() => {
  sessionStorage.clear();
  state = { enabled: false, consent_version: 4, consented_at: null, available: true, sent: 0, recent: [] };
  global.fetch = jest.fn(async (url, options = {}) => {
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      state = { ...state, enabled: body.enabled, consented_at: body.enabled ? 1_000_000 : null, reconsent_required: false };
    }
    return { ok: true, json: async () => state };
  });
});

const renewal = () => { state = { ...state, reconsent_required: true, previous_consent_version: 3,
  consent_changes: ['포장 오벌 연습과 무음 진단 기록을 추가합니다.'] }; };

test('existing sender sees one renewal prompt and remains paused after deferring or remounting', async () => {
  const { LogSendSwitch } = require('../desktop/RuntimePreview');
  renewal();
  const view = render(<><LogSendSwitch apiBase="" /><SessionLogAutoSend apiBase="" /></>);
  const dialog = await screen.findByRole('dialog', { name: '로그 전송 동의 내용이 변경되었습니다' });
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(dialog).toHaveTextContent('자동 보내기는 중지되어 있습니다');
  expect(dialog).toHaveTextContent('포장 오벌 연습과 무음 진단 기록을 추가합니다.');
  expect(screen.getByRole('button', { name: '다시 동의하고 켜기' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '나중에' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '로그 자동 보내기' })).toHaveTextContent('로그 재동의');
  expect(fetch.mock.calls.some(([,options]) => options?.method === 'PUT')).toBe(false);
  view.unmount();
  render(<LogSendSwitch apiBase="" />);
  await screen.findByRole('button', { name: '로그 자동 보내기' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '로그 자동 보내기' }));
  expect(screen.getByRole('dialog')).toHaveTextContent('재동의');
});

test('renewal needs a fresh check, saves only after agreement and synchronizes both controls', async () => {
  const { LogSendSwitch } = require('../desktop/RuntimePreview');
  renewal();
  render(<><LogSendSwitch apiBase="" /><SessionLogAutoSend apiBase="" /></>);
  await screen.findByRole('dialog');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '다시 동의하고 켜기' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('switch', { name: '세션 로그 자동 보내기' })).toBeChecked();
  expect(screen.getByRole('button', { name: '로그 자동 보내기' })).toHaveAttribute('aria-pressed', 'true');
  expect(JSON.parse(fetch.mock.calls.find(([,o]) => o?.method === 'PUT')[1].body)).toEqual({ enabled:true,consent_version:4 });
  fireEvent.click(screen.getByRole('button', { name: '로그 자동 보내기' }));
  await waitFor(() => expect(screen.getByRole('switch', { name:'세션 로그 자동 보내기' })).not.toBeChecked());
  fireEvent.click(screen.getByRole('button', { name:'로그 자동 보내기' }));
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});

test('explicitly stopping renewal keeps sending off and clears the reminder', async () => {
  const { LogSendSwitch } = require('../desktop/RuntimePreview');
  renewal();
  render(<LogSendSwitch apiBase="" />);
  await screen.findByRole('dialog');
  fireEvent.click(screen.getByRole('button', { name:'자동 보내기 끄기' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('button', { name:'로그 자동 보내기' })).toHaveTextContent('로그 보내기');
  expect(JSON.parse(fetch.mock.calls.find(([,o]) => o?.method === 'PUT')[1].body)).toEqual({ enabled:false });
});

test('a changed notice during approval reloads its contents and requires a new checkbox', async () => {
  const { LogSendSwitch } = require('../desktop/RuntimePreview');
  renewal();
  fetch.mockImplementation(async (_url, options = {}) => {
    if (options.method === 'PUT') {
      state = { ...state, consent_version:5, consent_changes:['다시 변경된 전송 안내'] };
      return { ok:false, status:422, json:async () => ({ detail:'안내문을 다시 확인한 뒤 켜 주세요.' }) };
    }
    return { ok:true, json:async () => state };
  });
  render(<LogSendSwitch apiBase="" />);
  await screen.findByRole('dialog');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name:'다시 동의하고 켜기' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('안내문을 다시 확인');
  expect(screen.getByRole('dialog')).toHaveTextContent('다시 변경된 전송 안내');
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(screen.getByRole('button', { name:'다시 동의하고 켜기' })).toBeDisabled();
});

test('a save failure leaves renewal paused until the user explicitly retries', async () => {
  const { LogSendSwitch } = require('../desktop/RuntimePreview');
  renewal();
  fetch.mockImplementation(async (_url, options = {}) => options.method === 'PUT'
    ? { ok:false,status:503,json:async () => ({ detail:'합성 저장 실패' }) }
    : { ok:true,json:async () => state });
  render(<LogSendSwitch apiBase="" />);
  await screen.findByRole('dialog');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name:'다시 동의하고 켜기' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('합성 저장 실패');
  expect(screen.getByRole('button', { name:'로그 자동 보내기', hidden: true })).toHaveAttribute('aria-pressed','false');
  expect(fetch.mock.calls.filter(([,o]) => o?.method === 'PUT')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name:'나중에' }));
  fireEvent.click(screen.getByRole('button', { name:'로그 자동 보내기' }));
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});

test('unavailable session storage cannot suppress the required renewal notice', async () => {
  const { LogSendSwitch } = require('../desktop/RuntimePreview');
  renewal();
  const storage=jest.spyOn(Storage.prototype,'getItem').mockImplementation(() => { throw Error('unavailable'); });
  try {
    render(<LogSendSwitch apiBase="" />);
    expect(await screen.findByRole('dialog')).toHaveTextContent('재동의');
    fireEvent.keyDown(screen.getByRole('dialog'), {key:'Escape',code:'Escape'});
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(fetch.mock.calls.some(([,o]) => o?.method === 'PUT')).toBe(false);
  } finally { storage.mockRestore(); }
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
  expect(screen.getByText(/본인의 피트 허용·출발 대열 신호/)).toBeInTheDocument();
  expect(screen.getByText(/2분 이상이면 안내가 없어도/)).toBeInTheDocument();
  expect(screen.getByText(/최대 6구간·1,200행/)).toHaveTextContent('빠진 구간을 표시');
  expect(screen.getByText(/주변 차 기록을 먼저 빼고/)).toBeInTheDocument();
  const accept = screen.getByRole('button', { name: '동의하고 켜기' });
  expect(accept).toBeDisabled();
  expect(global.fetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(accept);
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('켜짐'));
  const put = global.fetch.mock.calls.find(([, options]) => options?.method === 'PUT');
  expect(JSON.parse(put[1].body)).toEqual({ enabled: true, consent_version: 4 });
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
  expect(toggle).not.toHaveTextContent('로그 보내기');
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'false'));
  expect(toggle).toHaveTextContent('로그 보내기');
});
