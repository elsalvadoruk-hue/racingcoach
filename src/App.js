import React from 'react';
import { useAnalysisWorkspace } from './analysis/useAnalysisWorkspace';
import { API_BASE } from './analysis/analysisClient';
import { SessionModeChangeDialog } from './analysis/WorkspaceUi';
import CornerLab from './desktop/CornerLab';
import { DesktopRuntimeProvider } from './desktop/RuntimePreview';
import './desktop/lab.css';
import { HelpLayer } from './components/HelpGuide';

export default function App() {
  const workspace = useAnalysisWorkspace();
  return <DesktopRuntimeProvider apiBase={API_BASE}>
    <HelpLayer hoverEnabled={workspace.hoverHelpEnabled} />
    <SessionModeChangeDialog prompt={workspace.modeChangePrompt} loading={workspace.loading}
      onCancel={() => workspace.setModeChangePrompt(null)} onConfirm={workspace.confirmSessionModeChange} />
    <CornerLab analysisWorkspace={workspace} />
  </DesktopRuntimeProvider>;
}
