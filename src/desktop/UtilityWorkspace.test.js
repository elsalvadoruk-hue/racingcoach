import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import UtilityWorkspace from './UtilityWorkspace';

let mockReferenceProps, mockVoiceReviewProps;
jest.mock('../analysis/ReferencePanel', () => ({ ExternalReferencePanel: props => { mockReferenceProps = props; return <div>real-reference-workflow</div>; } }));
jest.mock('../components/AppSettings', () => ({ __esModule: true, default: props => <div data-testid="settings-base">{props.apiBase}</div> }));
jest.mock('../components/OverlaySettings', () => ({ __esModule: true, default: props => <div data-testid="overlay-base">{props.apiBase}</div> }));
jest.mock('../components/SessionLogExport', () => ({ __esModule: true, default: () => <div>real-session-log</div> }));
jest.mock('../components/RadioAudioPrepare', () => ({ __esModule: true, default: () => <div>real-radio-prepare</div> }));
jest.mock('../components/RadioDiagnostics', () => ({ __esModule: true, default: () => <div>real-diagnostics</div> }));
jest.mock('../components/LiveCoach', () => ({ NoteText: ({ note }) => <p>{note.text}</p>, CornerCoach: () => <div>real-corner-coach</div>,
  VoiceReview: props => { mockVoiceReviewProps = props; return <div>real-voice-review</div>; } }));
jest.mock('./RuntimePreview', () => ({ AudioPreferences: () => <div>real-audio-settings</div>, VoicePreview: () => <div>real-voice-workspace</div> }));
jest.mock('../analysis/HistoryPanels', () => ({ HistoryPanel: () => <div>real-analysis-history</div>, LongTermMemoryPanel: () => <div>real-memory-history</div> }));
jest.mock('../analysis/CoachingPanels', () => ({ PersonalizationPanel: () => <div>real-personalization</div> }));
jest.mock('../components/TrialWorkflow', () => ({ TrialHistory: () => <div>real-trial-history</div> }));

const runtime = () => ({ apiBase: 'http://localhost:24158', host: { notes: [{ id: 1, text: 'saved voice note' }], refreshNotes: jest.fn(), status: {} } });

test('production settings preserve actual native settings and all Garage61 collection actions', () => {
  const w = { refreshExternalReferences: jest.fn(), startGarage61Collection: jest.fn(), cancelGarage61Collection: jest.fn(),
    applyExternalReferences: jest.fn(), setSelectedExternalReferences: jest.fn(), toggleExternalReference: jest.fn(),
    externalReferenceStatus: { recommended_selection: ['one.csv', 'two.csv'], display_limit: 1 }, selectedExternalReferences: ['one.csv'] };
  render(<UtilityWorkspace workspace="settings" runtime={runtime()} analysisWorkspace={w} />);
  expect(screen.getByTestId('settings-base')).toHaveTextContent('http://localhost:24158');
  expect(screen.getByTestId('overlay-base')).toHaveTextContent('http://localhost:24158');
  expect(screen.queryByRole('group', { name: /읽기 전용/ })).not.toBeInTheDocument();
  mockReferenceProps.onCollect(); mockReferenceProps.onForceCollect(); mockReferenceProps.onCancelCollect();
  mockReferenceProps.onAutoSelect(); mockReferenceProps.onApply();
  expect(w.startGarage61Collection.mock.calls).toEqual([[], [null, true]]);
  expect(w.cancelGarage61Collection).toHaveBeenCalledTimes(1); expect(w.applyExternalReferences).toHaveBeenCalledTimes(1);
  expect(w.setSelectedExternalReferences).toHaveBeenCalledWith(['one.csv']);
});

test('history keeps personalization, actual saved notes, review editing and session diagnostics together', () => {
  const w = { memoryStatus: { enabled: true }, analysis: { file: { name: 'local.ibt' }, memory: { saved: true }, laps: [], performance: { corners: [] } }, runAnalysis: jest.fn() };
  const onOpenCorner = jest.fn();
  render(<UtilityWorkspace workspace="history" runtime={runtime()} analysisWorkspace={w} onOpenCorner={onOpenCorner} />);
  for (const text of ['real-personalization', 'real-memory-history', 'real-trial-history', 'real-session-log', 'real-diagnostics', 'saved voice note', 'real-voice-review']) expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.getByText('real-voice-review')).not.toBeVisible();
  fireEvent.click(screen.getByText('대화로 남긴 코너'));
  expect(screen.getByText('real-voice-review')).toBeVisible();
  expect(screen.getByText('saved voice note')).not.toBeVisible();
  fireEvent.click(screen.getByText('음성 답변·코너 메모'));
  expect(screen.getByText('saved voice note')).toBeVisible();
  expect(screen.getByRole('button', { name: '대화 기록 새로고침' })).toBeEnabled();
  mockVoiceReviewProps.onRefresh(); mockVoiceReviewProps.onOpenCorner({ corner_id: 'C3' });
  expect(w.runAnalysis).toHaveBeenCalledWith({ includeAI: false }); expect(onOpenCorner).toHaveBeenCalledWith({ corner_id: 'C3' });
});
