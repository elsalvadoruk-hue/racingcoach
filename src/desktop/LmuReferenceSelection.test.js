import React from 'react';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import SourcePicker from './SourcePicker';
import { requestJson } from '../analysis/analysisClient';
import { useLmuWorkspace } from '../analysis/useLmuWorkspace';
import { activateSession, data, LAST_SESSION_KEY, LMU_SESSION_KEY, rememberSession, selectGame } from './sessionData';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));
const A = 'aaaaaaaaaaaaaaaaaaaaaaaa', B = 'bbbbbbbbbbbbbbbbbbbbbbbb', C = 'cccccccccccccccccccccccc';
const lap = n => ({ lap_number: n, lap_time_s: 117 + n, status: 'VALIDITY_UNKNOWN', valid: null, complete: true, measurement_eligible: true });
const ownLaps = [lap(1), lap(2)], referenceLaps = [lap(7), lap(8)];
const files = [A, B, C].map((name, index) => ({ name, file_name: `${['current', 'reference', 'third'][index]}.duckdb`, display_name: `${['current', 'reference', 'third'][index]}.duckdb`, analyzable: true }));
const comparison = { provider: 'LMU', target_lap: 2, reference_lap: 7, reference_filename: B, corners: [], trace: [] };
const sourceSession = { provider: 'LMU', kind: 'LIVE_LOCAL', filename: A, file_name: 'current.duckdb', defaultTarget: 2, defaultReference: 1,
  session: { track_name: 'Monza', car_name: 'GT3', track_length_m: 5793 }, laps: ownLaps, corners: [], initialComparison: {}, external: [] };
let override;

beforeEach(() => {
  localStorage.clear(); selectGame('lmu'); activateSession(sourceSession); override = () => undefined; requestJson.mockReset();
  requestJson.mockImplementation((path, options = {}) => {
    const body = options.body instanceof File ? options.body : options.body ? JSON.parse(options.body) : {};
    const custom = override(path, body, options);
    if (custom !== undefined) return custom;
    const route = path.split('/').pop();
    if (route === 'files') return Promise.resolve({ files });
    if (route === 'references') return Promise.resolve({ files: [] });
    if (route === 'file-laps') return Promise.resolve({ laps: body.filename === B ? referenceLaps : [lap(9)] });
    if (route === 'comparison') return Promise.resolve(comparison);
    if (route === 'session') return Promise.resolve({ ...sourceSession, ...body, defaultTarget: body.target_lap ?? 2, defaultReference: body.reference_lap ?? 1,
      reference_laps: body.reference_filename === B ? referenceLaps : ownLaps, initialComparison: comparison });
    return Promise.reject(new Error(`Unexpected endpoint ${path}`));
  });
});
afterEach(() => selectGame('iracing'));

function picker(onApply = jest.fn()) {
  return render(<SourcePicker target="2" reference="1" external={[]} analysisWorkspace={{ game: 'lmu' }} onApply={onApply} />);
}
async function ready() {
  await waitFor(() => expect(screen.getByLabelText('기준 주행 파일 · LMU').options).toHaveLength(3));
  await waitFor(() => expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
}
const callBody = route => JSON.parse(requestJson.mock.calls.find(([path]) => path.endsWith(`/${route}`))[1].body);

test('opens a native file input and imports a downloaded DuckDB directly as the manual reference', async () => {
  const imported = 'download-123456789abc.duckdb';
  override = path => path.includes('/telemetry/import?') ? Promise.resolve({ file: { name: imported, analyzable: true } })
    : path.endsWith('/comparison') ? Promise.resolve({ ...comparison, reference_filename: imported, reference_lap: 9 }) : undefined;
  const onApply = jest.fn(); picker(onApply); await ready();
  const input = screen.getByLabelText('열 DuckDB 파일');
  expect(input).toHaveAttribute('type', 'file'); expect(input).toHaveAttribute('accept', '.duckdb');
  const click = jest.spyOn(input, 'click');
  fireEvent.click(screen.getByRole('button', { name: '기준 DuckDB 파일 열기…' }));
  expect(click).toHaveBeenCalledTimes(1);
  const file = new File(['synthetic duckdb'], '다운로드.duckdb');
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(screen.getByLabelText('비교 기준')).toHaveValue('9'));
  expect(screen.getByLabelText('기준 선택 방식')).toHaveValue('MANUAL');
  expect(screen.getByLabelText('기준 주행 파일 · LMU')).toHaveValue(imported);
  expect(screen.getByLabelText('주행 파일 · LMU DuckDB')).toHaveValue(A);
  const upload = requestJson.mock.calls.find(([path]) => path.includes('/telemetry/import?'));
  expect(upload[1].body).toBe(file); expect(upload[1].headers['Content-Type']).toBe('application/octet-stream');
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
  expect(callBody('comparison')).toMatchObject({ filename: A, reference_filename: imported, reference_lap: 9 });
});

