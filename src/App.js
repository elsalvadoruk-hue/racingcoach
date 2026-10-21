import React, { useState } from 'react';
import { useAnalysisWorkspace } from './analysis/useAnalysisWorkspace';
import { API_BASE } from './analysis/analysisClient';
import { SessionModeChangeDialog } from './analysis/WorkspaceUi';
import CornerLab from './desktop/CornerLab';
import { DesktopRuntimeProvider } from './desktop/RuntimePreview';
import './desktop/lab.css';
import { HelpLayer } from './components/HelpGuide';
import UpdateCenter from './components/UpdateCenter';
import { selectGame } from './desktop/sessionData';
import { useLmuWorkspace } from './analysis/useLmuWorkspace';
import { GameContext } from './lib/gameContext';

export default function App() {
  const [liveGame, setLiveGame] = useState(null);
  const [game, setGame] = useState(() => {
    let saved; try { saved = localStorage.getItem('coach.selected-game.v1'); } catch { /* Default game. */ }
    const initial = saved === 'lmu' ? 'lmu' : 'iracing';
    selectGame(initial); return initial;
  });
  const changeGame = next => {
    if (!next || next === game) return;
    selectGame(next);
    try { localStorage.setItem('coach.selected-game.v1', next); } catch { /* Selection remains usable. */ }
    setGame(next);
  };
  return <GameContext.Provider value={{ selectedGame: game, liveGame, activeGame: liveGame ?? game, setLiveGame }}>
    {game === 'lmu' ? <LmuApp onGameChange={changeGame} /> : <IracingApp onGameChange={changeGame} />}
  </GameContext.Provider>;
}

function IracingApp({ onGameChange }) {
  const workspace = useAnalysisWorkspace();
  return <DesktopRuntimeProvider apiBase={API_BASE}>
    <HelpLayer hoverEnabled={workspace.hoverHelpEnabled} />
    <UpdateCenter apiBase={API_BASE} />
    <SessionModeChangeDialog prompt={workspace.modeChangePrompt} loading={workspace.loading}
      onCancel={() => workspace.setModeChangePrompt(null)} onConfirm={workspace.confirmSessionModeChange} />
    <CornerLab analysisWorkspace={workspace} game="iracing" onGameChange={onGameChange} />
  </DesktopRuntimeProvider>;
}

// Live coaching (spotter, pit wall, F8 questions, overlay) is one service for
// both games: the backend reads whichever sim is running. Only the analysis
// workspace differs.
function LmuApp({ onGameChange }) {
  const workspace = useLmuWorkspace();
  return <DesktopRuntimeProvider apiBase={API_BASE}>
    <HelpLayer hoverEnabled={workspace.hoverHelpEnabled} />
    <UpdateCenter apiBase={API_BASE} />
    <CornerLab analysisWorkspace={workspace} game="lmu" onGameChange={onGameChange} />
  </DesktopRuntimeProvider>;
}
