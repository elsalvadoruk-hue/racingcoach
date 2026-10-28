import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { UserManual } from '../components/HelpGuide';
import AppSettings from '../components/AppSettings';
import SessionLogExport from '../components/SessionLogExport';
import RadioAudioPrepare from '../components/RadioAudioPrepare';
import { UpdateSettings } from '../components/UpdateCenter';
import RadioDiagnostics from '../components/RadioDiagnostics';
import { NoteText, VoiceReview, CornerCoach } from '../components/LiveCoach';
import { HistoryPanel, LongTermMemoryPanel } from '../analysis/HistoryPanels';
import { PersonalizationPanel } from '../analysis/CoachingPanels';
import { ExternalReferencePanel } from '../analysis/ReferencePanel';
import { TrialHistory } from '../components/TrialWorkflow';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '../components/ui/empty';
import { AudioPreferences, VoicePreview } from './RuntimePreview';
import { Field, FieldLabel } from '../components/ui/field';
import { Switch } from '../components/ui/switch';
import { GameSettingsNotice } from '../lib/gameContext';

export const workspaceLabels = { driver: '드라이버 리포트', summary: '주행 요약', corners: '코너 비교', laps: '랩 분석', expert: '전문가 보기', setup: '셋업 실험', live: '음성 코치', overlay: '오버레이', history: '기록', settings: '설정', guide: '사용 가이드' };

function ExistingPanel({ title, description, children, id, collapsible = false }) {
  if (collapsible) return <Card asChild id={id}><details>
    <summary className="cursor-pointer p-6"><CardTitle asChild><span>{title}</span></CardTitle></summary>
    <CardContent>{description && <CardDescription>{description}</CardDescription>}{children}</CardContent>
  </details></Card>;
  return <Card id={id}><CardHeader><CardTitle>{title}</CardTitle>{description && <CardDescription>{description}</CardDescription>}</CardHeader><CardContent>{children}</CardContent></Card>;
}

function VoiceNotes({ runtime }) {
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const refresh = async () => { setBusy(true); try { await runtime.host.refreshNotes(); setError(''); } catch { setError('음성 기록을 불러오지 못했습니다.'); } finally { setBusy(false); } };
  return <><div className="mb-3 flex justify-end"><Button variant="outline" size="sm" disabled={busy} onClick={refresh}>대화 기록 새로고침</Button></div>
    {error && <p role="alert">{error}</p>}{runtime.host.notes.length ? runtime.host.notes.map(note => <article key={note.id} className="coach-live-note"><NoteText note={note} /></article>)
      : <Empty><EmptyHeader><EmptyTitle>아직 대화가 없습니다</EmptyTitle><EmptyDescription>마이크 질문과 코너 메모, 자동 보고를 이곳에서 확인할 수 있습니다.</EmptyDescription></EmptyHeader></Empty>}</>;
}

function ReferenceSettings({ workspace: w }) {
  if (!w.refreshExternalReferences) return null;
  return <ExternalReferencePanel referenceStatus={w.externalReferenceStatus} files={w.externalReferences} selected={w.selectedExternalReferences}
    analysis={w.externalReferenceAnalysis} loading={w.loading || Boolean(w.activeTrial)} collector={w.garage61Collector} collectorLoading={w.garage61CollectorLoading}
    apiStatus={w.garage61ApiStatus} onToggle={w.toggleExternalReference} onRefresh={() => w.refreshExternalReferences()}
    onPrimarySelect={name => !w.activeTrial && w.setSelectedExternalReferences(current => [name, ...current.filter(file => file !== name)])}
    onApply={() => !w.activeTrial && w.applyExternalReferences()} onAutoSelect={() => !w.activeTrial && w.setSelectedExternalReferences((w.externalReferenceStatus?.recommended_selection || []).slice(0, w.externalReferenceStatus?.display_limit || w.externalReferenceStatus?.max_selection || 9))}
    onClear={() => !w.activeTrial && w.setSelectedExternalReferences([])} onCollect={() => w.startGarage61Collection()}
    onForceCollect={() => w.startGarage61Collection(null, true)} onCancelCollect={w.cancelGarage61Collection} />;
}

