import React from 'react';
import { render, screen } from '@testing-library/react';
import { VoiceWorkspace } from './VoiceWorkspace';

jest.mock('../components/SpotterPanel', () => () => <div>actual-spotter-panel</div>);
jest.mock('../components/PitWall', () => () => <div>actual-pit-wall-panel</div>);
jest.mock('./RuntimePreview', () => ({ AudioPreferences: () => <div>shared-audio-preferences</div> }));

function runtime(available) {
  return { controls: { armed: false, speak: true, mode: 'measured', volume: 80 }, speech: { available, verified: false }, change: jest.fn(),
    host: { phase: 'idle', status: { connected: false }, spotter: {}, audioOutput: {}, wallPreferences: {} } };
}

test('without speech API the voice page hides microphone and AI modes while preserving the actual radio controls', () => {
  render(<VoiceWorkspace runtime={runtime(false)} />);
  expect(screen.queryByRole('button', { name: '마이크 준비' })).not.toBeInTheDocument();
  expect(screen.queryByRole('group', { name: '음성 답변 방식' })).not.toBeInTheDocument();
  expect(screen.getByText('actual-spotter-panel')).toBeInTheDocument(); expect(screen.getByText('actual-pit-wall-panel')).toBeInTheDocument();
});

test('configured voice exposes the real microphone and push-to-talk controls without requiring a cloud call', () => {
  render(<VoiceWorkspace runtime={runtime(true)} />);
  expect(screen.getByRole('button', { name: '마이크 준비' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '녹음 시작' })).toBeDisabled();
  expect(screen.getByRole('radio', { name: 'AI 원인 분석' })).toBeInTheDocument();
});
