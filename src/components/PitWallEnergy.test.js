import React from 'react';
import { render, screen } from '@testing-library/react';
import PitWall, { readPitPreferences } from './PitWall';

const props = { enabled: false, onToggle: () => {}, onChange: () => {}, onTestVoice: () => {}, engineers: {}, preferences: readPitPreferences() };

test('LMU shows measured virtual energy, and "측정 중" until laps are measured', () => {
  const { rerender } = render(<PitWall {...props} provider="LMU" view={{ energy_pct: 62.44, energy_pct_per_lap: 3.18, energy_laps: 19.6, energy_target_pct_per_lap: 2.9, energy_limits_stint: true }} />);
  expect(screen.getByText('62.4%')).toBeInTheDocument();
  expect(screen.getByText('3.2%')).toBeInTheDocument();
  expect(screen.getByText('19.6랩')).toBeInTheDocument();
  expect(screen.getByText(/완주 목표 랩당 2.9%/)).toBeInTheDocument();
  expect(screen.getByText('이번 스틴트는 연료보다 에너지가 먼저 떨어집니다.')).toBeInTheDocument();
  rerender(<PitWall {...props} provider="LMU" view={{ energy_pct: 80 }} />);
  expect(screen.getAllByText('측정 중')).toHaveLength(2);
});

test('iRacing has no energy fields at all', () => {
  render(<PitWall {...props} view={{ energy_pct: 62 }} />);
  expect(screen.queryByText('버추얼 에너지')).not.toBeInTheDocument();
});
