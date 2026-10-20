import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import CornerNamesPage from './CornerNamesPage';
import { activateSession, selectGame } from './sessionData';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
jest.mock('./Telemetry', () => ({ TrackMap: ({ schematic }) => <div data-testid="names-track-map" data-schematic={String(schematic)} /> }));

const FILE = 'aaaaaaaaaaaaaaaaaaaaaaaa', REFERENCE = 'bbbbbbbbbbbbbbbbbbbbbbbb';
const corners = [
  { id: 'C1', label: '자동 구간 1', entry_pct: .1, apex_pct: .15, exit_pct: .2 },
  { id: 'C2', label: '자동 구간 2', entry_pct: .4, apex_pct: .45, exit_pct: .5 },
];
const emptyNames = { C1: { number: '', name: '', spoken: '' }, C2: { number: '', name: '', spoken: '' } };
const payload = (confirmed = true) => ({ provider: 'LMU', confirmed, corners: corners.map((corner, index) => ({
  ...corner, number: confirmed ? String(index + 1) : null, name: '', spoken: '', suggested_number: String(index + 1),
})), official_map: { assets: { active: '/track-maps/official/239/active.svg', turns: '/track-maps/official/239/turns.svg' } } });
const props = overrides => ({ game: 'lmu', corners, points: [], names: emptyNames, onSaved: jest.fn(), onBack: jest.fn(), ...overrides });

beforeEach(() => {
  requestJson.mockReset(); selectGame('lmu');
  activateSession({ filename: FILE, reference_filename: REFERENCE, defaultTarget: 7, defaultReference: 2,
    session: { track_name: 'Monza', track_config: 'Grand Prix', track_length_m: 5800 }, laps: [], corners });
});
afterEach(() => selectGame('iracing'));

test('LMU loads the exact current comparison identity and confirmed names through LMU services only', async () => {
  requestJson.mockResolvedValue(payload());
  const onSaved = jest.fn();
  render(<CornerNamesPage {...props({ onSaved })} />);
  expect(await screen.findByText(/확정된 번호입니다. 같은 LMU 트랙/)).toBeInTheDocument();
  const [path, options] = requestJson.mock.calls[0];
  const url = new URL(path, 'http://localhost:24157');
  expect(url.pathname).toBe('/api/lmu/corner-names');
  expect(Object.fromEntries(url.searchParams)).toEqual({ filename: FILE, target_lap: '7', reference_lap: '2', reference_filename: REFERENCE });
  expect(options.signal).toBeInstanceOf(AbortSignal);
  expect(screen.getByLabelText('코너 번호')).toHaveValue('1');
  expect(screen.getByTestId('names-track-map')).toHaveAttribute('data-schematic', 'true');
  expect(screen.getByRole('heading', { name: 'iRacing 참조 지도' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'iRacing Monza 트랙 지도' })).toHaveAttribute('src', '/track-maps/official/239/active.svg');
  expect(onSaved).toHaveBeenCalledWith({ C1: { number: '1', name: '', spoken: '' }, C2: { number: '2', name: '', spoken: '' } });
  expect(screen.getByRole('button', { name: '수정 내용 확정' })).toBeDisabled();
  expect(requestJson.mock.calls.every(([called]) => called.startsWith('/api/lmu/'))).toBe(true);
});

test('map suggestions remain drafts until explicit confirmation and PUT uses server-returned names', async () => {
  const confirmed = payload(); confirmed.corners[0] = { ...confirmed.corners[0], number: '1-2', name: '레티필로', spoken: '레티필로 진입' };
  requestJson.mockImplementation((path, options = {}) => Promise.resolve(options.method === 'PUT' ? confirmed : payload(false)));
  const onSaved = jest.fn();
  render(<CornerNamesPage {...props({ onSaved })} />);
  expect(await screen.findByText(/지도 제안을 확인하거나/)).toBeInTheDocument();
  expect(screen.getByLabelText('코너 번호')).toHaveValue('');
  expect(onSaved).toHaveBeenLastCalledWith(emptyNames);
  const callsBeforeDraft = onSaved.mock.calls.length;
  fireEvent.click(screen.getByRole('button', { name: '공식 지도 제안 적용' }));
  expect(screen.getByLabelText('코너 번호')).toHaveValue('1');
  expect(screen.getByRole('button', { name: '코너 비교로' })).toBeDisabled();
  expect(onSaved).toHaveBeenCalledTimes(callsBeforeDraft);
  fireEvent.change(screen.getByLabelText('코너 이름'), { target: { value: ' 레티필로 ' } });
  fireEvent.change(screen.getByLabelText('음성으로 부를 말 · 선택'), { target: { value: '레티필로 진입' } });
  expect(requestJson.mock.calls.filter(([, options = {}]) => options.method === 'PUT')).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '수정 내용 확정' }));
  expect(await screen.findByText(/확정했습니다. 같은 LMU 트랙/)).toBeInTheDocument();
  const [path, options] = requestJson.mock.calls.find(([, opts = {}]) => opts.method === 'PUT');
  expect(path).toBe('/api/lmu/corner-names');
  expect(JSON.parse(options.body)).toEqual({ filename: FILE, target_lap: 7, reference_lap: 2, reference_filename: REFERENCE,
    corners: [{ id: 'C1', number: '1', name: ' 레티필로 ', spoken: '레티필로 진입' }, { id: 'C2', number: '2', name: '', spoken: '' }] });
  expect(screen.getByLabelText('코너 번호')).toHaveValue('1-2');
  expect(screen.getByLabelText('코너 이름')).toHaveValue('레티필로');
  expect(onSaved).toHaveBeenLastCalledWith({ C1: { number: '1-2', name: '레티필로', spoken: '레티필로 진입' }, C2: { number: '2', name: '', spoken: '' } });
  expect(screen.getByRole('button', { name: '수정 내용 확정' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '코너 비교로' })).toBeEnabled();
  expect(requestJson.mock.calls.every(([called]) => called.startsWith('/api/lmu/'))).toBe(true);
});

test('failed LMU confirmation preserves editable draft and does not publish it to the comparison', async () => {
  requestJson.mockImplementation((path, options = {}) => options.method === 'PUT' ? Promise.reject(new Error('저장할 현재 구간이 달라졌습니다.')) : Promise.resolve(payload()));
  const onSaved = jest.fn();
  render(<CornerNamesPage {...props({ onSaved })} />);
  await screen.findByText(/확정된 번호입니다/);
  fireEvent.change(screen.getByLabelText('코너 번호'), { target: { value: '9' } });
  fireEvent.click(screen.getByRole('button', { name: '수정 내용 확정' }));
  expect(await screen.findByText('저장할 현재 구간이 달라졌습니다.')).toBeInTheDocument();
  expect(screen.getByLabelText('코너 번호')).toHaveValue('9');
  expect(screen.getByRole('button', { name: '수정 내용 확정' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '코너 비교로' })).toBeDisabled();
  expect(onSaved).toHaveBeenCalledTimes(1);
});

test('iRacing corner names retain the original filename-only endpoint contract', async () => {
  requestJson.mockResolvedValue(payload());
  render(<CornerNamesPage {...props({ game: 'iracing' })} />);
  await waitFor(() => expect(screen.getByLabelText('코너 번호')).toHaveValue('1'));
  const url = new URL(requestJson.mock.calls[0][0], 'http://localhost:24157');
  expect(url.pathname).toBe('/api/corner-names');
  expect(Object.fromEntries(url.searchParams)).toEqual({ filename: FILE });
  expect(screen.getByTestId('names-track-map')).toHaveAttribute('data-schematic', 'false');
});
