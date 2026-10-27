import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { ExperimentManager, FileSelect } from './ExperimentPanels';

jest.mock('../components/HelpGuide', () => ({ HelpHint: () => null }));

const files = [
  { name: 'empty.ibt', analyzable: true, valid_laps: 0, car_name: 'Car', track_name: 'Track' },
  { name: 'good.ibt', analyzable: true, valid_laps: 2, car_name: 'Car', track_name: 'Track' },
  { name: 'unknown.ibt', metadata_pending: true, valid_laps: null, car_name: 'Car', track_name: 'Track' },
];

test('legacy file selection disables a known empty run but leaves unknown metadata selectable', () => {
  const change = jest.fn();
  render(<FileSelect label="주행" value="good.ibt" files={files} onChange={change} />);
  fireEvent.click(screen.getByRole('button', { name: /good.ibt/ }));
  expect(screen.getByRole('option', { name: /empty.ibt/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('option', { name: /empty.ibt/ }));
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('option', { name: /unknown.ibt/ }));
  expect(change).toHaveBeenCalledWith('unknown.ibt');
});

test('legacy experiments skip a known empty preferred baseline without changing a frozen trial', async () => {
  const view = render(<ExperimentManager files={files} preferredBaseline="empty.ibt" sessionMode="BASELINE_3LAP" />);
  expect(await screen.findByRole('button', { name: /good.ibt/ })).toBeInTheDocument();
  view.unmount();
  const trial = { id: 'saved', baseline_file: 'empty.ibt', candidate_file: 'good.ibt',
    context: files[0], reference_mode: 'SINGLE', reference_files: ['saved.csv'] };
  render(<ExperimentManager files={files} trial={trial} sessionMode="BASELINE_3LAP" />);
  expect(await screen.findByRole('button', { name: /empty.ibt/ })).toBeDisabled();
  expect(trial.reference_files).toEqual(['saved.csv']);
  expect(trial.baseline_file).toBe('empty.ibt');
  expect(screen.getByRole('button', { name: 'A/B 결과 확인' })).toBeDisabled();
});
