import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import WorkspaceNotices from './WorkspaceNotices';

test('new telemetry and active trial notices expose the original file action and experiment destination', () => {
  const handleFileChange = jest.fn(), navigate = jest.fn();
  render(<WorkspaceNotices workspace={{ newFileNotice: 'new.ibt', handleFileChange, activeTrial: { id: 'trial' }, pendingReferences: ['one.csv'] }} onNavigate={navigate} />);
  fireEvent.click(screen.getByRole('button', { name: '새 주행 확인' }));
  fireEvent.click(screen.getByRole('button', { name: '실험 열기' }));
  expect(handleFileChange).toHaveBeenCalledWith('new.ibt'); expect(navigate).toHaveBeenCalledWith('setup');
  expect(screen.getByRole('button', { name: '새 기준 적용' })).toBeDisabled();
});

test('reference apply waits for state to contain the new selection and aggregate mode before using the existing action', async () => {
  const apply = jest.fn(async () => {});
  function Owner() {
    const [references, setSelectedExternalReferences] = useState(['old.csv']);
    const [referenceMode, setReferenceMode] = useState('LOCAL');
    const [pendingReferences, setPendingReferences] = useState(['new.csv']);
    return <WorkspaceNotices workspace={{ pendingReferences, referenceMode, selectedExternalReferences: references, setSelectedExternalReferences,
      setReferenceMode, setPendingReferences, applyExternalReferences: () => apply(references, referenceMode) }} />;
  }
  render(<Owner />);
  fireEvent.click(screen.getByRole('button', { name: '새 기준 적용' }));
  await waitFor(() => expect(apply).toHaveBeenCalledWith(['new.csv'], 'AGGREGATE'));
  expect(screen.queryByRole('button', { name: '새 기준 적용' })).not.toBeInTheDocument();
});
