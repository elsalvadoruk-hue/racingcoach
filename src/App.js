import { HelpLayer, HelpHint, QuickStart, UserManual } from "./components/HelpGuide";
import WorkspaceShell from "./components/WorkspaceShell";
import { Button } from "./components/ui/button";
import { ChevronDown, Target, TimerReset, Sparkles, Mic, Route, Activity, Database, LineChart, SlidersHorizontal, FlaskConical, Thermometer, Layers3, UploadCloud, Info } from "lucide-react";
import { NativeSelect } from "./components/ui/native-select";
import { Badge } from "./components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "./components/ui/toggle-group";
import { TrialPanel, TrialHistory } from "./components/TrialWorkflow";
import LiveCoach, { VoiceReview } from "./components/LiveCoach";
import PracticeCoach from "./components/PracticeCoach";
import CornerComparison from "./components/CornerComparison";
import CornerNames from "./components/CornerNames";
import LapRangeFilter from "./components/LapRangeFilter";
import LapTimeUse from "./components/LapTimeUse";
import CornerConsistency from "./components/CornerConsistency";
import LapOverlay from "./components/LapOverlay";
import DamperHistogram from "./components/DamperHistogram";
import GGDiagram from "./components/GGDiagram";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "./components/ui/card";
import { Skeleton } from "./components/ui/skeleton";
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from "./components/ui/empty";
import OverlaySettings from "./components/OverlaySettings";
import AppSettings from "./components/AppSettings";
import SessionLogExport from "./components/SessionLogExport";
import React from "react";
import { useAnalysisWorkspace } from './analysis/useAnalysisWorkspace';
import { ErrorMessage, MetricCard, Panel, SessionModeChangeDialog } from './analysis/WorkspaceUi';
import { APP_TABS, QUALITY_THEME, SECTION_NAV, SESSION_MODES, cx, formatLapSeconds, pitServiceHint, sessionModeLabel } from './analysis/analysisShared';
import { ExperimentManager, FileSelect } from './analysis/ExperimentPanels';
import { ComparisonSummary, LapSelectionSummary, LapTable, QualityBanner, SessionModeHint } from './analysis/LapPanels';
import { API_BASE } from './analysis/analysisClient';
import { RunSummary, StructuredFeedback } from './analysis/SummaryPanels';
import { ClosedLoopCoachPanel, PersonalizationPanel } from './analysis/CoachingPanels';
import { CornerTable, PriorityCards } from './analysis/CornerPanels';
import { ExternalReferencePanel } from './analysis/ReferencePanel';
import { AIExperimentPlanner, AIUsageBanner, BriefingViewer } from './analysis/AiPanels';
import { SetupRecommendation } from './analysis/SetupPanels';
import { ClassificationPanel, RideAeroPanel, TirePanel } from './analysis/VehiclePanels';
import { HistoryPanel, LongTermMemoryPanel } from './analysis/HistoryPanels';

