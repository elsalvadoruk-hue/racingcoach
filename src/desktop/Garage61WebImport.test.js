import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Garage61WebImport, { GARAGE61_LAPS_URL } from './Garage61WebImport';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));

const NAME = 'Garage 61 - Jane Doe - BMW M4 GT3 EVO - Long Beach Street Circuit (Unknown) - 01.18.898 - 01M3HJNV67E0C95DYS6DXM80Q1.csv';
const listing = files => ({ available: true, directory: 'Downloads', files });

beforeEach(() => requestJson.mockReset());

test('opens Garage61 in the browser and lists new downloads only', async () => {
  requestJson.mockResolvedValue(listing([
    { name: NAME, driver_name: 'Jane Doe', car_name: 'BMW M4 GT3 EVO', track_name: 'Long Beach Street Circuit', lap_time_s: 78.898, imported: false },
    { name: NAME.replace('Q1', 'Q2'), driver_name: 'Kept', lap_time_s: 79, imported: true },
  ]));
  render(<Garage61WebImport carName="BMW M4 GT3 EVO" trackName="Long Beach" onImported={jest.fn()} />);
  const link = screen.getByRole('link', { name: 'Garage61에서 랩 찾기' });
  expect(link).toHaveAttribute('href', GARAGE61_LAPS_URL);
  expect(link).toHaveAttribute('target', '_blank');
  expect(link.getAttribute('rel')).toContain('noopener');
  expect(screen.getByText('BMW M4 GT3 EVO · Long Beach')).toBeInTheDocument();
  expect(await screen.findByText(/Jane Doe · 1:18\.898/)).toBeInTheDocument();
  expect(screen.queryByText(/Kept/)).not.toBeInTheDocument();
});

test('importing a download hands the kept lap to the comparison and rechecks on focus', async () => {
  const onImported = jest.fn();
  requestJson.mockImplementation(async (path, options) => path === '/api/garage61/downloads/import'
    ? { name: JSON.parse(options.body).name, already_imported: false }
    : listing([{ name: NAME, driver_name: 'Jane Doe', lap_time_s: 78.898, imported: false }]));
  render(<Garage61WebImport onImported={onImported} />);
  fireEvent.click(await screen.findByRole('button', { name: '가져오기' }));
  await waitFor(() => expect(onImported).toHaveBeenCalledWith(NAME));
  expect(await screen.findByRole('status')).toHaveTextContent('가져왔습니다');
  const listings = () => requestJson.mock.calls.filter(([path]) => path === '/api/garage61/downloads').length;
  const before = listings();
  window.dispatchEvent(new Event('focus'));
  await waitFor(() => expect(listings()).toBeGreaterThan(before));
});

test('a picked file must be a Garage61 export; server refusals are explained', async () => {
  requestJson.mockImplementation(async path => {
    if (path === '/api/garage61/downloads') return listing([]);
    const error = new Error('Garage61 랩 CSV로 읽을 수 없습니다'); error.status = 422; throw error;
  });
  const onImported = jest.fn();
  render(<Garage61WebImport onImported={onImported} />);
  const input = screen.getByLabelText('Garage61 CSV 파일');
  fireEvent.change(input, { target: { files: [new File(['x'], 'notes.csv', { type: 'text/csv' })] } });
  expect(await screen.findByRole('alert')).toHaveTextContent('"Garage 61 - "로 시작');
  fireEvent.change(input, { target: { files: [new File(['x'], NAME, { type: 'text/csv' })] } });
  expect(await screen.findByRole('alert')).toHaveTextContent('읽을 수 없습니다');
  expect(onImported).not.toHaveBeenCalled();
  expect(requestJson.mock.calls.some(([path]) => path.startsWith('/api/garage61/import?filename='))).toBe(true);
});

test('a missing listing does not block the file picker or show an error', async () => {
  requestJson.mockRejectedValue(Object.assign(new Error('Not Found'), { status: 404 }));
  render(<Garage61WebImport onImported={jest.fn()} />);
  await waitFor(() => expect(requestJson).toHaveBeenCalled());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'CSV 파일 직접 선택…' })).toBeEnabled();
});
