import React from 'react';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { useGarage61Preference } from './useGarage61Preference';
import { ExternalReferencePanel } from './ReferencePanel';
import { requestJson } from './analysisClient';

jest.mock('./analysisClient', () => ({ requestJson: jest.fn() }));

beforeEach(() => requestJson.mockReset());

test('loads saved mode and commits server-confirmed changes before refreshing references', async () => {
  requestJson.mockResolvedValueOnce({ selection_mode: 'FASTEST' }).mockResolvedValueOnce({ selection_mode: 'MATCHED' });
  const changed = jest.fn();
  const { result } = renderHook(() => useGarage61Preference(changed));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.mode).toBe('FASTEST');
  await act(() => result.current.change('MATCHED'));
  expect(JSON.parse(requestJson.mock.calls[1][1].body)).toEqual({ selection_mode: 'MATCHED' });
  expect(result.current.mode).toBe('MATCHED');
  expect(changed).toHaveBeenCalledTimes(1);
  await act(() => result.current.change(''));
  expect(requestJson).toHaveBeenCalledTimes(2);
});

test('save failure retains previous mode and does not refresh or apply selected references', async () => {
  requestJson.mockResolvedValueOnce({ selection_mode: 'MATCHED' }).mockRejectedValueOnce(new Error('수집 중'));
  const changed = jest.fn();
  const { result } = renderHook(() => useGarage61Preference(changed));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(() => result.current.change('FASTEST'));
  expect(result.current.mode).toBe('MATCHED');
  expect(result.current.error).toContain('수집 중');
  expect(changed).not.toHaveBeenCalled();
});

test('failed load can be retried and rapid duplicate saves send one request', async () => {
  requestJson.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ selection_mode: 'MATCHED' });
  const { result } = renderHook(() => useGarage61Preference());
  await waitFor(() => expect(result.current.error).toContain('offline'));
  await act(() => result.current.reload());
  let finish;
  requestJson.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  let pending;
  act(() => { pending = result.current.change('FASTEST'); result.current.change('FASTEST'); });
  expect(requestJson).toHaveBeenCalledTimes(3);
  await act(async () => { finish({ selection_mode: 'FASTEST' }); await pending; });
  expect(result.current.mode).toBe('FASTEST');
});

test('settings show both choices, fastest explanation, and lock changes during collection', () => {
  const preference = { mode: 'MATCHED', loading: false, saving: false, change: jest.fn() };
  const { rerender } = render(<ExternalReferencePanel preference={preference} />);
  expect(screen.getByRole('radio', { name: '조건에 맞는 데이터' })).toHaveAttribute('aria-checked', 'true');
  fireEvent.click(screen.getByRole('radio', { name: '가장 빠른 데이터' }));
  expect(preference.change).toHaveBeenCalledWith('FASTEST');
  rerender(<ExternalReferencePanel preference={{ ...preference, mode: 'FASTEST' }} collector={{ job: { busy: true } }} />);
  expect(screen.getByRole('radio', { name: '가장 빠른 데이터' })).toBeDisabled();
  expect(screen.getByText(/날씨·연료·셋업 조건은 다를 수 있습니다/)).toBeInTheDocument();
  expect(screen.getByText(/사용 가능한 기록을 랩타임이 빠른 순서로 추천합니다/)).toBeInTheDocument();
});
