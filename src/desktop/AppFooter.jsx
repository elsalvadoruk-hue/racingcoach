import React from 'react';
import { Keyboard, Maximize2 } from 'lucide-react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import WorkspaceNotices from './WorkspaceNotices';

export default function AppFooter({ version, busy, message, workspace, onNavigate, onFullscreen }) {
  return <footer className="lab-footer">
    <div className="footer-left">
      <span className="footer-local-status"><i />로컬 앱 · {busy ? '불러오는 중' : '측정값 기반'}</span>
      <span role="status" className="footer-notice">{message}</span>
      <WorkspaceNotices workspace={workspace} busy={busy} onNavigate={onNavigate} />
    </div>
    <div className="footer-project" aria-label="앱 버전과 소스 코드">
      <Badge variant="outline" className="app-version" title="현재 앱 버전">{version}</Badge>
      <Button asChild variant="ghost" size="icon" className="footer-project-link">
        <a href="https://github.com/elsalvadoruk-hue/racingcoach" target="_blank" rel="noopener noreferrer" aria-label="GitHub 프로젝트 열기 (새 탭)" title="GitHub 프로젝트 열기">
          <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.65 7.65 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" /></svg>
        </a>
      </Button>
    </div>
    <div className="footer-shortcuts">
      <span><Keyboard aria-hidden="true" /><kbd>←</kbd><kbd>→</kbd> 코너 이동</span>
      <span><kbd>F</kbd> 그래프 집중</span>
      <Button variant="ghost" size="sm" className="footer-fullscreen" aria-label="전체화면 · F11" onClick={onFullscreen}><kbd>F11</kbd>전체화면<Maximize2 data-icon="inline-end" aria-hidden="true" /></Button>
    </div>
  </footer>;
}
