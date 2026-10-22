import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import CornerNote, { NoteIcon, useCornerNotes } from './CornerNotes';
import { requestJson } from '../analysis/analysisClient';
import CornerNamesPage from './CornerNamesPage';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./Telemetry', () => ({ TrackMap: () => <div /> }));
const corner = { id: 'C1', apex_pct: .123 };
const session = { track_platform_id: 123, track_config: 'GP' };
function Editor({ track = session, selected = corner, game = 'iracing' }) {
  const store = useCornerNotes(track, game);
  return <><CornerNote corner={selected} store={store} /><NoteIcon notes={store.notes} pct={selected.apex_pct} /></>;
}
beforeEach(() => requestJson.mockReset());

test('loads, edits, saves and clears a track note through the API', async () => {
  requestJson.mockImplementation((path, options) => Promise.resolve(options?.method === 'PUT'
    ? { ...JSON.parse(options.body), pct: .125, updated_at: '2026-10-22T00:00:00+00:00' }
    : { notes: [] }));
  render(<Editor />);
  await waitFor(() => expect(screen.getByRole('button', { name: '메모 쓰기' })).toBeEnabled());
  expect(requestJson.mock.calls[0][0]).toBe('/api/corner-notes?game=iracing&track_id=123&track_config=GP');
  fireEvent.click(screen.getByRole('button', { name: '메모 쓰기' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '흰 표식에서 제동합니다.' } });
  fireEvent.click(screen.getByRole('button', { name: '메모 저장' }));
  expect(await screen.findByText('메모를 저장했습니다.')).toBeInTheDocument();
  expect(screen.getByText('흰 표식에서 제동합니다.')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: '내 메모 있음' })).toBeInTheDocument();
  const request = requestJson.mock.calls.find(([, options]) => options?.method === 'PUT');
  expect(JSON.parse(request[1].body)).toEqual({ game: 'iracing', track_id: '123', track_config: 'GP', pct: .123, text: '흰 표식에서 제동합니다.' });
  fireEvent.click(screen.getByRole('button', { name: '메모 편집' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: '메모 저장' }));
  expect(await screen.findByText('메모를 삭제했습니다.')).toBeInTheDocument();
  expect(screen.queryByRole('img', { name: '내 메모 있음' })).not.toBeInTheDocument();
});

test('failed save keeps the draft and explains why it failed', async () => {
  requestJson.mockImplementation((path, options) => options?.method === 'PUT'
    ? Promise.reject(new Error('메모를 저장하지 못했습니다. 파일 접근 권한을 확인해 주세요.')) : Promise.resolve({ notes: [] }));
  render(<Editor />);
  await waitFor(() => expect(screen.getByRole('button', { name: '메모 쓰기' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '메모 쓰기' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '실패 뒤에도 보존할 내용' } });
  fireEvent.click(screen.getByRole('button', { name: '메모 저장' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('파일 접근 권한');
  expect(screen.getByRole('textbox')).toHaveValue('실패 뒤에도 보존할 내용');
  expect(screen.getByRole('button', { name: '메모 저장' })).toBeEnabled();
});

test('Unicode character count permits 300 and prevents saving 301', async () => {
  requestJson.mockResolvedValue({ notes: [] });
  render(<Editor />);
  await waitFor(() => expect(screen.getByRole('button', { name: '메모 쓰기' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '메모 쓰기' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '🏁'.repeat(300) } });
  expect(screen.getByText(/300\/300자/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '메모 저장' })).toBeEnabled();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '가'.repeat(301) } });
  expect(screen.getByRole('button', { name: '메모 저장' })).toBeDisabled();
});

test('track changes ignore late responses and never display or save previous track notes', async () => {
  let resolveOld;
  requestJson.mockImplementation(path => path.includes('track_id=123')
    ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve({ notes: [{ pct: .125, text: '새 트랙 메모' }] }));
  const { rerender } = render(<Editor />);
  rerender(<Editor track={{ ...session, track_platform_id: 'lmu-track-new' }} game="lmu" />);
  expect(await screen.findByText('새 트랙 메모')).toBeInTheDocument();
  await act(async () => resolveOld({ notes: [{ pct: .125, text: '이전 트랙 메모' }] }));
  expect(screen.queryByText('이전 트랙 메모')).not.toBeInTheDocument();
  expect(requestJson.mock.calls[1][0]).toContain('game=lmu&track_id=lmu-track-new');
});

test('changing corners resets an unsaved draft to that corner note', async () => {
  requestJson.mockResolvedValue({ notes: [{ pct: .125, text: '첫 코너' }, { pct: .2, text: '둘째 코너' }] });
  const { rerender } = render(<Editor />);
  await screen.findByText('첫 코너');
  fireEvent.click(screen.getByRole('button', { name: '메모 편집' }));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '저장하지 않은 변경' } });
  rerender(<Editor selected={{ id: 'C2', apex_pct: .2 }} />);
  expect(screen.getByText('둘째 코너')).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(requestJson.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(0);
});

test('unknown track identity and unreadable notes cannot overwrite existing data', async () => {
  const { rerender } = render(<Editor track={{}} />);
  expect(screen.getByText(/트랙과 코너 위치를 확인할 수 없어/)).toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalled();
  rerender(<Editor track={{ track_platform_id: 0 }} />);
  expect(screen.getByText(/트랙과 코너 위치를 확인할 수 없어/)).toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalled();
  requestJson.mockRejectedValue(new Error('메모 파일을 읽지 못했습니다.'));
  rerender(<Editor />);
  expect(await screen.findByRole('alert')).toHaveTextContent('메모 파일을 읽지 못했습니다.');
  expect(screen.getByRole('button', { name: '메모 쓰기' })).toBeDisabled();
});

test('corner-number list displays a note icon only on the matching corner', () => {
  requestJson.mockResolvedValue({ corners: [] });
  const corners = [{ ...corner, entry_pct: .1, exit_pct: .15 }, { id: 'C2', apex_pct: .2, entry_pct: .18, exit_pct: .22 }];
  render(<CornerNamesPage corners={corners} points={[]} names={{}} onSaved={jest.fn()} onBack={jest.fn()}
    cornerNotes={[{ pct: .125, text: '메모가 있는 코너' }]} />);
  const list = screen.getByRole('navigation', { name: '코너 목록' });
  expect(within(list).getAllByRole('img', { name: '내 메모 있음' })).toHaveLength(1);
  expect(within(list).getByRole('img', { name: '내 메모 있음' }).closest('button')).toHaveTextContent('C1');
});
