import React from 'react';
import { render, screen } from '@testing-library/react';
import PitWall, { readPitPreferences } from './PitWall';
import { GameContext } from '../lib/gameContext';

const props = { enabled: false, onToggle: () => {}, onChange: () => {}, onTestVoice: () => {}, engineers: {}, preferences: readPitPreferences() };

test('LMU replaces calculation cards with individual energy choices', () => {
  render(<GameContext.Provider value={{ selectedGame: 'lmu' }}><PitWall {...props} view={{ energy_pct: 62.44 }} /></GameContext.Provider>);
  expect(screen.queryByText('62.4%')).not.toBeInTheDocument();
  expect(screen.getByLabelText('에너지 긴급·스틴트 한도')).toBeInTheDocument();
  expect(screen.getByLabelText('랩별 에너지 사용 평가')).toBeInTheDocument();
  expect(screen.queryByLabelText('앞·뒤 ARB 조절')).not.toBeInTheDocument();
});

test('iRacing has no energy fields at all', () => {
  render(<PitWall {...props} view={{ energy_pct: 62 }} />);
  expect(screen.queryByText('LMU 버추얼 에너지')).not.toBeInTheDocument();
});
