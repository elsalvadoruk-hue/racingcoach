import React, { createContext, useCallback, useContext, useRef, useState } from 'react';

export const GameContext = createContext({ selectedGame: 'iracing', liveGame: null, activeGame: 'iracing' });
export const useGame = () => useContext(GameContext);
// Keep editing separate from the values used by the connected simulator.
// The in-memory copies also work when browser storage is unavailable.
export function useGameSettings(read, write) {
  const { selectedGame, activeGame } = useGame();
  const values = useRef(null);
  if (!values.current) values.current = { iracing: read('iracing'), lmu: read('lmu') };
  const [, refresh] = useState(0);
  const update = useCallback(value => {
    const next = typeof value === 'function' ? value(values.current[selectedGame]) : value;
    values.current = { ...values.current, [selectedGame]: next };
    write(selectedGame, next);
    refresh(n => n + 1);
    return next;
  }, [selectedGame, write]);
  return [values.current[selectedGame], update, values.current[activeGame]];
}
export function GameSettingsNotice() {
  const { selectedGame, liveGame } = useGame();
  if (!liveGame || liveGame === selectedGame) return null;
  const label = game => game === 'lmu' ? 'LMU' : 'iRacing';
  return <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-foreground">지금 주행 중인 게임은 {label(liveGame)}입니다. 여기서 바꾸는 {label(selectedGame)} 설정은 {label(selectedGame)}에 탔을 때 적용됩니다.</p>;
}
