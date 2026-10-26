import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SourcePicker from './SourcePicker';
import { activateSession, labRequest } from './sessionData';

jest.mock('./sessionData', () => ({ ...jest.requireActual('./sessionData'), labRequest: jest.fn() }));
jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn(async () => ({ health: { enabled: false }, job: null })) }));

test('LMU automatic baseline permits another file with unknown weather and explains the relaxed policy', async () => {
  const laps = [{ lap_number: 2, status: 'UNVERIFIED', measurement_eligible: true, lap_time_s: 120 }];
  const personalReference = { filename: 'fast.duckdb', file_name: 'fast.duckdb', lap: 4, lap_time_s: 117,
    conditions: { warnings: ['SURFACE_CONDITION_UNKNOWN'], status: 'PARTIAL' } };
  const session = { kind: 'LIVE_LOCAL', filename: 'current.duckdb', defaultTarget: 2, defaultReference: 4,
    reference_filename: 'fast.duckdb', reference_selection: 'AUTO', personalReference,
    session: { track_length_m: 1000 }, corners: [], laps };
  activateSession(session);
  labRequest.mockImplementation(async route => {
    if (route === 'files') return { files: [{ name: 'current.duckdb' }, { name: 'fast.duckdb' }] };
    if (route === 'session') return session;
    if (route === 'references') return { files: [], personalReference };
    if (route === 'file-laps') return { laps: [{ ...laps[0], lap_number: 4, lap_time_s: 117 }] };
    throw new Error(route);
  });
  render(<SourcePicker target="2" reference="4" external={[]} onApply={jest.fn()}
    analysisWorkspace={{ game: 'lmu' }} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
  expect(screen.getByLabelText('기준 주행 파일 · LMU')).toHaveValue('fast.duckdb');
  expect(screen.getByText(/비 여부만 크게 구분하고 온도·타이어 차이는 제한하지 않습니다/)).toBeInTheDocument();
  expect(screen.getByText(/날씨 정보가 없는 기록도 다른 파일까지 찾아 비교 기준으로 사용합니다/)).toBeInTheDocument();
  expect(screen.queryByText('노면 상태를 확인할 수 없어 같은 파일에서만 자동 기준을 선택했습니다.')).not.toBeInTheDocument();
  activateSession({ ...session, filename: '', laps: [], corners: [], initialComparison: {} });
});

test('switching IBT files during a trial preserves the frozen Garage61 references', async () => {
  const session = { kind: 'LIVE_LOCAL', filename: 'A.ibt', defaultTarget: 2, defaultReference: 3,
    session: { track_length_m: 1000 }, corners: [], laps: [
      { lap_number: 2, status: 'VALID', lap_time_s: 90 }, { lap_number: 3, status: 'VALID', lap_time_s: 89 }] };
  activateSession(session);
  const result = { analysisResult: { file: 'B.ibt' }, practice: { current: null } };
  labRequest.mockImplementation(async route => {
    if (route === 'files') return { files: [{ name: 'A.ibt' }, { name: 'B.ibt' }] };
    if (route === 'session') return { ...session, filename: 'B.ibt' };
    if (route === 'references') return { files: [{ name: 'frozen.csv', usable: true, compatible: true }] };
    if (route === 'comparison') return result;
    throw new Error(route);
  });
  const onApply = jest.fn();
  render(<SourcePicker target="2" reference="3" external={['frozen.csv']} onApply={onApply}
    analysisWorkspace={{ activeTrial: { reference_mode: 'SINGLE', reference_files: ['frozen.csv'] } }} />);
  await waitFor(() => expect(screen.getByLabelText('주행 파일 · IBT').options).toHaveLength(2));
  fireEvent.change(screen.getByLabelText('주행 파일 · IBT'), { target: { value: 'B.ibt' } });
  await waitFor(() => expect(screen.getByRole('button', { name: '선택한 랩 비교하기' })).toBeEnabled());
  expect(screen.getByLabelText('기준 종류')).toHaveValue('GARAGE61');
  expect(screen.getByLabelText('기준 종류')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '선택한 랩 비교하기' }));
  await waitFor(() => expect(onApply).toHaveBeenCalled());
  expect(labRequest).toHaveBeenCalledWith('comparison', expect.objectContaining({ filename: 'B.ibt',
    reference_mode: 'SINGLE', external_reference_files: ['frozen.csv'] }));
  expect(onApply.mock.calls[0][3]).toEqual(['frozen.csv']);
  activateSession({ ...session, filename: '', laps: [], corners: [], initialComparison: {} });
});
