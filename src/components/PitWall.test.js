import { GameContext } from '../lib/gameContext';
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import LiveCoach from './LiveCoach';
import { PIT_WALL_KEY } from './PitWall';
import useAudioOutput from '../useAudioOutput';
import { VoiceRecorder } from '../voiceAudio';

jest.mock('../useAudioOutput');
let output, pendingReport, count, fetched;
const originalSocket = global.WebSocket, originalFetch = global.fetch;
const report = { note: { id: 'auto-1', created_at: 1000, text: '자동 보고', source: 'AUTO_REPORT', reply_source: 'MEASURED', reply: '완주 연료가 부족합니다.', feelings: [], context: { engineer: 'strategy' } }, audio_base64: 'auto-audio', warning: '' };
beforeEach(() => {
  localStorage.clear(); count = 0; fetched = []; pendingReport = null;
  global.WebSocket = jest.fn(() => ({ close: jest.fn() }));
  output = { devices: [], outputId: '', supported: false, refresh: jest.fn(), changeOutput: jest.fn(), play: jest.fn(async () => {}), stop: jest.fn(), volume: 1, muted: false, changeVolume: jest.fn(), toggleMute: jest.fn(), isPlaying: false };
  useAudioOutput.mockImplementation(() => output);
  global.fetch = jest.fn(async (url, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    fetched.push({ url, body });
    if (url.endsWith('/reports')) {
      if (pendingReport && body.level !== 'off') return pendingReport;
      return { ok: true, json: async () => ({ pit_wall: { active: true, class_position: 2, fuel_to_add_l: 4, remaining_laps: 6.5 }, report: body.level !== 'off' && count++ === 0 ? report : null }) };
    }
    if (url.includes('/voices/')) return { ok: true, json: async () => ({ audio_base64: 'sample-audio' }) };
    return { ok: true, json: async () => ({ notes: [] }) };
  });
});
afterEach(() => { global.WebSocket = originalSocket; global.fetch = originalFetch; jest.useRealTimers(); jest.restoreAllMocks(); });

test('LMU pit wall edits preserve iRacing values and reports apply only the connected game', async () => {
  localStorage.setItem(PIT_WALL_KEY, JSON.stringify({ reserve_laps: .8 }));
  let context = { selectedGame: 'lmu', activeGame: 'iracing', liveGame: 'iracing' };
  const app = () => <GameContext.Provider value={context}><LiveCoach apiBase="http://test" visible /></GameContext.Provider>;
  const { rerender } = render(app());
  expect(screen.getByLabelText('완주 여유 연료 랩')).toHaveValue(.8);
  fireEvent.change(screen.getByLabelText('완주 여유 연료 랩'), { target: { value: '2' } });
  expect(JSON.parse(localStorage.getItem(`${PIT_WALL_KEY}.lmu`)).reserve_laps).toBe(2);
  expect(JSON.parse(localStorage.getItem(PIT_WALL_KEY)).reserve_laps).toBe(.8);
  await waitFor(() => expect(fetched.some(r => r.url.endsWith('/reports') && r.body.reserve_laps === .8)).toBe(true));
  context = { ...context, activeGame: 'lmu', liveGame: 'lmu' }; rerender(app());
  await waitFor(() => expect(fetched.some(r => r.url.endsWith('/reports') && r.body.reserve_laps === 2)).toBe(true));
  context = { ...context, selectedGame: 'iracing' }; rerender(app());
  expect(screen.getByLabelText('완주 여유 연료 랩')).toHaveValue(.8);
});

test('editing an inactive game microphone preserves the active recorder and next game uses its own device', async () => {
  localStorage.setItem('iracing.voice.inputDevice', 'iracing-mic');
  const open = jest.spyOn(VoiceRecorder.prototype, 'open').mockResolvedValue();
  const close = jest.spyOn(VoiceRecorder.prototype, 'close').mockImplementation(() => {});
  let context = { selectedGame: 'lmu', activeGame: 'iracing', liveGame: 'iracing' }, host;
  const app = () => <GameContext.Provider value={context}><LiveCoach apiBase="http://test" visible>{value => { host = value; return null; }}</LiveCoach></GameContext.Provider>;
  const { rerender } = render(app());
  await act(async () => host.prepare());
  expect(open).toHaveBeenLastCalledWith('iracing-mic');
  act(() => host.changeInputDevice('lmu-mic'));
  expect(host.armed).toBe(true); expect(close).not.toHaveBeenCalled();
  expect(localStorage.getItem('iracing.voice.inputDevice')).toBe('iracing-mic');
  context = { ...context, activeGame: 'lmu', liveGame: 'lmu' }; rerender(app());
  expect(close).toHaveBeenCalledTimes(1); expect(host.armed).toBe(false);
  await act(async () => host.prepare());
  expect(open).toHaveBeenLastCalledWith('lmu-mic');
});