test('can import a DuckDB as the current driving file', async () => {
  override = path => path.includes('/telemetry/import?') ? Promise.resolve({ file: { name: 'new.duckdb', analyzable: true } }) : undefined;
  picker(); await ready();
  fireEvent.click(screen.getByRole('button', { name: 'DuckDB 파일 열기…' }));
  fireEvent.change(screen.getByLabelText('열 DuckDB 파일'), { target: { files: [new File(['fixture'], 'new.duckdb')] } });
  await waitFor(() => expect(screen.getByLabelText('주행 파일 · LMU DuckDB')).toHaveValue('new.duckdb'));
  await waitFor(() => expect(requestJson.mock.calls.some(([path, options]) => path.endsWith('/session') && JSON.parse(options.body).filename === 'new.duckdb')).toBe(true));
});

test('cancel and import errors preserve the selected reference and allow another attempt', async () => {
  override = path => path.includes('/telemetry/import?') ? Promise.reject(new Error('LMU 데이터베이스를 읽을 수 없습니다.')) : undefined;
  picker(); await ready();
  const input = screen.getByLabelText('열 DuckDB 파일');
  fireEvent.change(input, { target: { files: [] } });
  expect(requestJson.mock.calls.some(([path]) => path.includes('/telemetry/import?'))).toBe(false);
  fireEvent.change(input, { target: { files: [new File(['bad'], 'bad.duckdb')] } });
  expect(await screen.findByRole('alert')).toHaveTextContent('LMU 데이터베이스를 읽을 수 없습니다.');
  expect(screen.getByLabelText('기준 주행 파일 · LMU')).toHaveValue(A);
  expect(screen.getByRole('button', { name: '기준 DuckDB 파일 열기…' })).toBeEnabled();
});

test('unmount aborts an import and ignores its delayed result', async () => {
  let pending;
  override = (path, body, options) => path.includes('/telemetry/import?') ? new Promise(resolve => { pending = { resolve, signal: options.signal }; }) : undefined;
  const onApply = jest.fn(); const view = picker(onApply); await ready();
  fireEvent.change(screen.getByLabelText('열 DuckDB 파일'), { target: { files: [new File(['fixture'], 'new.duckdb')] } });
  expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeDisabled();
  view.unmount(); expect(pending.signal.aborted).toBe(true);
  await act(async () => pending.resolve({ file: { name: 'late.duckdb' } }));
  expect(onApply).not.toHaveBeenCalled(); expect(data.filename).toBe(A);
});

test('iRacing has no DuckDB picker or import buttons', async () => {
  selectGame('iracing'); activateSession({ ...sourceSession, provider: 'iracing' });
  render(<SourcePicker target="2" reference="1" external={[]} analysisWorkspace={{ game: 'iracing' }} onApply={jest.fn()} />);
  await waitFor(() => expect(screen.getByLabelText('기준 주행 파일 · IBT').options).toHaveLength(3));
  expect(screen.queryByLabelText('열 DuckDB 파일')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /DuckDB 파일 열기/ })).not.toBeInTheDocument();
});

