import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import LmuSettingsPanel from './LmuSettingsPanel';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));

const folder = 'D:\\LMU\\Telemetry';
const view = { settings: { telemetry_dirs: [] }, source: 'DEFAULT',
  telemetry_folders: [{ path: folder, exists: true }, { path: 'E:\\Unavailable', exists: false }] };

beforeEach(() => { requestJson.mockReset(); requestJson.mockResolvedValue(view); });

test('folder settings load without mutating settings and display found and missing directories', async () => {
  const onChooseSource = jest.fn();
  render(<LmuSettingsPanel onChooseSource={onChooseSource} />);
  expect(await screen.findByText(`${folder} · 폴더 확인됨`)).toBeInTheDocument();
  expect(screen.getByText('E:\\Unavailable · 폴더 없음')).toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledTimes(1);
  expect(requestJson.mock.calls[0][0]).toBe('/api/lmu/settings');
  expect(requestJson.mock.calls[0][1].method).toBeUndefined();
  fireEvent.click(screen.getByRole('button', { name: '주행 목록 확인' }));
  expect(onChooseSource).toHaveBeenCalledTimes(1);
});

test('adding folder saves only LMU folder settings and clears input after success', async () => {
  requestJson.mockImplementation(async (path, options) => options?.method === 'PUT'
    ? { ...view, source: 'SAVED', settings: { telemetry_dirs: [folder] } } : view);
  render(<LmuSettingsPanel />);
  await screen.findByText(`${folder} · 폴더 확인됨`);
  fireEvent.change(screen.getByLabelText('추가할 LMU 기록 폴더'), { target: { value: ` ${folder} ` } });
  fireEvent.click(screen.getByRole('button', { name: '폴더 추가' }));
  expect(await screen.findByText(/LMU 기록 폴더를 저장했습니다/)).toBeInTheDocument();
  const [path, options] = requestJson.mock.calls.find(([, args]) => args.method === 'PUT');
  expect(path).toBe('/api/lmu/settings'); expect(JSON.parse(options.body)).toEqual({ telemetry_dirs: [folder] });
  expect(screen.getByLabelText('추가할 LMU 기록 폴더')).toHaveValue('');
  expect(screen.getByRole('button', { name: `${folder} 빼기` })).toBeInTheDocument();
});

test('removing saved folder preserves other paths and environment precedence explanation', async () => {
  const second = 'E:\\Telemetry';
  requestJson.mockResolvedValue({ ...view, source: 'ENVIRONMENT', settings: { telemetry_dirs: [folder, second] } });
  render(<LmuSettingsPanel />);
  expect(await screen.findByText(/환경 변수의 폴더가 우선합니다/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: `${folder} 빼기` }));
  await waitFor(() => expect(requestJson.mock.calls.some(([, options]) => options.method === 'PUT')).toBe(true));
  expect(JSON.parse(requestJson.mock.calls.find(([, options]) => options.method === 'PUT')[1].body)).toEqual({ telemetry_dirs: [second] });
});

test('save failure keeps user input and existing folder display', async () => {
  requestJson.mockImplementation(async (path, options) => {
    if (options.method === 'PUT') throw new Error('전체 경로를 입력하세요.');
    return view;
  });
  render(<LmuSettingsPanel />);
  await screen.findByText(`${folder} · 폴더 확인됨`);
  fireEvent.change(screen.getByLabelText('추가할 LMU 기록 폴더'), { target: { value: 'relative' } });
  fireEvent.click(screen.getByRole('button', { name: '폴더 추가' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('전체 경로를 입력하세요.');
  expect(screen.getByLabelText('추가할 LMU 기록 폴더')).toHaveValue('relative');
  expect(screen.queryByText(/저장했습니다/)).not.toBeInTheDocument();
});

test('game change unmount cancels save and never updates the next game screen', async () => {
  let resolveSave;
  requestJson.mockImplementation((path, options) => options.method === 'PUT'
    ? new Promise(resolve => { resolveSave = resolve; }) : Promise.resolve(view));
  const { unmount } = render(<LmuSettingsPanel />);
  await screen.findByText(`${folder} · 폴더 확인됨`);
  fireEvent.change(screen.getByLabelText('추가할 LMU 기록 폴더'), { target: { value: folder } });
  fireEvent.click(screen.getByRole('button', { name: '폴더 추가' }));
  const signal = requestJson.mock.calls.find(([, options]) => options.method === 'PUT')[1].signal;
  unmount(); expect(signal.aborted).toBe(true);
  await act(async () => { resolveSave(view); });
  expect(screen.queryByText(/저장했습니다/)).not.toBeInTheDocument();
});

test('pending initial load disables mutations and failed load remains readable', async () => {
  requestJson.mockRejectedValue(new Error('설정을 읽을 수 없습니다.'));
  render(<LmuSettingsPanel />);
  expect(screen.getByRole('button', { name: '폴더 추가' })).toBeDisabled();
  expect(await screen.findByRole('alert')).toHaveTextContent('설정을 읽을 수 없습니다.');
  expect(screen.getByLabelText('추가할 LMU 기록 폴더')).toBeDisabled();
  expect(requestJson).toHaveBeenCalledTimes(1);
});
