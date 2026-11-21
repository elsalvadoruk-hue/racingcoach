import React, { useEffect, useState } from 'react';
import { Button } from './ui/button';
import { loadManual, loadedManual } from './manualLoading';

export default function ManualWorkspace({ game, ...props }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(() => Boolean(loadedManual(game)));

  useEffect(() => {
    let active = true;
    loadManual(game).then(() => {
      if (active) setReady(true);
    }, () => {
      if (active) setFailed(true);
    });
    return () => { active = false; };
  }, [game, attempt]);

  const Manual = ready && loadedManual(game);
  if (Manual) return <Manual {...props} />;
  if (failed) return <div role="alert" className="flex items-center gap-3">
    <span>사용 가이드를 불러오지 못했습니다.</span>
    <Button variant="outline" onClick={() => { setFailed(false); setAttempt(value => value + 1); }}>다시 시도</Button>
  </div>;
  return <p role="status" className="text-sm text-muted-foreground">사용 가이드를 불러오는 중입니다.</p>;
}
