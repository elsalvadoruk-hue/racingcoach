import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SourcePicker from './SourcePicker';
import { activateSession, labRequest } from './sessionData';

jest.mock('./sessionData', () => ({ ...jest.requireActual('./sessionData'), labRequest: jest.fn() }));

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
