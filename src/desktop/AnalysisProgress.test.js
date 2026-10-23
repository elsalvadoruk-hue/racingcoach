import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { AnalysisProgressBar, useAnalysisProgress } from './AnalysisProgress';
import { requestJson } from '../analysis/analysisClient';

jest.mock('../analysis/analysisClient', () => ({ requestJson: jest.fn() }));

function Harness({ onReady }) {
  const progress = useAnalysisProgress();
  onReady(progress);
  return <AnalysisProgressBar view={progress.view} />;
}

beforeEach(() => { jest.useFakeTimers(); requestJson.mockReset(); });
afterEach(() => jest.useRealTimers());

test('the bar appears for a slow analysis, shows the stage and time left, and goes away at the end', async () => {
  let progress;
  requestJson.mockResolvedValue({ percent: 42, stage: 'handling', label: '차 거동 비교 중', remaining_s: 23 });
  render(<Harness onReady={value => { progress = value; }} />);
  let id;
  act(() => { id = progress.begin(); });
  expect(screen.queryByTestId('analysis-progress')).not.toBeInTheDocument();
  await act(async () => { jest.advanceTimersByTime(600); });
  await act(async () => { jest.advanceTimersByTime(600); });
  expect(requestJson).toHaveBeenCalledWith(`/api/workspace/progress/${id}`, expect.objectContaining({ signal: expect.anything(), timeoutMs: 5000 }));
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42');
  expect(screen.getByText('차 거동 비교 중')).toBeInTheDocument();
  expect(screen.getByText('예상 42% · 약 25초 남음')).toBeInTheDocument();
  act(() => progress.end());
  expect(screen.queryByTestId('analysis-progress')).not.toBeInTheDocument();
  const calls = requestJson.mock.calls.length;
  await act(async () => { jest.advanceTimersByTime(3000); });
  expect(requestJson.mock.calls.length).toBe(calls);
});

test('a failed poll never breaks the picker', async () => {
  let progress;
  requestJson.mockRejectedValue(new Error('offline'));
  render(<Harness onReady={value => { progress = value; }} />);
  act(() => { progress.begin(); });
  await act(async () => { jest.advanceTimersByTime(2000); });
  expect(screen.queryByTestId('analysis-progress')).not.toBeInTheDocument();
  act(() => progress.end());
});

test('finishing an older request does not stop the current request', async () => {
  let progress;
  requestJson.mockResolvedValue({ percent: 60, label: '코너 측정 중' });
  render(<Harness onReady={value => { progress = value; }} />);
  let previous, current;
  act(() => { previous = progress.begin(); current = progress.begin(); });
  act(() => progress.end(previous));
  await act(async () => { jest.advanceTimersByTime(500); });
  await act(async () => { jest.advanceTimersByTime(500); });
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '60');
  expect(requestJson).toHaveBeenLastCalledWith(`/api/workspace/progress/${current}`, expect.anything());
  act(() => progress.end(current));
});

test('unmount aborts an in-flight poll and a late reply cannot schedule more polls', async () => {
  let progress, reply;
  requestJson.mockImplementation(() => new Promise(resolve => { reply = resolve; }));
  const { unmount } = render(<Harness onReady={value => { progress = value; }} />);
  act(() => progress.begin());
  await act(async () => { jest.advanceTimersByTime(500); });
  const signal = requestJson.mock.calls[0][1].signal;
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { reply({ percent: 90 }); });
  await act(async () => { jest.advanceTimersByTime(3000); });
  expect(requestJson).toHaveBeenCalledTimes(1);
});

test('a job waiting to start shows an indeterminate bar without an invented percent', async () => {
  let progress;
  requestJson.mockResolvedValue({ percent: null, stage: 'unknown' });
  render(<Harness onReady={value => { progress = value; }} />);
  act(() => progress.begin());
  await act(async () => { jest.advanceTimersByTime(500); });
  await act(async () => { jest.advanceTimersByTime(500); });
  expect(screen.getByText('분석 준비 중')).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  act(() => progress.end());
});
