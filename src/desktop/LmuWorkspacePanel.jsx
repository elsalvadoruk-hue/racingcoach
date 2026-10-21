import React, { useCallback, useEffect, useState } from 'react';
import { Button } from '../components/ui/button';
import { API_BASE, requestJson } from '../analysis/analysisClient';
import { UpdateSettings } from '../components/UpdateCenter';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Field, FieldLabel } from '../components/ui/field';
import { Switch } from '../components/ui/switch';
import LmuSettingsPanel from './LmuSettingsPanel';
import LmuComingSoon from './LmuComingSoon';
import LmuHistoryPanel from './LmuHistoryPanel';
import OverlaySettings from '../components/OverlaySettings';
import { AudioPreferences } from './RuntimePreview';
import { UserManual } from '../components/HelpGuide';
import { LMU_SECTIONS } from '../components/helpLmu';
import { GameSettingsNotice } from '../lib/gameContext';

// Live coaching (voice coach tab) is the shared iRacing screen; this routes
// only the LMU analysis/settings views.
export default function LmuWorkspacePanel(props) {
  if (props.workspace === 'setup') return <LmuComingSoon onNavigate={props.onNavigate} />;
  if (props.workspace === 'history') return <LmuHistoryPanel onChooseSource={props.onChooseSource} onOpenSession={props.onOpenSession} />;
  return <LmuWorkspaceOverview {...props} />;
}

const BRIDGE_STATES = {
  READY: ['준비됨', 'secondary'],
  NATIVE_NOT_BUILT: ['빌드 필요', 'destructive'],
  ABI_MISMATCH: ['다시 빌드 필요', 'destructive'],
  SDK_UNAVAILABLE: ['LMU SDK 없음', 'outline'],
  UNSUPPORTED_PLATFORM: ['지원하지 않는 환경', 'outline'],
};

export function LmuBridgePanel() {
  const [bridge, setBridge] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const refresh = useCallback(async signal => {
    try { setBridge(await requestJson('/api/lmu/live/bridge', { signal })); setError(''); }
    catch (e) { if (!signal?.aborted) setError(e.message); }
  }, []);
  useEffect(() => { const controller = new AbortController(); refresh(controller.signal); return () => controller.abort(); }, [refresh]);
  const build = async () => {
    setBusy(true); setError('');
    try { setBridge(await requestJson('/api/lmu/live/bridge/build', { method: 'POST', timeoutMs: 180000 })); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const [label, variant] = BRIDGE_STATES[bridge?.state] || [bridge?.state || '확인 중', 'outline'];
  return <Card><CardHeader><CardTitle>LMU 실시간 연결</CardTitle>
    <CardDescription>스포터·피트월·F8 질문·오버레이는 iRacing과 같은 기능을 그대로 씁니다. LMU를 실행하고 차에 타면 자동으로 LMU에 연결됩니다.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2"><Badge variant={variant}>{label}</Badge><span>{bridge?.message}</span></div>
      {bridge?.buildable && <p>LMU 공유 메모리를 읽는 작은 프로그램(브리지)을 이 PC에 맞춰 한 번 빌드해야 합니다. Visual Studio C++ 빌드 도구가 필요하고 1분 정도 걸립니다. LMU가 업데이트되면 다시 빌드하라는 안내가 나옵니다.</p>}
      {error && <p role="alert">{error}</p>}
      <div className="flex flex-wrap gap-2">
        {bridge?.buildable && <Button disabled={busy} onClick={build}>{busy ? '빌드 중…' : '실시간 브리지 빌드'}</Button>}
        <Button variant="outline" disabled={busy} onClick={() => refresh()}>상태 새로고침</Button>
      </div>
    </CardContent></Card>;
}

function LmuWorkspaceOverview({ workspace, onChooseSource, onNavigate, analysisWorkspace = {}, runtime }) {
  const title = { settings: '설정', guide: 'LMU 사용 가이드' }[workspace];
  return <section className="wide-workspace" aria-label={`LMU ${title}`}>
    <div className="wide-heading"><div><h2>{title}</h2><p>LMU 기록 파일 분석과 실시간 음성 코치 설정입니다.</p></div>{onChooseSource && <Button variant="outline" onClick={onChooseSource}>주행 선택</Button>}</div>
    {workspace === 'guide' && <UserManual sections={LMU_SECTIONS} heading="LMU 사용법"
      intro="iRacing과 다른 점은 1장, 처음 연결은 2장, 주행 중 음성은 6~10장, 폴더와 게임별 설정은 15장을 보세요."
      emptyHint="주행 분석이 없어도 매뉴얼을 읽을 수 있습니다. 기능을 사용하려면 LMU DuckDB 주행 파일을 선택하세요."
      hasAnalysis={!!analysisWorkspace.analysis} onNavigate={onNavigate} />}
    {workspace === 'settings' && <>
      <GameSettingsNotice />
      <LmuBridgePanel />
      <LmuSettingsPanel onChooseSource={onChooseSource} />
      {runtime?.supported !== false && <AudioPreferences runtime={runtime} />}
      <Card id="overlay-settings"><CardHeader><CardTitle>오버레이</CardTitle><CardDescription>iRacing과 같은 HUD입니다. 주행 중 자동 표시와 크기·위치·불투명도를 설정합니다.</CardDescription></CardHeader><CardContent><OverlaySettings apiBase={API_BASE} /></CardContent></Card>
      <Card><CardHeader><CardTitle>화면 도움말</CardTitle></CardHeader><CardContent><Field orientation="horizontal"><FieldLabel htmlFor="desktop-hover-help">마우스 오버 설명</FieldLabel><Switch id="desktop-hover-help" checked={analysisWorkspace.hoverHelpEnabled !== false} onCheckedChange={analysisWorkspace.toggleHoverHelp} /></Field></CardContent></Card>
      <Card id="app-update"><CardHeader><CardTitle>업데이트</CardTitle></CardHeader><CardContent><UpdateSettings apiBase={API_BASE} /></CardContent></Card>
    </>}
  </section>;
}