test('three male voices, preferences and automatic reports use the common audio output', async () => {
  render(<LiveCoach apiBase="http://test" visible />);
  const panel = screen.getByRole('region', { name: '레이스 피트월' });
  expect(within(panel).getByLabelText('자동 보고 범위')).toBeVisible();
  expect(within(panel).getByText(/Algenib/)).toBeVisible();
  expect(within(panel).getByText(/Charon/)).toBeVisible();
  expect(within(panel).getByText(/Orus/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('예상 피트 총손실 초'), { target: { value: '25' } });
  fireEvent.change(screen.getByLabelText('완주 여유 연료 랩'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('연료 절약 목표'), { target: { value: 'reserve' } });
  expect(JSON.parse(localStorage.getItem(`${PIT_WALL_KEY}.iracing`))).toMatchObject({ pit_loss_s: 25, reserve_laps: 1 });
  expect(JSON.parse(localStorage.getItem(`${PIT_WALL_KEY}.iracing`)).fuel_save_mode).toBe('reserve');
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 시작' }));
  await screen.findByText('완주 연료가 부족합니다.');
  expect(output.play).toHaveBeenCalledWith('auto-audio', 'audio/mpeg', expect.objectContaining({ onStatus: expect.any(Function) }));
  expect(fetched.some((r) => r.body?.level === 'important' && r.body.reserve_laps === 1 && r.body.pit_loss_s === 25)).toBe(true);
  expect(fetched.some((r) => r.body?.fuel_save_mode === 'reserve')).toBe(true);
});

test('voice previews dispatch each engineer voice', async () => {
  render(<LiveCoach apiBase="http://test" visible />);
  for (const [index, role] of ['coach', 'race', 'strategy'].entries()) {
    fireEvent.click(screen.getAllByRole('button', { name: '목소리 들어보기' })[index]);
    await waitFor(() => expect(fetched.some((r) => r.url.endsWith(`/voices/${role}/test`))).toBe(true));
    await waitFor(() => expect(screen.getAllByRole('button', { name: '목소리 들어보기' })[index]).toBeEnabled());
  }
  expect(output.play).toHaveBeenCalledTimes(3);
});

test('muted automatic reports are text only and continue outside the voice tab', async () => {
  output.muted = true;
  const { rerender } = render(<LiveCoach apiBase="http://test" visible />);
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 시작' }));
  await screen.findByText('완주 연료가 부족합니다.');
  expect(fetched.some((r) => r.body?.level === 'important' && r.body.speak === false)).toBe(true);
  expect(output.play).not.toHaveBeenCalled();
  rerender(<LiveCoach apiBase="http://test" visible={false} />);
  await waitFor(() => expect(fetched.filter((r) => r.body?.level === 'important').length).toBeGreaterThan(1));
});

test('automatic reports wait while another answer is playing', async () => {
  output.isPlaying = true;
  render(<LiveCoach apiBase="http://test" visible />);
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 시작' }));
  await waitFor(() => expect(screen.getByText(/엔지니어 발화 중/)).toBeVisible());
  expect(fetched.filter((r) => r.url.endsWith('/reports'))).toHaveLength(0);
});

test('stopping automatic reports cancels late cloud audio', async () => {
  let finish;
  pendingReport = new Promise((resolve) => { finish = resolve; });
  render(<LiveCoach apiBase="http://test" visible />);
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 시작' }));
  await waitFor(() => expect(fetched.some((r) => r.body?.level === 'important')).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 중지' }));
  await act(async () => finish({ ok: true, json: async () => ({ report }) }));
  expect(output.play).not.toHaveBeenCalled();
});

test('questions default to measured answers and only explicit AI selection requests AI', async () => {
  const fallback = global.fetch;
  let questionCount = 0;
  global.fetch = jest.fn(async (url, options) => {
    if (url.endsWith('/captures')) return { ok: true, json: async () => ({ capture_id: `capture-${questionCount}` }) };
    if (url.endsWith('/utterances')) {
      const body = JSON.parse(options.body); fetched.push({ url, body }); questionCount += 1;
      return { ok: true, json: async () => ({ note: { id: `question-${questionCount}`, created_at: 1000,
        text: body.text, source: 'TEXT', feelings: [], reply: body.reply ? '답변입니다.' : '',
        reply_source: body.analysis_mode === 'ai' ? 'VERTEX_AI' : 'LOCAL', context: { engineer: 'coach' } } }) };
    }
    return fallback(url, options);
  });
  render(<LiveCoach apiBase="http://test" visible />);
  expect(screen.getByRole('button', { name: '측정값 답변', exact: true })).toHaveAttribute('aria-pressed', 'true');
  let expectedCount = 0;
  for (const [mode, label, reply] of [['measured', '측정값 답변', true], ['ai', 'AI 원인 분석', true], ['measured', '메모만 기록', false]]) {
    fireEvent.click(screen.getByRole('button', { name: label, exact: true }));
    fireEvent.change(screen.getByLabelText('텍스트로 질문하거나 느낌 남기기'), { target: { value: '방금 코너 어땠어?' } });
    fireEvent.click(screen.getByRole('button', { name: '기록 보내기' }));
    expectedCount += 1;
    await waitFor(() => expect(fetched.filter((r) => r.url.endsWith('/utterances'))).toHaveLength(expectedCount));
    await waitFor(() => expect(screen.getByRole('button', { name: label, exact: true })).toBeEnabled());
    expect(fetched.filter((r) => r.url.endsWith('/utterances')).at(-1).body).toMatchObject({ analysis_mode: mode, reply });
  }
  expect(fetched.filter((r) => r.url.endsWith('/utterances')).map((r) => r.body.analysis_mode)).toEqual(['measured', 'ai', 'measured']);
});

test('a report cut off by a more urgent call stays undelivered and is said again', async () => {
  output.play = jest.fn(async () => true);
  pendingReport = { ok: true, json: async () => ({ pit_wall: { active: true }, report: { ...report, priority: 2, spoken_text: '두 랩 안에 피트합니다.' } }) };
  render(<LiveCoach apiBase="http://test" visible />);
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 시작' }));
  await waitFor(() => expect(output.play).toHaveBeenCalledTimes(1), { timeout: 4000 });
  const acks = () => fetched.filter(({ url }) => url.endsWith('/reports/auto-1/ack')).length;
  await act(async () => output.play.mock.calls[0][2].onStatus('interrupted', 'preempted'));
  expect(acks()).toBe(0);
  await waitFor(() => expect(output.play).toHaveBeenCalledTimes(2), { timeout: 5000 });
  await act(async () => output.play.mock.calls[1][2].onStatus('completed'));
  await waitFor(() => expect(acks()).toBe(1));
}, 15000);

test.each(['disconnect', 'new session'])('urgent fuel speech is invalidated by %s before playback starts', async (transition) => {
  const fallback = global.fetch;
  let currentStatus = { connected: true, snapshot: { run_id: 'old-run', brake: 1, steering_rad: 1, speed_mps: 30 } };
  global.fetch = jest.fn(async (url, options) => url.endsWith('/status')
    ? { ok: true, json: async () => currentStatus } : fallback(url, options));
  output.play = jest.fn(async () => true);
  pendingReport = { ok: true, json: async () => ({ pit_wall: { active: true }, report: {
    ...report, priority: 3, note: { ...report.note, context: { ...report.note.context, snapshot: { run_id: 'old-run' } } },
  } }) };
  render(<LiveCoach apiBase="http://test" visible />);
  fireEvent.click(screen.getByRole('button', { name: '자동 보고 시작' }));
  await waitFor(() => expect(output.play).toHaveBeenCalledTimes(1));
  const options = output.play.mock.calls[0][2];
  expect(options.valid()).toBe(true); // Urgency still bypasses braking and steering.
  currentStatus = transition === 'disconnect' ? { connected: false, snapshot: {} }
    : { connected: true, snapshot: { run_id: 'new-run', brake: 0, steering_rad: 0, speed_mps: 30 } };
  await waitFor(() => expect(options.valid()).toBe(false), { timeout: 2500 });
  await act(async () => options.onStatus('cancelled', 'realtime_state_changed'));
});
