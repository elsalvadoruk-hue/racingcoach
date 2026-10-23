import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import StartupLoading from './StartupLoading';

test('preparation stays indeterminate until the server supplies an estimate', () => {
  const { rerender } = render(<StartupLoading busy game="iracing" />);
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  expect(screen.getByLabelText('시작 준비')).toHaveAttribute('aria-busy', 'true');
  expect(screen.queryByText(/100%/)).not.toBeInTheDocument();
  rerender(<StartupLoading busy game="lmu" progress={{ label: '차 거동 비교 중', percent: 42, remaining_s: 23 }} />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42');
  expect(screen.getByText('예상 42% · 약 25초 남음')).toBeInTheDocument();
  expect(screen.getByText('차 거동 비교 중')).toBeInTheDocument();
});

test('failure replaces progress with the real error and offers working recovery actions', () => {
  const onRetry = jest.fn(), onChoose = jest.fn(), onSettings = jest.fn();
  render(<StartupLoading busy={false} error="주행 파일을 읽을 수 없습니다" onRetry={onRetry} onChoose={onChoose} onSettings={onSettings}
    progress={{ percent: 99, label: '차 거동 비교 중' }} />);
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('주행 파일을 읽을 수 없습니다');
  fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));
  fireEvent.click(screen.getByRole('button', { name: '다른 주행 선택' }));
  fireEvent.click(screen.getByRole('button', { name: '연결 상태 확인' }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(onChoose).toHaveBeenCalledTimes(1);
  expect(onSettings).toHaveBeenCalledTimes(1);
});
