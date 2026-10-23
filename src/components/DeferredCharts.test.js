import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { DeferredDamperHistogram, DeferredLapTimeUse } from './DeferredCharts';

let mockDamperLoads = 0;
jest.mock('./DamperHistogram', () => {
  if (mockDamperLoads++ === 0) throw new Error('Synthetic chart download failed');
  return { __esModule: true, default: ({ current }) => <p>댐퍼 표본 {current.samples}개</p> };
});
jest.mock('./LapTimeUse', () => ({ __esModule: true, default: ({ laps, provider }) => <p>{provider} {laps.length}랩 차트</p> }));

test('a failed chart download preserves the workspace and can retry with the original data', async () => {
  const error = jest.spyOn(console, 'error').mockImplementation(() => {});
  try {
    render(<><h1>주행 분석 유지</h1><DeferredDamperHistogram current={{ samples: 120 }} /></>);
    expect(await screen.findByRole('alert')).toHaveTextContent('그래프를 불러오지 못했습니다.');
    expect(screen.getByRole('heading', { name: '주행 분석 유지' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '그래프 다시 불러오기' }));
    expect(await screen.findByText('댐퍼 표본 120개')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  } finally { error.mockRestore(); }
});

test('the deferred lap chart receives the selected game and laps', async () => {
  render(<DeferredLapTimeUse provider="lmu" laps={[{ lap_number: 4 }, { lap_number: 5 }]} />);
  expect(await screen.findByText('lmu 2랩 차트')).toBeInTheDocument();
});
