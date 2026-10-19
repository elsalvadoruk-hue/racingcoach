import { render, screen } from '@testing-library/react';
import PitWall from './PitWall';

const preferences = { level: 'important', reserve_laps: 0.5, pit_loss_s: null, fuel_save_mode: 'finish', pit_rule: 'UNKNOWN', hold_enabled: true, hold_lead_s: 1 };
const props = { enabled: true, onToggle: () => {}, onChange: () => {}, engineers: {}, onTestVoice: () => {}, busy: false, speaking: false };

test('an unset pit rule follows the series record and says so', () => {
  render(<PitWall {...props} preferences={preferences} view={{ pit_rule: 'SEQUENTIAL', pit_rule_source: 'RECORD' }} />);
  expect(screen.getByRole('option', { name: '자동 · 지난 경기 기록: 순차 교체' })).toBeInTheDocument();
  expect(screen.getByTestId('pit-rule-note')).toHaveTextContent('지난 경기 피트 기록대로');
});

test('without a record the automatic choice says no tyre advice will be given', () => {
  render(<PitWall {...props} preferences={preferences} view={{}} />);
  expect(screen.getByRole('option', { name: '자동 · 지난 경기 기록 따르기' })).toBeInTheDocument();
  expect(screen.getByTestId('pit-rule-note')).toHaveTextContent('없으면 타이어 안내를 하지 않습니다');
});

test('a rule the driver chose shows no note', () => {
  render(<PitWall {...props} preferences={{ ...preferences, pit_rule: 'CONCURRENT' }} view={{ pit_rule: 'CONCURRENT', pit_rule_source: 'DRIVER' }} />);
  expect(screen.queryByTestId('pit-rule-note')).toBeNull();
});