test('applies another file reference with its own lap options and precise comparison identities', async () => {
  const onApply = jest.fn(); picker(onApply); await ready();
  fireEvent.change(screen.getByLabelText('기준 주행 파일 · LMU'), { target: { value: B } });
  await waitFor(() => expect(screen.getByLabelText('비교 기준')).toHaveValue('8'));
  expect([...screen.getByLabelText('내 주행').options].map(option => option.value)).toEqual(['1', '2']);
  expect([...screen.getByLabelText('비교 기준').options].map(option => option.value)).toEqual(['7', '8']);
  fireEvent.change(screen.getByLabelText('비교 기준'), { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
  expect(callBody('comparison')).toMatchObject({ filename: A, reference_filename: B, target_lap: 2, reference_lap: 7, external_reference_files: [], reference_mode: 'LOCAL' });
  expect(onApply.mock.calls[0]).toEqual([expect.objectContaining({ filename: A, reference_filename: B, reference_file_name: 'reference.duckdb', reference_laps: referenceLaps }), '2', '7', [], comparison]);
  expect(requestJson.mock.calls.every(([path]) => path.startsWith('/api/lmu/'))).toBe(true);
});

test('remembers and restores another-file reference without changing the iRacing session key', async () => {
  const savedSession = { ...sourceSession, reference_filename: B, reference_file_name: 'reference.duckdb', reference_laps: referenceLaps };
  localStorage.setItem(LAST_SESSION_KEY, 'preserved iRacing selection');
  rememberSession(savedSession, '2', '7', []);
  expect(JSON.parse(localStorage.getItem(LMU_SESSION_KEY))).toMatchObject({ filename: A, target_lap: 2, reference_lap: 7, reference_filename: B });
  selectGame('lmu');
  const { result } = renderHook(useLmuWorkspace);
  await waitFor(() => expect(result.current.initialLoading).toBe(false));
  expect(callBody('session')).toMatchObject({ filename: A, reference_filename: B, target_lap: 2, reference_lap: 7 });
  expect(data.reference_filename).toBe(B);
  expect(data.reference_laps).toEqual(referenceLaps);
  expect(data.defaultReference).toBe(7);
  expect(JSON.parse(localStorage.getItem(LMU_SESSION_KEY)).reference_filename).toBe(B);
  expect(localStorage.getItem(LAST_SESSION_KEY)).toBe('preserved iRacing selection');
});

test('incompatible reference rejection preserves the current session and displays the server explanation', async () => {
  override = path => path.endsWith('/comparison') ? Promise.reject(Object.assign(new Error('차량·트랙·레이아웃이 달라 비교할 수 없습니다.'), { status: 422 })) : undefined;
  const onApply = jest.fn(); picker(onApply); await ready();
  fireEvent.change(screen.getByLabelText('기준 주행 파일 · LMU'), { target: { value: B } });
  await waitFor(() => expect(screen.getByLabelText('비교 기준')).toHaveValue('8'));
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('차량·트랙·레이아웃이 달라 비교할 수 없습니다.');
  expect(onApply).not.toHaveBeenCalled(); expect(data.filename).toBe(A); expect(data.reference_filename).toBeUndefined();
  expect(localStorage.getItem(LMU_SESSION_KEY)).toBeNull();
});

test('a later reference file wins over a delayed lap list and aborts the stale request', async () => {
  let pending;
  override = (path, body, options) => path.endsWith('/file-laps') && body.filename === B ? new Promise(resolve => { pending = { resolve, signal: options.signal }; }) : undefined;
  picker(); await ready();
  fireEvent.change(screen.getByLabelText('기준 주행 파일 · LMU'), { target: { value: B } });
  await waitFor(() => expect(pending).toBeDefined());
  expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('기준 주행 파일 · LMU'), { target: { value: C } });
  await waitFor(() => expect(screen.getByLabelText('비교 기준')).toHaveValue('9'));
  expect(pending.signal.aborted).toBe(true);
  await act(async () => pending.resolve({ laps: referenceLaps }));
  expect([...screen.getByLabelText('비교 기준').options].map(option => option.value)).toEqual(['9']);
  expect(screen.getByLabelText('기준 주행 파일 · LMU')).toHaveValue(C);
});

test('switching back to the current file cancels the other-file lap request and restores local options', async () => {
  let pending;
  override = (path, body, options) => path.endsWith('/file-laps') && body.filename === B ? new Promise(resolve => { pending = { resolve, signal: options.signal }; }) : undefined;
  picker(); await ready();
  fireEvent.change(screen.getByLabelText('기준 주행 파일 · LMU'), { target: { value: B } });
  await waitFor(() => expect(pending).toBeDefined());
  fireEvent.change(screen.getByLabelText('기준 주행 파일 · LMU'), { target: { value: A } });
  expect(pending.signal.aborted).toBe(true);
  await act(async () => pending.resolve({ laps: referenceLaps }));
  expect([...screen.getByLabelText('비교 기준').options].map(option => option.value)).toEqual(['1', '2']);
  expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled();
});

test('game selection and unmount cancel the reference request without applying late LMU data', async () => {
  let pending; const onApply = jest.fn();
  override = (path, body, options) => path.endsWith('/file-laps') && body.filename === B ? new Promise(resolve => { pending = { resolve, signal: options.signal }; }) : undefined;
  function Harness() {
    const [game, setGame] = React.useState('lmu');
    return <><label><input type="radio" name="test-game" checked={game === 'iracing'} onChange={() => { selectGame('iracing'); setGame('iracing'); }} />iRacing 선택</label>{game === 'lmu' && <SourcePicker target="2" reference="1" external={[]} analysisWorkspace={{ game: 'lmu' }} onApply={onApply} />}</>;
  }
  render(<Harness />); await ready();
  fireEvent.change(screen.getByLabelText('기준 주행 파일 · LMU'), { target: { value: B } });
  await waitFor(() => expect(pending).toBeDefined());
  fireEvent.click(screen.getByLabelText('iRacing 선택'));
  expect(pending.signal.aborted).toBe(true);
  await act(async () => pending.resolve({ laps: referenceLaps }));
  expect(onApply).not.toHaveBeenCalled(); expect(data.provider).toBe('iracing');
  expect(screen.queryByLabelText('비교 기준')).not.toBeInTheDocument();
});