export default function UtilityWorkspace({ workspace, runtime, onNavigate, analysisWorkspace = {}, onOpenCorner }) {
  const w = analysisWorkspace, analysis = w.analysis, apiBase = runtime.apiBase;
  // Keep legacy section ids intact; the desktop navigation table also preserves their anchors.
  const navigate = onNavigate;
  if (workspace === 'live') return <VoicePreview runtime={runtime} onSettings={() => onNavigate('settings')} />;
  return <section className="utility-workspace" aria-label={workspaceLabels[workspace]}>
    <div className="utility-heading"><div><div className="eyebrow">WORKSPACE / {workspace.toUpperCase()}</div><h1>{workspaceLabels[workspace]}</h1><p>기록과 세부 설정은 이 영역 안에서 스크롤할 수 있습니다.</p></div></div>
    {workspace === 'guide' ? <UserManual hasAnalysis={!!analysis} onNavigate={navigate} /> : workspace === 'history' ? <>
      <ExistingPanel title="셋업 실험 이력"><TrialHistory history={w.trialHistory || []} /></ExistingPanel>
      {analysis?.memory && <ExistingPanel id="personalization" title="개인 주행 특성과 학습"><PersonalizationPanel memory={analysis.memory} /></ExistingPanel>}
      {w.memoryStatus ? <ExistingPanel id="memory-history" title="개인 주행 기록"><LongTermMemoryPanel memoryStatus={w.memoryStatus} sessions={w.memorySessions || []} currentMemory={analysis?.memory} /></ExistingPanel>
        : <ExistingPanel title="주행 분석 이력"><HistoryPanel history={w.memorySessions || []} /></ExistingPanel>}
      {analysis && <ExistingPanel id="voice-review-panel" title="대화로 남긴 코너" collapsible description="분석한 랩·코너에 메모를 연결하고 인식된 문장을 수정할 수 있습니다."><VoiceReview review={analysis.voice_review} filename={analysis.file?.name || w.selectedFile} laps={analysis.laps || []}
        corners={analysis.performance?.corners?.length ? analysis.performance.corners : analysis.coaching?.clean_segment_corners || []} apiBase={apiBase}
        onRefresh={() => w.runAnalysis?.({ includeAI: false })} onNavigate={navigate} onOpenCorner={onOpenCorner} /></ExistingPanel>}
      <ExistingPanel title="음성 답변·코너 메모" collapsible><VoiceNotes runtime={runtime} /></ExistingPanel>
      <ExistingPanel title="실시간 코너 비교"><CornerCoach coach={runtime.host.status?.corner_coach} controls={runtime.host.status?.controls} connected={!!runtime.host.status?.connected} /></ExistingPanel>
      <ExistingPanel title="음성 전달 진단"><RadioDiagnostics apiBase={apiBase} /></ExistingPanel>
      <ExistingPanel title="세션 로그"><SessionLogExport apiBase={apiBase} /></ExistingPanel>
    </> : <>
      <GameSettingsNotice />
      <ExistingPanel title="화면 도움말"><Field orientation="horizontal"><FieldLabel htmlFor="desktop-hover-help">마우스 오버 설명</FieldLabel><Switch id="desktop-hover-help" checked={w.hoverHelpEnabled !== false} onCheckedChange={w.toggleHoverHelp} /></Field></ExistingPanel>
      <AudioPreferences runtime={runtime} />
      <ExistingPanel id="overlay-settings" title="오버레이" description="주행 중 자동 표시와 미리보기, 크기·위치·불투명도를 설정합니다."><Button variant="outline" onClick={() => onNavigate('overlay')}>오버레이 설정 열기</Button></ExistingPanel>
      <ExistingPanel title="설치 설정"><AppSettings apiBase={apiBase} /></ExistingPanel>
      <ExistingPanel id="app-update" title="업데이트"><UpdateSettings apiBase={apiBase} /></ExistingPanel>
      <ExistingPanel id="external-reference" title="Garage61 기준·수집"><ReferenceSettings workspace={w} /></ExistingPanel>
      <ExistingPanel title="엔지니어 음성 직접 합성 (Google 키)"><RadioAudioPrepare apiBase={apiBase} /></ExistingPanel>
    </>}
  </section>;
}
