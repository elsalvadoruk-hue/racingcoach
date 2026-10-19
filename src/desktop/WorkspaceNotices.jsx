import React, { useEffect, useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import './workspaceNotices.css';

export default function WorkspaceNotices({ workspace: w, busy, onNavigate }) {
  const queued = useRef(null), [applying, setApplying] = useState(false);
  // Wait for the workspace to own the chosen references before invoking its existing apply action.
  useEffect(() => {
    const next = queued.current;
    if (!next || w.loading || w.activeTrial || w.referenceMode !== next.mode
      || JSON.stringify(w.selectedExternalReferences) !== JSON.stringify(next.files)) return;
    queued.current = null;
    Promise.resolve(w.applyExternalReferences()).catch(error => w.setError?.(error.message || '새 기준을 적용하지 못했습니다.')).finally(() => setApplying(false));
    w.setPendingReferences(null);
  }, [w]);
  const apply = () => {
    if (busy || w.activeTrial || !w.pendingReferences?.length) return;
    const mode = w.referenceMode === 'LOCAL' ? 'AGGREGATE' : w.referenceMode;
    const files = [...w.pendingReferences];
    queued.current = { files, mode }; setApplying(true);
    w.setSelectedExternalReferences(files); w.setReferenceMode(mode);
  };
  return <div className="desktop-workspace-notices" aria-label="주행 연결 알림">
    {w.newFileNotice && <span title={w.newFileNotice}>새 주행 발견<Button variant="outline" size="sm" disabled={busy} onClick={() => w.handleFileChange(w.newFileNotice)}>새 주행 확인</Button></span>}
    {w.pendingReferences?.length > 0 && <span>{w.activeTrial ? '새 기준 도착 · 실험 기준 고정' : `새 기준 ${w.pendingReferences.length}랩`}<Button variant="outline" size="sm" disabled={busy || applying || Boolean(w.activeTrial)} onClick={apply}>새 기준 적용</Button></span>}
    {w.activeTrial && <span>셋업 실험 진행<Button variant="outline" size="sm" onClick={() => onNavigate('setup')}>실험 열기</Button></span>}
  </div>;
}