export default function App() {
  const {
    activeTab, activeTrial, analysis, analysisIsCurrent, analysisSnapshot, applyExternalReferences,
    beginTrial, canAttachTrial, canStartTrial, cancelGarage61Collection, classification,
    cleanSectionOnly, comparison, comparisonCorners, comparisonPath, comparisonTrace, completeTrial,
    confirmSessionModeChange, controlsExpanded, currentContext, detail, detailLoading, displayComparison,
    displayPriorities, error, externalCornerLoader, externalReferenceAnalysis, externalReferenceStatus,
    externalReferences, feedbackPhase, feedbackSpeed, feedbackSymptom, fileIndex, files,
    garage61ApiStatus, garage61Collector, garage61CollectorLoading, goal, handleExperimentStored,
    handleFileChange, handleReferenceLapChange, handleSessionModeChange, handleTargetLapChange,
    hasExternalTrace, hoverHelpEnabled, initialLoading, lapRange, loadComparisonDetail, loadFileLaps,
    loadMemory, loading, localCornerLoader, memorySessions, memoryStatus, modeChangePrompt,
    navigateToSection, newFileNotice, overlayFiles, overlayLoader, overlayReferences, patchTrial,
    pendingReferences, pickedLaps, pitRule, practiceGarage61Refs, quality, referenceLap,
    referenceLapManual, referenceMode, refreshExternalReferences, requestedCorner, resetAutoLapSelection,
    runAnalysis, selectTab, selectedAssignedMode, selectedExternalReferences, selectedFile,
    selectedFileEntry, selectedFileRef, selectedStoredModes, session, sessionMode, setControlsExpanded,
    setError, setFeedbackPhase, setFeedbackSpeed, setFeedbackSymptom, setGoal, setLapRange,
    setModeChangePrompt, setPendingReferences, setPitRule, setReferenceLapManual, setReferenceMode,
    setSelectedExternalReferences, setSetupMode, setTargetLapManual, setUserFeedback, setupMode,
    startGarage61Collection, status, tableCornerLoader, targetLap, targetLapManual,
    toggleExternalReference, toggleHoverHelp, trialHistory, userFeedback, wantsExternalComparison,
    workflow
  } = useAnalysisWorkspace();
  return (
    <div className="coach-ui font-sans text-base leading-relaxed">
      <HelpLayer hoverEnabled={hoverHelpEnabled} />
      <SessionModeChangeDialog
        prompt={modeChangePrompt}
        loading={loading}
        onCancel={() => setModeChangePrompt(null)}
        onConfirm={confirmSessionModeChange}
      />

      <WorkspaceShell tabs={APP_TABS} activeTab={activeTab} onTab={selectTab}
        hoverHelpEnabled={hoverHelpEnabled} onToggleHelp={toggleHoverHelp} onGuide={() => navigateToSection('user-guide')}
        hasAnalysis={Boolean(analysis)} trialActive={Boolean(activeTrial)} sections={analysis ? SECTION_NAV[activeTab] : null}>
        <div hidden={!['summary', 'compare', 'setup'].includes(activeTab) && !activeTrial}>
          <Button variant="outline" type="button" className="coach-context-toggle" aria-expanded={controlsExpanded || !analysis || Boolean(activeTrial && !activeTrial.candidate_file)} disabled={!analysis || Boolean(activeTrial && !activeTrial.candidate_file)} onClick={() => setControlsExpanded((value) => !value)}>
            <span><span className="coach-context-subject">{selectedFileEntry?.car_name || session?.car_name || '주행 파일 선택'}</span><span className="coach-context-track">{selectedFileEntry?.track_name || session?.track_name || 'IBT 파일을 선택하세요.'}</span></span><span>주행·비교 기준 변경 <ChevronDown size={14} aria-hidden="true" /></span>
          </Button>
          <div className={cx('coach-control-body', (controlsExpanded || !analysis || Boolean(activeTrial && !activeTrial.candidate_file)) && 'is-open')}>
          <header className="coach-control-deck" aria-label="주행과 비교 기준 선택">
            <div className="coach-control-column">
              <div className="coach-control-title">주행 선택<HelpHint topic="file" /></div>
              <div className="coach-control-row">
                <FileSelect label="분석할 주행" value={selectedFile} onChange={handleFileChange} files={files} hideLabel />
                <Button variant="default" data-help="analyze" type="button" disabled={loading || !selectedFile || selectedFileEntry?.analyzable === false} onClick={() => runAnalysis({ includeAI: false })} className="coach-primary-action">
                  {loading ? '분석 중…' : '분석 갱신'}
                </Button>
              </div>
              {!selectedFileEntry && <p className="coach-control-context">IBT 파일을 선택하면 자동으로 분석합니다.</p>}
              {(selectedFileEntry?.analyzable === false || selectedFileEntry?.retryable) && (selectedFileEntry?.eligibility_reason || selectedFileEntry?.reason) && <p className="mt-2 text-xs text-warning">{selectedFileEntry.eligibility_reason || selectedFileEntry.reason}</p>}
              
            </div>
            <div className="coach-control-column">
              <div className="coach-control-title">비교 기준<HelpHint topic="reference" /></div>
              <div className="coach-reference-controls">
          <div className="text-sm text-muted-foreground"><label className="sr-only" htmlFor="reference-mode">비교 기준</label><NativeSelect id="reference-mode" data-help="reference" aria-label="비교 기준 방식" value={referenceMode} disabled={Boolean(activeTrial) || loading} onChange={(event) => setReferenceMode(event.target.value)} className="min-h-[50px] w-full"><option value="LOCAL">내 주행의 기준 랩</option><option value="AGGREGATE">Garage61 여러 랩 합성</option><option value="SINGLE">Garage61 실제 드라이버 한 랩</option></NativeSelect></div>
          {referenceMode === 'SINGLE' ? <div className="text-sm font-bold text-muted-foreground"><div className="flex items-center gap-1"><label htmlFor="reference-driver">실제 기준 드라이버</label><HelpHint topic="single" /></div><NativeSelect id="reference-driver" data-help="single" aria-label="Garage61 실제 기준 랩" value={selectedExternalReferences[0] || ''} disabled={Boolean(activeTrial) || loading} onChange={(event) => setSelectedExternalReferences([event.target.value])} className="mt-2 w-full"><option value="">기준 랩 선택</option>{externalReferences.map((file) => <option key={file.name} value={file.name} disabled={file.usable === false}>{file.driver_name || file.name} · {file.lap_time || formatLapSeconds(file.lap_time_s)}</option>)}</NativeSelect></div> : <p className="coach-reference-note">{referenceMode === 'LOCAL' ? '내 주행의 두 랩 비교' : `Garage61 ${selectedExternalReferences.length}개 선택 · 코너 비교에서 변경`}</p>}
          <Button variant="outline" data-help="referenceApply" type="button" onClick={() => runAnalysis({ referenceModeOverride: referenceMode })} disabled={loading || !selectedFile || Boolean(activeTrial) || (referenceMode !== 'LOCAL' && !selectedExternalReferences.length)} className="coach-secondary-action coach-reference-apply">비교 기준 적용</Button>

              </div>
            </div>
          </header>
          </div>
          <section className="coach-control-meta" aria-label="주행 유형 · 기록 저장"><div className="coach-control-meta-heading">주행 유형 · 기록 저장<Badge variant="secondary">{sessionModeLabel(selectedAssignedMode || sessionMode)}</Badge></div>
                
                <ToggleGroup type="multiple" variant="outline" size="sm" value={[sessionMode]} onValueChange={values => handleSessionModeChange(values.find(mode => mode !== sessionMode) || sessionMode)} disabled={loading || !selectedFile || selectedFileEntry?.analyzable === false} aria-label="주행 유형" className="flex flex-wrap justify-start gap-2">{SESSION_MODES.map(({ value, label }) => <ToggleGroupItem data-help="session" key={value} value={value}>{label}</ToggleGroupItem>)}</ToggleGroup><HelpHint topic="session" />
                {selectedStoredModes.length > 1 && <p>저장 유형: {selectedStoredModes.map(sessionModeLabel).join(' / ')}</p>}
                <SessionModeHint file={selectedFileEntry} stored={selectedAssignedMode} current={sessionMode} />
                {sessionMode === 'ENDURANCE' && <div className="mt-3 flex flex-wrap items-center gap-2"><span>피트 규정</span><HelpHint topic="pit" />{['CONCURRENT','SEQUENTIAL'].map((rule) => <Button variant="outline" type="button" data-help="pit" key={rule} aria-pressed={pitRule === rule} onClick={() => setPitRule(rule)} className="border  px-3 py-2 text-xs">{rule === 'CONCURRENT' ? '동시 교체' : '순차 교체'}</Button>)}
                  {pitServiceHint(analysis?.pit_service) && <span className="text-xs text-muted-foreground" data-testid="pit-service-hint">{pitServiceHint(analysis.pit_service)}</span>}</div>}
              </section>
        <details className="coach-quality-line"><summary data-help="quality" className="cursor-pointer text-sm font-bold text-muted-foreground">데이터 품질 · 유효랩 {quality?.valid_laps ?? selectedFileEntry?.valid_laps ?? '--'} · {cleanSectionOnly ? '부분 코너 관측' : QUALITY_THEME[quality?.state]?.label || '확인 중'}</summary><div className="mt-3"><QualityBanner quality={quality} /></div></details>
        </div>

        {error && <ErrorMessage message={error} />}
        {['summary', 'compare', 'setup'].includes(activeTab) && status?.telemetry?.retryable && <div role="status" className="rounded-xl border border-primary/30 bg-primary/15 p-3 text-sm text-primary">{status.telemetry.reason || 'IBT 파일 읽기 대기 · 텔레메트리 기록이 종료되면 자동으로 다시 확인합니다.'}</div>}
        {workflow.storageError && <ErrorMessage message={workflow.storageError} />}
        {['summary', 'compare', 'setup'].includes(activeTab) && fileIndex?.pending_count > 0 && <div className="rounded-xl border border-border bg-background/40 p-3 text-sm text-muted-foreground">상세 정보 미확인 파일 {fileIndex.pending_count}개 · 파일을 선택하면 랩 수와 사용 가능한 코너를 확인합니다.</div>}
        {['summary', 'compare', 'setup'].includes(activeTab) && newFileNotice && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-primary/15 p-3 text-sm text-primary"><span>새 주행 발견: {newFileNotice} · 실험의 변경 전 A는 유지합니다.</span><Button variant="outline" type="button" onClick={() => handleFileChange(newFileNotice)} className=" border  px-3 py-2 ">새 주행 확인</Button></div>}
        <TrialPanel trial={activeTrial} currentFile={selectedFile} canAttach={canAttachTrial} onAttach={() => canAttachTrial && patchTrial({ candidate_file: selectedFile, candidate_context: currentContext })}
          onPatch={patchTrial} onComplete={completeTrial} onCompare={() => navigateToSection('ab-manager')} />
        {['summary', 'compare', 'setup'].includes(activeTab) && pendingReferences && <div className="coach-notice flex flex-wrap items-center justify-between gap-3 rounded-xl border border-accent/25 bg-accent/10 p-3 text-sm text-foreground"><span>{activeTrial ? '새 기준 자료 도착 · 실험 기준 고정' : '새 기준 자료 도착'}</span><Button variant="outline" type="button" disabled={Boolean(activeTrial) || loading} onClick={() => { const next = pendingReferences; setSelectedExternalReferences(next); if (referenceMode === 'LOCAL') setReferenceMode('AGGREGATE'); setPendingReferences(null); runAnalysis({ externalReferencesOverride: next, referenceModeOverride: referenceMode === 'LOCAL' ? 'AGGREGATE' : referenceMode }); }} className=" border  px-3 py-2  disabled:opacity-40">새 기준 적용</Button></div>}
        {['summary', 'compare', 'setup'].includes(activeTab) && analysis && !analysisIsCurrent && <div className="rounded-xl border border-warning/30 bg-warning/15 p-3 text-sm text-warning">입력이나 비교 기준이 바뀌었습니다. 현재 카드에는 이전 분석 조건의 결과가 표시됩니다. 분석을 갱신하면 셋업 실험을 시작할 수 있습니다.</div>}
        {['summary', 'compare'].includes(activeTab) && analysisSnapshot?.referenceMode === 'AGGREGATE' && externalReferenceAnalysis?.aggregate?.representative_reference_file && <div className="coach-reference-context">여러 랩 합성 기준 <Button variant="outline" type="button" disabled={Boolean(activeTrial) || loading} onClick={() => { const filename = externalReferenceAnalysis.aggregate.representative_reference_file; setReferenceMode('SINGLE'); setSelectedExternalReferences([filename]); runAnalysis({ referenceModeOverride: 'SINGLE', externalReferencesOverride: [filename] }); }} className="ml-2  border  px-3 py-1   disabled:opacity-40">대표 드라이버의 실제 한 랩으로 보기</Button></div>}
        {cleanSectionOnly && <div className="rounded-xl border border-warning/30 bg-warning/15 p-3 text-sm text-warning">부분 코너 관측 · 유효 완주랩은 없습니다. 깨끗한 구간 {quality?.clean_segment_count || analysis.coaching?.clean_segment_corners?.length || 0}개를 코칭에 사용하며 랩 페이스와 A/B 속도 효과 근거에는 포함하지 않습니다.</div>}


        {activeTab === 'summary' && !analysis && <QuickStart hasAnalysis={false} onGuide={() => navigateToSection('user-guide')} onSetup={() => navigateToSection('briefing')} />}
        <div id="user-guide" className="scroll-mt-40" hidden={activeTab !== 'guide'} role="region" aria-labelledby="tab-guide"><UserManual hasAnalysis={Boolean(analysis)} onNavigate={navigateToSection} /></div>
        <LiveCoach apiBase={API_BASE} visible={activeTab === 'live'} />
        {analysis ? (
          <>
            <div hidden={activeTab !== 'summary'} role="region" aria-labelledby="tab-summary" className="flex flex-col gap-6">
<Panel title="오늘의 한 가지" icon={Target} id="practice-coach">
              <PracticeCoach apiBase={API_BASE} filename={analysis.file?.name || selectedFile} practice={analysis.practice} coach={analysis.closed_loop_coach} visualizations={analysis.visualizations} trackLength={analysis.visualizations?.track_length_m || analysis.session?.track_length_m}
                corners={analysis.performance?.corners || []} loadWindow={localCornerLoader} referenceLabel={comparison?.reference_lap != null ? `내 기준 랩 ${comparison.reference_lap}` : '내 기준 랩'}
                externalCorners={externalReferenceAnalysis?.status === 'READY' ? externalReferenceAnalysis.corners || [] : []}
                externalLoadWindow={externalCornerLoader} externalLabel={externalReferenceAnalysis?.aggregate?.reference_label}
                focusSets={analysis.practice_focuses} garage61Available={!activeTrial && practiceGarage61Refs.length > 0}
                onUseGarage61={() => { setSelectedExternalReferences(practiceGarage61Refs); setReferenceMode('AGGREGATE'); runAnalysis({ referenceModeOverride: 'AGGREGATE', externalReferencesOverride: practiceGarage61Refs }); }} />
            </Panel>
            <Panel title="이번 주행 요약" icon={TimerReset} id="run-summary">
              <RunSummary analysis={analysis} onNavigate={navigateToSection} referenceMode={analysisSnapshot?.referenceMode || 'LOCAL'} />
            </Panel>
            <QuickStart hasAnalysis onGuide={() => navigateToSection('user-guide')} onSetup={() => navigateToSection('briefing')} />
            <Panel title="다음 주행에서 할 일" icon={Sparkles} id="closed-loop-coach">
              <ClosedLoopCoachPanel coach={analysis.closed_loop_coach} currentFile={selectedFile} memory={analysis.memory} />
            </Panel>
            {/* Long lists of memos sit last and folded, so the summary stays one screen. */}
            <Panel title={`대화로 남긴 코너${analysis.voice_review?.notes?.length ? ` · ${analysis.voice_review?.notes?.length}건` : ''}`} icon={Mic} id="voice-review-panel" collapsible defaultOpen={false}>
            <VoiceReview review={analysis.voice_review} filename={analysis.file?.name || selectedFile} laps={analysis.laps || []}
              corners={analysis.performance?.corners?.length ? analysis.performance.corners : analysis.coaching?.clean_segment_corners || []}
              apiBase={API_BASE} onRefresh={() => runAnalysis({ includeAI: false })} onNavigate={navigateToSection}
              onOpenCorner={async (note) => {
                if (activeTrial || loading) { setError('진행 중 실험의 랩과 기준을 유지합니다. 실험 종료 후 메모 랩을 비교하세요.'); return; }
                if (Number(comparison?.target_lap) !== Number(note.lap_number)) {
                  const sameAsReference = Number(referenceLap) === Number(note.lap_number);
                  const payload = await runAnalysis({ includeAI: false, persistMemory: false, targetLapOverride: Number(note.lap_number), ...(sameAsReference ? { referenceLapOverride: null } : {}) });
                  if (!payload || Number(payload.comparison?.target_lap) !== Number(note.lap_number)) { setError('이 랩은 비교 대상으로 사용할 수 없습니다. 대화 카드의 해당 랩 관측을 확인하세요.'); return; }
                  setTargetLapManual(true);
                  if (sameAsReference) setReferenceLapManual(false);
                }
                navigateToSection('corner-inputs', { distance_pct: note.distance_pct });
              }} />
            </Panel>
            </div>
            <div hidden={activeTab !== 'compare'} role="region" aria-labelledby="tab-compare" className="flex flex-col gap-6">
<div id="corner-inputs">
              <CornerComparison trace={comparisonTrace} corners={comparisonCorners} pathData={comparisonPath} requestedCorner={requestedCorner}
                referencePath={wantsExternalComparison ? externalReferenceAnalysis?.visualizations?.reference_track_path : analysis.visualizations?.reference_track_path}
                referenceLineSource={wantsExternalComparison ? externalReferenceAnalysis?.visualizations?.reference_line_source : analysis.visualizations?.reference_line_source}
                detailReferencePath={detail?.reference_track_path} detailLineSource={detail?.reference_line_source}
                trackIdentity={session} trackLengthM={session?.track_length_m || analysis.visualizations?.track_length_m}
                referenceLabel={hasExternalTrace ? externalReferenceAnalysis.aggregate?.reference_label || (analysisSnapshot?.referenceMode === 'SINGLE' ? '선택한 Garage61 드라이버' : 'Garage61 합성 기준') : comparison?.reference_lap != null ? `내 기준 랩 ${comparison.reference_lap}` : '비교 기준 없음'}
                comparisonKey={JSON.stringify([analysisSnapshot?.filename, analysisSnapshot?.referenceMode, analysisSnapshot?.references, analysis?.file?.modified_at, comparison?.target_lap, comparison?.reference_lap])} onRangeChange={loadComparisonDetail} detailTrace={detail?.trace} detailPath={detail?.track_path} detailQuality={detail?.quality} detailLoading={detailLoading} />
            </div>
            <Panel title="코너 번호 확인" icon={Route} id="corner-names" collapsible defaultOpen={!analysis.performance?.corner_names_confirmed}>
              <CornerNames apiBase={API_BASE} filename={analysis.file?.name} onSaved={() => runAnalysis({ includeAI: false, quiet: true })} />
            </Panel>
            <Panel title="거리 기준 랩 비교" icon={TimerReset} id="comparison">
              <ComparisonSummary comparison={displayComparison} lapSelection={hasExternalTrace ? null : analysis.lap_selection} />
            </Panel>
<Panel title="랩타임 단축 우선순위" icon={Target} id="priorities">
              <PriorityCards priorities={displayPriorities} />
            </Panel>
<Panel title="Garage61 기준 주행" icon={Target} collapsible defaultOpen={false} id="external-reference">
              <ExternalReferencePanel
                referenceStatus={externalReferenceStatus}
                files={externalReferences}
                selected={selectedExternalReferences}
                analysis={externalReferenceAnalysis}
                loading={loading || Boolean(activeTrial)}
                collector={garage61Collector}
                collectorLoading={garage61CollectorLoading}
                apiStatus={garage61ApiStatus}
                onToggle={toggleExternalReference}
                onRefresh={refreshExternalReferences}
                onApply={applyExternalReferences}
                onAutoSelect={() => !activeTrial && setSelectedExternalReferences((externalReferenceStatus?.recommended_selection || []).slice(0, externalReferenceStatus?.display_limit || externalReferenceStatus?.max_selection || 9))}
                onClear={() => !activeTrial && setSelectedExternalReferences([])}
                onCollect={() => startGarage61Collection(selectedFileRef.current, false)}
                onForceCollect={() => startGarage61Collection(selectedFileRef.current, true)}
                onCancelCollect={cancelGarage61Collection}
              />
            </Panel>
<Panel title="랩별 시간 쓰임새" icon={Activity} collapsible defaultOpen={false} id="lap-time-use">
              <LapRangeFilter laps={analysis.laps || []} value={lapRange} onChange={setLapRange} />
              <div className="mt-4"><LapTimeUse laps={(analysis.laps || []).filter((lap) => pickedLaps.has(lap.lap_number))} /></div>
            </Panel>
<Panel title="랩 선택 및 품질" icon={Database} collapsible defaultOpen={false} id="lap-detail">
              <LapSelectionSummary
                selection={analysis.lap_selection}
                onResetAuto={resetAutoLapSelection}
                loading={loading}
              />
              <LapTable
                laps={analysis.laps}
                targetLap={targetLap}
                referenceLap={referenceLap}
                onTargetChange={handleTargetLapChange}
                onReferenceChange={handleReferenceLapChange}
              />
              <div className="mt-4 flex justify-end">
                <Button variant="outline"
                  type="button"
                  onClick={() => runAnalysis({ includeAI: false })}
                  data-help="analyze"
                  disabled={!targetLap || loading}
                  className=" border   px-4 py-2 text-sm   transition  disabled:opacity-30"
                >
                  {targetLapManual || referenceLapManual ? '수동 선택 랩으로 재계산' : '자동 선택 랩으로 재계산'}
                </Button>
              </div>
            </Panel>
<Panel title="코너 자동 분할 및 위상별 비교" icon={Route} collapsible defaultOpen={false} id="corner-table">
              <CornerTable corners={comparisonCorners} trackLength={analysis.visualizations?.track_length_m || analysis.session?.track_length_m} loadWindow={tableCornerLoader}
                referenceLabel={wantsExternalComparison ? 'Garage61 기준' : comparison?.reference_lap != null ? `기준 랩 ${comparison.reference_lap}` : '기준 랩'} />
            </Panel>
            <Panel title="코너별 일관성" icon={Activity} collapsible defaultOpen={false} id="corner-consistency">
              <LapRangeFilter laps={analysis.laps || []} value={lapRange} onChange={setLapRange} />
              <div className="mt-4"><CornerConsistency consistency={analysis.performance?.corner_consistency} allowedLaps={pickedLaps} /></div>
            </Panel>
            <Panel title="전문가 보기 · 여러 랩 겹쳐 보기" icon={LineChart} collapsible defaultOpen={false} lazy id="lap-overlay">
              <LapOverlay laps={analysis.laps || []} corners={analysis.performance?.corners || []} load={analysisSnapshot ? overlayLoader : null}
                preferredLaps={[comparison?.reference_lap, comparison?.target_lap]}
                otherFiles={overlayFiles} references={overlayReferences} loadLaps={loadFileLaps} />
            </Panel>
            </div>
            <div hidden={activeTab !== 'setup'} role="region" aria-labelledby="tab-setup" className="flex flex-col gap-6">
<Panel title="드라이버 피드백과 검증" icon={SlidersHorizontal} id="briefing">
              <StructuredFeedback phase={feedbackPhase} speed={feedbackSpeed} symptom={feedbackSymptom}
                setupMode={setupMode} onSetupMode={setSetupMode}
                onPhase={setFeedbackPhase} onSpeed={setFeedbackSpeed} onSymptom={setFeedbackSymptom}
                value={userFeedback} onChange={setUserFeedback} result={analysis.structured_feedback}
                coach={analysis.closed_loop_coach} onAnalyze={() => runAnalysis({ includeAI: false })}
                onStart={beginTrial} canStart={canStartTrial} goal={goal} onGoal={setGoal} trialActive={Boolean(activeTrial)}
                onNavigate={navigateToSection} loading={loading} />
            </Panel>
            <Panel title="엔지니어 브리핑" icon={Sparkles} collapsible defaultOpen={false} id="engineer-briefing">
              <div className="mb-4 flex justify-end"><Button variant="outline" data-help="ai" type="button" onClick={() => runAnalysis({ includeAI: true })}
                disabled={loading || !status?.ai_enabled} className=" border   px-4 py-3 text-sm   disabled:opacity-40">{status?.ai_enabled ? 'AI 브리핑 생성 · 토큰 사용' : 'AI 연결 필요'}</Button></div>
              <BriefingViewer briefing={analysis.briefing} />
            </Panel>
              <Panel title="셋업 결정" icon={SlidersHorizontal} id="setup-decision">
                <SetupRecommendation recommendation={analysis.setup_recommendation} onStart={beginTrial} canStart={canStartTrial} />
              </Panel>
<Panel title="셋업 A/B 비교" icon={FlaskConical} id="ab-manager">
              <ExperimentManager
                files={files}
                sessionMode={sessionMode}
                preferredBaseline={activeTrial?.baseline_file || selectedFile}
                trial={activeTrial} goal={goal} onComparison={(result) => patchTrial({ comparison: result })}
                onStored={handleExperimentStored}
              />
            </Panel>
              <Panel title="원인 분류" icon={Activity} id="decision" collapsible defaultOpen={false}>
                <ClassificationPanel classification={classification} />
              </Panel>
<Panel title="AI 셋업 실험 계획" icon={Sparkles} collapsible defaultOpen={false} id="ai-planner">
              <AIExperimentPlanner
                selectedFile={selectedFile}
                sessionMode={sessionMode}
                pitRule={pitRule}
                targetLap={targetLap}
                referenceLap={referenceLap}
                userFeedback={userFeedback}
                feedbackPhase={feedbackPhase}
                feedbackSpeed={feedbackSpeed}
                feedbackSymptom={feedbackSymptom}
                setupMode={setupMode}
                aiEnabled={Boolean(status?.ai_enabled)}
                externalReferenceFiles={selectedExternalReferences}
                onStored={loadMemory}
              />
            </Panel>
<Panel title="타이어 종료 스냅샷" icon={Thermometer} collapsible defaultOpen={false} id="tire-snapshot">
              <TirePanel tires={analysis.vehicle?.tires} />
            </Panel>

            <Panel title="차고·서스펜션·에어로" icon={Layers3} collapsible defaultOpen={false} id="ride-aero">
              <RideAeroPanel
                rideHeight={analysis.vehicle?.ride_height}
                aero={analysis.vehicle?.aero}
              />
            </Panel>
            <Panel title="댐퍼 속도 분포" icon={Activity} collapsible defaultOpen={false} id="damper-velocity">
              <DamperHistogram current={analysis.vehicle?.damper_velocity} />
            </Panel>
            <Panel title="G-G 그림 (그립 사용)" icon={Activity} collapsible defaultOpen={false} id="gg-diagram">
              <GGDiagram current={analysis.vehicle?.gg} />
            </Panel>
            </div>
            <div hidden={activeTab !== 'history'} role="region" aria-labelledby="tab-history" className="flex flex-col gap-6">
<Panel title="내 주행 특성과 셋업 학습" icon={Activity} id="personalization">
              <PersonalizationPanel memory={analysis.memory} />
            </Panel>
            <Panel title="종료한 셋업 실험" icon={FlaskConical}><TrialHistory history={trialHistory} /></Panel>
<Panel title="개인 기록 저장" icon={Database}>
              <LongTermMemoryPanel
                memoryStatus={memoryStatus}
                sessions={memorySessions}
                currentMemory={analysis.memory}
              />
            </Panel>
            </div>
          </>
        ) : activeTab === 'history' ? (
          <div className="flex flex-col gap-6"><Panel title="셋업 실험 이력" icon={FlaskConical}><TrialHistory history={trialHistory} /></Panel><Panel title="주행 분석 이력" icon={Database}><HistoryPanel history={memorySessions} /></Panel></div>
        ) : activeTab !== 'settings' && activeTab !== 'guide' && activeTab !== 'live' ? (
          loading || initialLoading ? (
            <Card aria-busy="true" role="status" aria-label="주행 분석 중">
              <CardHeader><CardTitle>선택한 주행을 분석하고 있습니다.</CardTitle><CardDescription>랩과 코너 데이터를 확인하고 있습니다.</CardDescription></CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-3">{[0, 1, 2].map(index => <Skeleton key={index} className="h-24 rounded-xl" />)}</CardContent>
            </Card>
          ) : (
            <Empty className="border">
              <EmptyHeader><EmptyMedia variant="icon"><UploadCloud aria-hidden="true" /></EmptyMedia><EmptyTitle role="heading" aria-level={2}>분석할 주행을 선택하세요.</EmptyTitle><EmptyDescription>IBT 파일을 선택하면 주행 분석을 시작합니다.</EmptyDescription></EmptyHeader>
            </Empty>
          )
        ) : null}
        <div id="settings" hidden={activeTab !== 'settings'} role="region" aria-labelledby="tab-settings" className="flex flex-col gap-6">
          {analysis && <AIUsageBanner usage={analysis.ai_usage} aiEnabled={Boolean(status?.ai_enabled)} />}
          <Panel title="연결과 데이터 상태" icon={Activity}>
            <div className="coach-connection-cards grid gap-3 md:grid-cols-3">
              <MetricCard label="텔레메트리" value={status?.telemetry ? '파일 확인됨' : '주행 파일 대기'} note={status?.telemetry?.file_name} icon={Activity} tone="cyan" />
              <MetricCard label="Garage61 개인 API" value={garage61ApiStatus?.connected ? '연결됨' : garage61ApiStatus?.configured ? '연결 확인 필요' : '설정 필요'} note={garage61ApiStatus?.message} icon={Target} tone="violet" />
              <MetricCard label="AI 브리핑" value={status?.ai_enabled ? '사용 가능' : '연결 필요'} note="실행할 때만 토큰을 사용합니다." icon={Sparkles} tone="slate" />
            </div>
            <div className="coach-storage-row"><span>개인 기록</span><strong>{memoryStatus?.enabled ? `${memoryStatus.counts?.sessions ?? 0} 세션 저장됨` : memoryStatus?.error || '연결 확인 필요'}</strong></div>
            {memoryStatus?.path && <dl className="coach-storage-row"><dt>저장 위치</dt><dd className="min-w-0 break-all text-foreground">{memoryStatus.path}</dd></dl>}
          </Panel>
          <Panel title="오버레이" icon={Layers3} id="overlay-settings"><OverlaySettings apiBase={API_BASE} visible={activeTab === 'settings'} /></Panel>
          <Panel title="설치 설정" icon={Database}><AppSettings apiBase={API_BASE} visible={activeTab === 'settings'} /></Panel>
          <Panel title="세션 로그 보내기" icon={Activity}><SessionLogExport apiBase={API_BASE} visible={activeTab === 'settings'} /></Panel>
          <Panel title="사용 방법" icon={Info}><p className="text-sm text-foreground">사용 가이드에서 첫 주행, Garage61 비교, 셋업 A/B 실험과 문제 해결을 확인하세요.</p><Button variant="outline" type="button" onClick={() => navigateToSection('user-guide')} className="mt-3  border  px-4 py-2  ">전체 매뉴얼 열기</Button></Panel>
        </div>
      </WorkspaceShell>
    </div>
  );
}
