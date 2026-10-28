import { useHoverHelpSetting } from "../components/HelpGuide";
import { useTrialWorkflow } from "../components/TrialWorkflow";
import { useRef, useState, useCallback, useEffect, useMemo } from "react";
import { lapsInRange } from "../components/LapRangeFilter";
import { requestJson } from './analysisClient';
import { analysisContextKey, assignedModeForFile, comparisonContext, formatSetupOriginal, formatSetupTarget, formatSigned, hasSetupNumericValue, initialModeForFile, sameVehicleAndTrack, storedModesForFile } from './analysisShared';
import { useWorkspaceInitialization } from './useWorkspaceInitialization';
import { useTelemetryEvents } from './useTelemetryEvents';
import { useCollectorPolling } from './useCollectorPolling';
import { useAnalysisProgress } from '../desktop/AnalysisProgress';
import { isSelectableRecording } from '../lib/recordingFiles';

export function useAnalysisWorkspace() {
  const workspaceMounted = useRef(true);
  useEffect(() => { workspaceMounted.current = true; return () => { workspaceMounted.current = false; }; }, []);

  const [hoverHelpEnabled, toggleHoverHelp] = useHoverHelpSetting();
  const workflow = useTrialWorkflow();
  const { activeTrial, history: trialHistory, readDraft, saveDraft, patchTrial, startTrial, completeTrial, savePreferences } = workflow;
  const bootDraft = useRef(readDraft(workflow.preferences.selectedFile));
  const [activeTab, setActiveTab] = useState('summary');
  const [requestedCorner, setRequestedCorner] = useState(null);
  const [controlsExpanded, setControlsExpanded] = useState(false);
  const selectTab = useCallback((tab) => {
    setActiveTab(tab);
    window.requestAnimationFrame(() => document.getElementById('coach-content')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }, []);
  const [feedbackPhase, setFeedbackPhase] = useState(bootDraft.current.feedbackPhase || '');
  const [feedbackSpeed, setFeedbackSpeed] = useState(bootDraft.current.feedbackSpeed || '');
  const [feedbackSymptom, setFeedbackSymptom] = useState(bootDraft.current.feedbackSymptom || '');
  const [setupMode, setSetupMode] = useState(bootDraft.current.setupMode || 'EXPLORE');
  const [goal, setGoal] = useState(activeTrial?.goal || workflow.preferences.goal || 'COMFORT');
  const [referenceMode, setReferenceMode] = useState(activeTrial?.reference_mode || bootDraft.current.referenceMode || 'AGGREGATE');
  const [sessionMode, setSessionMode] = useState('BASELINE_3LAP');
  const [pitRule, setPitRule] = useState('NOT_APPLICABLE');
  const [status, setStatus] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  // Laps picked by lap time for the per-lap statistics (time use, corner consistency); a new file starts with all.
  const [lapRange, setLapRange] = useState(null);
  const analysedFileName = analysis?.file?.name;
  useEffect(() => { setLapRange(null); }, [analysedFileName]);
  const pickedLaps = useMemo(() => lapsInRange(analysis?.laps || [], lapRange), [analysis, lapRange]);
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState('');
  const [targetLap, setTargetLap] = useState('');
  const [referenceLap, setReferenceLap] = useState('');
  const [targetLapManual, setTargetLapManual] = useState(false);
  const [referenceLapManual, setReferenceLapManual] = useState(false);
  const [userFeedback, setUserFeedback] = useState(bootDraft.current.userFeedback || '');
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [initializationAttempt, setInitializationAttempt] = useState(0);
  const retryInitialization = useCallback(() => {
    setError(''); setInitialLoading(true); setInitializationAttempt(value => value + 1);
  }, []);
  const { view: analysisProgress, begin: beginProgress, end: endProgress } = useAnalysisProgress();
  const [error, setError] = useState('');
  const [memoryStatus, setMemoryStatus] = useState(null);
  const [memorySessions, setMemorySessions] = useState([]);
  const [externalReferenceStatus, setExternalReferenceStatus] = useState(null);
  const [externalReferences, setExternalReferences] = useState([]);
  const [selectedExternalReferences, setSelectedExternalReferences] = useState(activeTrial?.reference_files || bootDraft.current.referenceFiles || []);
  const selectedExternalRef = useRef(selectedExternalReferences);
  selectedExternalRef.current = selectedExternalReferences;
  const [garage61Collector, setGarage61Collector] = useState(null);
  const [garage61CollectorLoading, setGarage61CollectorLoading] = useState(false);
  const [garage61ApiStatus, setGarage61ApiStatus] = useState(null);
  const [modeChangePrompt, setModeChangePrompt] = useState(null);
  const [fileIndex, setFileIndex] = useState(null);
  const [pendingReferences, setPendingReferences] = useState(null);
  const [newFileNotice, setNewFileNotice] = useState('');
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [analysisSnapshot, setAnalysisSnapshot] = useState(null);
  const signatureRef = useRef(null);
  const waitingFileRef = useRef('');
  const filesRef = useRef([]);
  const selectedFileRef = useRef('');
  const collectorHandledJobRef = useRef('');
  const collectorAutoContextRef = useRef('');
  const collectorTerminalRetryRef = useRef({ jobId: '', attempts: 0 });
  const runAnalysisRef = useRef(null);
  const analysisRequestRef = useRef({ id: 0, controller: null });
  const detailRequestRef = useRef({ id: 0, controller: null });
  const activeTrialRef = useRef(activeTrial);
  activeTrialRef.current = activeTrial;
  const restoredFileRef = useRef(workflow.preferences.selectedFile || activeTrial?.baseline_file || '');
  const draftFileRef = useRef(restoredFileRef.current);
  const referenceModeRef = useRef(referenceMode);
  referenceModeRef.current = referenceMode;

  const restoreFileDraft = useCallback((filename) => {
    const draft = readDraft(filename);
    draftFileRef.current = filename;
    setFeedbackPhase(draft.feedbackPhase || ''); setFeedbackSpeed(draft.feedbackSpeed || '');
    setFeedbackSymptom(draft.feedbackSymptom || ''); setUserFeedback(draft.userFeedback || '');
    setSetupMode(draft.setupMode || 'EXPLORE');
    setReferenceMode(activeTrialRef.current?.reference_mode || draft.referenceMode || 'AGGREGATE');
    setSelectedExternalReferences(activeTrialRef.current?.reference_files || draft.referenceFiles || []);
  }, [readDraft]);

  useEffect(() => {
    if (!selectedFile || draftFileRef.current !== selectedFile) return;
    saveDraft(selectedFile, { feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, userFeedback, referenceMode, referenceFiles: selectedExternalReferences });
    savePreferences({ selectedFile, goal });
  }, [feedbackPhase, feedbackSpeed, feedbackSymptom, goal, referenceMode, saveDraft, savePreferences, selectedExternalReferences, selectedFile, setupMode, userFeedback]);

  const navigateToSection = useCallback((id, cornerId = null) => {
    if (id === 'corner-inputs' && cornerId !== null && cornerId !== undefined) setRequestedCorner(typeof cornerId === 'object' ? cornerId : { id: cornerId });
    const tab = ({ priorities: 'compare', comparison: 'compare', 'lap-detail': 'compare', 'external-reference': 'compare',
      'corner-inputs': 'compare', decision: 'setup', 'setup-decision': 'setup', 'ab-manager': 'setup', briefing: 'setup',
      personalization: 'history', settings: 'settings', summary: 'summary', 'user-guide': 'guide', 'closed-loop-coach': 'summary', 'live-coach': 'live', 'voice-review': 'summary' })[id] || 'summary';
    setActiveTab(tab);
    window.dispatchEvent(new CustomEvent('coach-reveal-section', { detail: id }));
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
  }, []);

  useEffect(() => {
    const navigate = (event) => navigateToSection(event.detail);
    window.addEventListener('coach-navigate', navigate);
    return () => window.removeEventListener('coach-navigate', navigate);
  }, [navigateToSection]);

  useEffect(() => () => { analysisRequestRef.current.controller?.abort(); detailRequestRef.current.controller?.abort(); }, []);

  useEffect(() => {
    selectedFileRef.current = selectedFile;
  }, [selectedFile]);

  useEffect(() => {
    if (sessionMode === 'ENDURANCE') {
      if (pitRule === 'NOT_APPLICABLE') setPitRule('CONCURRENT');
    } else {
      setPitRule('NOT_APPLICABLE');
    }
  }, [pitRule, sessionMode]);

  const loadFiles = useCallback(async () => {
    const payload = await requestJson('/api/files?limit=100');
    const nextFiles = payload.files || [];
    filesRef.current = nextFiles;
    setFiles(nextFiles);
    setFileIndex({ index_status: payload.index_status, pending_count: payload.pending_count, index_signature: payload.index_signature });
    const restored = nextFiles.find((file) => file.name === restoredFileRef.current && isSelectableRecording(file));
    const firstAnalyzable = restored || (!activeTrialRef.current && nextFiles.find(isSelectableRecording));
    if (!selectedFileRef.current && firstAnalyzable) {
      restoreFileDraft(firstAnalyzable.name);
      setSelectedFile(firstAnalyzable.name);
      selectedFileRef.current = firstAnalyzable.name;
    }
    return nextFiles;
  }, [restoreFileDraft]);

  const loadStatus = useCallback(async () => {
    const payload = await requestJson('/api/status');
    setStatus(payload);
    const telemetry = payload.telemetry;
    if (telemetry?.file_name && isSelectableRecording(telemetry) && !selectedFileRef.current && !restoredFileRef.current) {
      restoreFileDraft(telemetry.file_name);
      setSelectedFile(telemetry.file_name);
      selectedFileRef.current = telemetry.file_name;
    }
    return payload;
  }, [restoreFileDraft]);

  const loadMemory = useCallback(async () => {
    try {
      const [statusPayload, sessionsPayload] = await Promise.all([
        requestJson('/api/memory/status'),
        requestJson('/api/memory/sessions?limit=100'),
      ]);
      setMemoryStatus(statusPayload);
      setMemorySessions(sessionsPayload.sessions || []);
      return { status: statusPayload, sessions: sessionsPayload.sessions || [] };
    } catch (memoryError) {
      const fallback = {
        enabled: false,
        path: null,
        error: memoryError.message,
      };
      setMemoryStatus(fallback);
      return { status: fallback, sessions: [] };
    }
  }, []);

  const loadGarage61ApiStatus = useCallback(async () => {
    try {
      const payload = await requestJson('/api/garage61/api/status');
      setGarage61ApiStatus(payload.api || null);
      return payload.api || null;
    } catch (apiError) {
      const fallback = { connected: false, configured: false, message: apiError.message };
      setGarage61ApiStatus(fallback);
      return fallback;
    }
  }, []);

  const loadExternalReferences = useCallback(async (filename = null, autoSelect = true) => {
    const effectiveFilename = filename || selectedFileRef.current || '';
    if (!effectiveFilename) {
      const empty = { files: [], recommended_selection: [], total_compatible: 0 };
      setExternalReferenceStatus(empty);
      setExternalReferences([]);
      if (autoSelect) setSelectedExternalReferences([]);
      return empty;
    }
    try {
      const pinned = activeTrialRef.current?.reference_files || (autoSelect ? readDraft(effectiveFilename).referenceFiles : selectedExternalRef.current) || [];
      const retainedQuery = pinned.map(name => `&retained_files=${encodeURIComponent(name)}`).join('');
      const payload = await requestJson(`/api/external-references?filename=${encodeURIComponent(effectiveFilename)}${retainedQuery}`);
      if (selectedFileRef.current !== effectiveFilename) return payload;
      const nextFiles = payload.files || [];
      let appliedSelection;
      setExternalReferenceStatus(payload);
      setExternalReferences(nextFiles);
      if (payload.collector_health) {
        setGarage61Collector((current) => ({
          ...(current || {}),
          health: payload.collector_health,
          job: current?.job || null,
        }));
      }
      if (activeTrialRef.current) {
        appliedSelection = activeTrialRef.current.reference_files;
        setSelectedExternalReferences(activeTrialRef.current.reference_files);
        const nextRecommended = (payload.recommended_selection || []).slice(0, payload.display_limit || payload.max_selection || 9);
        if (JSON.stringify(nextRecommended) !== JSON.stringify(activeTrialRef.current.reference_files)) setPendingReferences(nextRecommended);
      } else if (payload.api_token_configured === false) {
        appliedSelection = [];
        setSelectedExternalReferences([]);
      } else if (autoSelect) {
        const draftReferences = readDraft(effectiveFilename).referenceFiles;
        const available = new Set(nextFiles.filter((item) => item.usable !== false).map((item) => item.name));
        const missingPrimary = draftReferences?.length && !available.has(draftReferences[0]);
        const restoredReferences = Array.isArray(draftReferences) ? draftReferences.filter((name) => available.has(name)) : null;
        appliedSelection = (missingPrimary ? [] : restoredReferences?.length ? restoredReferences : payload.recommended_selection || []).slice(0, payload.max_selection || 12);
        setSelectedExternalReferences(appliedSelection);
      } else {
        const available = new Set(nextFiles.filter((item) => item.usable !== false).map((item) => item.name));
        setSelectedExternalReferences((current) => available.has(current[0]) ? current.filter((name) => available.has(name)) : []);
      }
      return { ...payload, applied_selection: appliedSelection };
    } catch (referenceError) {
      const fallback = {
        directory: null,
        files: [],
        recommended_selection: [],
        total_compatible: 0,
        error: referenceError.message,
        max_selection: 12,
        recommended_cohort_size: 5,
        minimum_for_setup_conclusion: 2,
        strong_consensus_count: 3,
      };
      if (selectedFileRef.current !== effectiveFilename) return fallback;
      setExternalReferenceStatus(fallback);
      setExternalReferences([]);
      if (autoSelect && !activeTrialRef.current) setSelectedExternalReferences([]);
      return fallback;
    }
  }, [readDraft]);

  const loadGarage61CollectorStatus = useCallback(async (filename = null) => {
    const effectiveFilename = filename || selectedFileRef.current || '';
    if (!effectiveFilename) {
      const empty = { health: null, job: null };
      setGarage61Collector(empty);
      return empty;
    }
    try {
      const payload = await requestJson(`/api/garage61/collector/status?filename=${encodeURIComponent(effectiveFilename)}`);
      if (selectedFileRef.current !== effectiveFilename) return payload;
      setGarage61Collector(payload);
      return payload;
    } catch (collectorError) {
      const fallback = {
        health: null,
        job: { state: 'FAILED', message: collectorError.message, busy: false },
      };
      if (selectedFileRef.current !== effectiveFilename) return fallback;
      setGarage61Collector(fallback);
      return fallback;
    }
  }, []);

  const startGarage61Collection = useCallback(async (filename = null, force = false) => {
    if (!workspaceMounted.current) return null;
    const effectiveFilename = filename || selectedFileRef.current || '';
    if (!effectiveFilename) return null;
    setGarage61CollectorLoading(true);
    try {
      const payload = await requestJson('/api/garage61/collector/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: effectiveFilename,
          force: Boolean(force),
          target_count: 8,
          headless: false,
          collector_mode: 'AUTO',
        }),
      });
      setGarage61Collector(payload);
      return payload;
    } catch (collectorError) {
      setGarage61Collector((current) => ({
        ...(current || {}),
        health: current?.health || null,
        job: { state: 'FAILED', message: collectorError.message, busy: false },
      }));
      return null;
    } finally {
      setGarage61CollectorLoading(false);
    }
  }, []);

  const cancelGarage61Collection = useCallback(async () => {
    try {
      const payload = await requestJson('/api/garage61/collector/cancel', { method: 'POST' });
      setGarage61Collector((current) => ({
        ...(current || {}),
        health: current?.health || null,
        job: payload.job || null,
      }));
      return payload;
    } catch (collectorError) {
      setError(collectorError.message);
      return null;
    }
  }, []);

  const maybeStartGarage61Collection = useCallback(async (filename, referencePayload = null) => {
    if (!filename || !workspaceMounted.current || selectedFileRef.current !== filename) return null;
    if (!isSelectableRecording(filesRef.current.find(file => file.name === filename))) return null;
    const health = referencePayload?.collector_health || (await loadGarage61CollectorStatus(filename))?.health;
    if (!workspaceMounted.current || selectedFileRef.current !== filename) return null;
    if (!health?.enabled || !health?.needs_collection || health?.collection_policy?.api_token_configured === false) return null;
    const contextKey = health?.context?.context_key || `${filename}:unknown`;
    if (collectorAutoContextRef.current === contextKey) return null;
    collectorAutoContextRef.current = contextKey;
    const result = await startGarage61Collection(filename, false);
    const state = result?.job?.state;
    const returnedContext = result?.job?.context_key;
    if (state === 'FAILED' || (result?.job?.busy && returnedContext && returnedContext !== contextKey)) {
      collectorAutoContextRef.current = '';
    }
    return result;
  }, [loadGarage61CollectorStatus, startGarage61Collection]);

  const runAnalysis = useCallback(
    async ({
      quiet = false,
      filename = null,
      sessionModeOverride = null,
      pitRuleOverride = null,
      persistMemory = null,
      replaceMemoryMode = false,
      externalReferencesOverride = null,
      referenceModeOverride = null,
      forceAutoLaps = false,
      targetLapOverride = undefined,
      referenceLapOverride = undefined,
      referenceFilenameOverride = undefined,
      referenceSelectionOverride = undefined,
      onFailure = null,
    } = {}) => {
      const effectiveFilename = filename || selectedFileRef.current || null;
      if (!effectiveFilename || effectiveFilename !== selectedFileRef.current) return null;
      const knownFile = filesRef.current.find((item) => item.name === effectiveFilename);
      if (knownFile?.analyzable === false || (forceAutoLaps && !isSelectableRecording(knownFile))) {
        const unavailable = new Error(knownFile.eligibility_reason || knownFile.reason || '이 파일은 분석할 유효랩이 없습니다.');
        unavailable.status = 422;
        setError(unavailable.message); onFailure?.(unavailable); return null;
      }
      analysisRequestRef.current.controller?.abort();
      const requestId = analysisRequestRef.current.id + 1;
      const controller = new AbortController();
      analysisRequestRef.current = { id: requestId, controller };
      const isCurrent = () => !controller.signal.aborted && analysisRequestRef.current.id === requestId && selectedFileRef.current === effectiveFilename;
      if (!quiet) setLoading(true);
      setError('');
      const progressId = beginProgress();
      try {
        const fileDraft = forceAutoLaps && filename ? readDraft(effectiveFilename) : null;
        const effectiveFeedback = fileDraft ? {
          feedbackPhase: fileDraft.feedbackPhase || '', feedbackSpeed: fileDraft.feedbackSpeed || '',
          feedbackSymptom: fileDraft.feedbackSymptom || '', setupMode: fileDraft.setupMode || 'EXPLORE', userFeedback: fileDraft.userFeedback || '',
        } : { feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, userFeedback };
        const effectiveSessionMode = sessionModeOverride || sessionMode;
        const effectivePitRule = effectiveSessionMode === 'ENDURANCE'
          ? (pitRuleOverride || pitRule || 'CONCURRENT')
          : 'NOT_APPLICABLE';
        const requestedReferences = externalReferencesOverride === null
          ? selectedExternalReferences
          : externalReferencesOverride;
        const chosenReferenceMode = activeTrialRef.current?.reference_mode || referenceModeOverride || fileDraft?.referenceMode || referenceModeRef.current;
        const effectiveMode = chosenReferenceMode !== 'LOCAL' && requestedReferences.length ? chosenReferenceMode : 'LOCAL';
        const effectiveExternalReferences = effectiveMode === 'LOCAL' ? [] : effectiveMode === 'SINGLE' ? requestedReferences.slice(0, 1) : requestedReferences;
        if (chosenReferenceMode !== effectiveMode) setReferenceMode(effectiveMode);
        const fileEntry = filesRef.current.find((item) => item.name === effectiveFilename);
        const storedModes = storedModesForFile(fileEntry);
        const shouldPersist = persistMemory === null
          ? storedModes.includes(effectiveSessionMode)
          : Boolean(persistMemory);
        const requestedTargetLap = targetLapOverride !== undefined ? targetLapOverride : forceAutoLaps
          ? null
          : (targetLapManual && targetLap ? Number(targetLap) : null);
        const requestedReferenceLap = referenceLapOverride !== undefined ? referenceLapOverride : forceAutoLaps
          ? null
          : (referenceLapManual && referenceLap ? Number(referenceLap) : null);
        const referenceFilename = referenceFilenameOverride ?? (!forceAutoLaps && analysisSnapshot?.filename === effectiveFilename ? analysisSnapshot.referenceFilename : undefined);
        const referenceSelection = referenceSelectionOverride ?? (!forceAutoLaps && analysisSnapshot?.filename === effectiveFilename ? analysisSnapshot.referenceSelection : undefined);
        const snapshot = { filename: effectiveFilename, ...effectiveFeedback,
          referenceMode: effectiveMode, references: effectiveExternalReferences, targetLap: requestedTargetLap, referenceLap: requestedReferenceLap,
          ...(referenceFilename ? { referenceFilename } : {}),
          ...(referenceSelection ? { referenceSelection } : {}) };

        const payload = await requestJson('/api/analyze', {
          signal: controller.signal,
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            progress_id: progressId,
            session_mode: effectiveSessionMode,
            pit_rule: effectivePitRule,
            user_feedback: effectiveFeedback.userFeedback,
            feedback_phase: effectiveFeedback.feedbackPhase || null,
            feedback_speed: effectiveFeedback.feedbackSpeed || null,
            feedback_symptom: effectiveFeedback.feedbackSymptom || null,
            setup_mode: effectiveFeedback.setupMode,
            target_lap: requestedTargetLap,
            reference_lap: referenceFilename && referenceFilename !== effectiveFilename ? null : requestedReferenceLap,
            filename: effectiveFilename,
            persist_memory: shouldPersist,
            replace_existing_mode: Boolean(replaceMemoryMode),
            external_reference_files: effectiveExternalReferences,
          }),
        });
        if (!isCurrent()) return null;
        if (forceAutoLaps && !isSelectableRecording(payload.data_quality)) {
          await loadFiles();
          const unavailable = new Error('유효 랩 또는 측정 가능한 완주 랩이 없어 자동 불러오기에서 제외했습니다.');
          unavailable.status = 422;
          throw unavailable;
        }
        if (payload.data_quality?.analysis_scope === 'UNAVAILABLE') {
          const unavailable = new Error('이 주행에는 유효한 완주 랩이나 분석 가능한 정상 구간이 없습니다.');
          unavailable.status = 422;
          throw unavailable;
        }
        setAnalysis(payload);
        setAnalysisSnapshot(snapshot);
        setDetail(null);
        detailRequestRef.current.controller?.abort();

        const validLaps = (payload.laps || []).filter((lap) => lap.status === 'VALID');
        const comparison = payload.comparison;
        if (comparison) {
          setTargetLap(String(comparison.target_lap));
          if (comparison.reference_lap !== null && comparison.reference_lap !== undefined) {
            setReferenceLap(String(comparison.reference_lap));
          } else {
            setReferenceLap('');
          }
        } else if (validLaps.length) {
          const fastestValid = [...validLaps].sort((a, b) => Number(a.lap_time_s || Infinity) - Number(b.lap_time_s || Infinity))[0];
          if (fastestValid) setTargetLap(String(fastestValid.lap_number));
        }

        await loadFiles();
        await loadMemory();
        return payload;
      } catch (requestError) {
        if (isCurrent() && requestError.name !== 'AbortError') { setError(requestError.message); onFailure?.(requestError); }
        return null;
      } finally {
        endProgress(progressId);
        if (isCurrent()) setLoading(false);
      }
    },
    [analysisSnapshot, beginProgress, endProgress, feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, loadFiles, loadMemory, pitRule, readDraft, referenceLap, referenceLapManual, selectedExternalReferences, sessionMode, targetLap, targetLapManual, userFeedback]
  );

  const handleExperimentStored = useCallback(async () => {
    await loadMemory();
    await runAnalysis({ includeAI: false, quiet: true });
  }, [loadMemory, runAnalysis]);

  useWorkspaceInitialization({ initializationAttempt, loadExternalReferences, loadFiles, loadGarage61ApiStatus, loadMemory, loadStatus, maybeStartGarage61Collection, restoreFileDraft, runAnalysisRef, selectedFileRef, setError, setInitialLoading, setPitRule, setSelectedFile, setSessionMode, activeTrialRef, setTargetLapManual, setReferenceLapManual, signatureRef, waitingFileRef });

  useEffect(() => {
    runAnalysisRef.current = runAnalysis;
  }, [runAnalysis]);

  useTelemetryEvents({ initialLoading, activeTrialRef, analysisRequestRef, loadExternalReferences, loadFiles, loadStatus, maybeStartGarage61Collection, restoreFileDraft, runAnalysisRef, selectedFileRef, sessionMode, setAnalysis, setError, setLoading, setNewFileNotice, setPitRule, setReferenceLap, setReferenceLapManual, setSelectedFile, setSessionMode, setStatus, setTargetLap, setTargetLapManual, signatureRef, waitingFileRef });

  useEffect(() => {
    if (fileIndex?.index_status !== 'SCANNING') return undefined;
    let cancelled = false; let inFlight = false;
    const timer = window.setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const previous = selectedFileRef.current;
        await loadFiles();
        if (!cancelled && !previous && selectedFileRef.current) {
          const filename = selectedFileRef.current;
          const references = await loadExternalReferences(filename, true);
          await runAnalysisRef.current?.({ filename, persistMemory: false, forceAutoLaps: true,
            externalReferencesOverride: references.applied_selection || references.recommended_selection || [] });
        }
      } catch { /* Metadata polling continues while the index has work. */ }
      finally { inFlight = false; }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [fileIndex?.index_status, fileIndex?.pending_count, loadExternalReferences, loadFiles]);

  useCollectorPolling({ collectorAutoContextRef, collectorHandledJobRef, collectorTerminalRetryRef, garage61Collector, loadExternalReferences, loadGarage61CollectorStatus, maybeStartGarage61Collection, pitRule, selectedFileRef, sessionMode, setPendingReferences });

  const handleTargetLapChange = (value) => {
    setTargetLap(value);
    setTargetLapManual(true);
    if (referenceLap === value) {
      setReferenceLap('');
      setReferenceLapManual(false);
    }
  };

  const handleReferenceLapChange = (value) => {
    setReferenceLap(value);
    setReferenceLapManual(true);
    if (targetLap === value) {
      setTargetLap('');
      setTargetLapManual(false);
    }
  };

  const resetAutoLapSelection = async () => {
    setTargetLap('');
    setReferenceLap('');
    setTargetLapManual(false);
    setReferenceLapManual(false);
    await runAnalysis({ includeAI: false, forceAutoLaps: true });
  };

  const handleFileChange = async (filename) => {
    const fileEntry = files.find((item) => item.name === filename);
    if (fileEntry && !isSelectableRecording(fileEntry)) return;
    analysisRequestRef.current.controller?.abort();
    setAnalysis(null);
    setLoading(true);
    const nextMode = initialModeForFile(fileEntry);
    const nextPitRule = nextMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
    setModeChangePrompt(null);
    setSelectedFile(filename);
    selectedFileRef.current = filename;
    restoreFileDraft(filename);
    setNewFileNotice('');
    setSessionMode(nextMode);
    setPitRule(nextPitRule);
    setTargetLap('');
    setReferenceLap('');
    setTargetLapManual(false);
    setReferenceLapManual(false);
    const referencePayload = await loadExternalReferences(filename, true);
    if (selectedFileRef.current !== filename) return;
    const result = await runAnalysis({
      includeAI: false,
      filename,
      sessionModeOverride: nextMode,
      pitRuleOverride: nextPitRule,
      persistMemory: false,
      forceAutoLaps: true,
      externalReferencesOverride: (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9),
    });
    if (result) void maybeStartGarage61Collection(filename, referencePayload);
  };

  const commitSessionMode = async (nextMode, nextPitRule, replaceExistingMode = false) => {
    setSessionMode(nextMode);
    setPitRule(nextPitRule);
    setTargetLap('');
    setReferenceLap('');
    setTargetLapManual(false);
    setReferenceLapManual(false);
    if (selectedFileRef.current) {
      await runAnalysis({
        includeAI: false,
        sessionModeOverride: nextMode,
        pitRuleOverride: nextPitRule,
        persistMemory: true,
        replaceMemoryMode: replaceExistingMode,
        forceAutoLaps: true,
      });
    }
  };

  const handleSessionModeChange = async (nextMode) => {
    if (!selectedFileRef.current || loading) return;
    const fileEntry = files.find((item) => item.name === selectedFileRef.current);
    const currentModes = storedModesForFile(fileEntry);
    const nextPitRule = nextMode === 'ENDURANCE'
      ? (pitRule === 'NOT_APPLICABLE' ? 'CONCURRENT' : pitRule)
      : 'NOT_APPLICABLE';
    const requiresReplacement = currentModes.length > 1 || (
      currentModes.length === 1 && !currentModes.includes(nextMode)
    );

    if (requiresReplacement) {
      setModeChangePrompt({
        filename: selectedFileRef.current,
        currentModes,
        nextMode,
        nextPitRule,
        returnFocus: document.activeElement,
      });
      return;
    }

    await commitSessionMode(nextMode, nextPitRule, false);
  };

  const confirmSessionModeChange = async () => {
    const prompt = modeChangePrompt;
    if (!prompt) return;
    setModeChangePrompt(null);
    await commitSessionMode(prompt.nextMode, prompt.nextPitRule, true);
  };

  const toggleExternalReference = (filename) => {
    if (activeTrial) return;
    if (externalReferences.find((file) => file.name === filename)?.usable === false) return;
    const maxSelection = Number(externalReferenceStatus?.display_limit || externalReferenceStatus?.max_selection || 9);
    setSelectedExternalReferences((current) => {
      if (current.includes(filename)) return current.filter((name) => name !== filename);
      if (current.length >= maxSelection) return current;
      return [...current, filename];
    });
  };

  const refreshExternalReferences = async () => {
    const filename = selectedFileRef.current;
    const payload = await loadExternalReferences(filename, false);
    if (payload.error) {
      setError(payload.error);
      return payload;
    }
    const recommended = (payload.recommended_selection || []).slice(0, payload.display_limit || payload.max_selection || 9);
    if (filename && recommended.length) setPendingReferences(recommended);
    return payload;
  };

  const applyExternalReferences = async () => {
    await runAnalysis({
      includeAI: false,
      externalReferencesOverride: selectedExternalReferences,
    });
  };

  const session = analysis?.session;
  const quality = analysis?.data_quality || status?.telemetry?.data_quality;
  const comparison = analysis?.comparison;
  const classification = analysis?.performance?.classification;
  const externalReferenceAnalysis = analysis?.external_reference;
  const trace = analysis?.visualizations?.trace || [];
  const pathData = analysis?.visualizations?.track_path || [];
  const wantsExternalComparison = Boolean(analysisSnapshot && analysisSnapshot.referenceMode !== 'LOCAL');
  const hasExternalTrace = wantsExternalComparison && externalReferenceAnalysis?.status === 'READY';
  const comparisonTrace = wantsExternalComparison ? externalReferenceAnalysis?.visualizations?.trace || [] : trace;
  const comparisonCorners = wantsExternalComparison ? externalReferenceAnalysis?.corners || [] : analysis?.performance?.corners?.length ? analysis.performance.corners : analysis?.coaching?.clean_segment_corners || [];
  const comparisonPath = wantsExternalComparison && externalReferenceAnalysis?.visualizations?.track_path?.length ? externalReferenceAnalysis.visualizations.track_path : pathData;
  const selectedFileEntry = useMemo(
    () => files.find((item) => item.name === selectedFile),
    [files, selectedFile]
  );
  const selectedStoredModes = storedModesForFile(selectedFileEntry);
  const selectedAssignedMode = assignedModeForFile(selectedFileEntry);
  const cleanSectionOnly = Boolean(analysis && !quality?.valid_laps && quality?.coaching_ready && quality?.analysis_scope === 'CLEAN_SEGMENTS_ONLY');
  const desiredReferences = referenceMode === 'LOCAL' ? [] : referenceMode === 'SINGLE' ? selectedExternalReferences.slice(0, 1) : selectedExternalReferences;
  const desiredContext = { filename: selectedFile, feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, userFeedback,
    referenceMode, references: desiredReferences, targetLap: targetLapManual && targetLap ? Number(targetLap) : null,
    referenceLap: referenceLapManual && referenceLap ? Number(referenceLap) : null };
  const analysisIsCurrent = Boolean(analysisSnapshot && analysisContextKey(analysisSnapshot) === analysisContextKey(desiredContext));
  const canStartTrial = Boolean(analysis && analysisIsCurrent && !loading && !activeTrial);
  const currentContext = { ...(selectedFileEntry || {}), ...(session || {}), name: selectedFile };
  const canAttachTrial = Boolean(activeTrial && selectedFile !== activeTrial.baseline_file && isSelectableRecording(selectedFileEntry)
    && analysis && sameVehicleAndTrack(activeTrial.context, currentContext));
  const displayComparison = cleanSectionOnly ? null : hasExternalTrace ? { target_lap: comparison?.target_lap ?? analysis?.lap_selection?.target_lap,
    reference_lap: externalReferenceAnalysis.aggregate?.reference_label || '선택한 Garage61 기준',
    target_time: externalReferenceAnalysis.aggregate?.target_time, reference_time: externalReferenceAnalysis.aggregate?.reference_time ?? externalReferenceAnalysis.aggregate?.reference_median_time,
    total_delta_s: externalReferenceAnalysis.aggregate?.gap_to_reference_s ?? externalReferenceAnalysis.aggregate?.gap_to_reference_median_s } : wantsExternalComparison ? null : comparison;
  const displayPriorities = wantsExternalComparison ? (externalReferenceAnalysis?.top_losses || []).slice(0, 3).map((corner, index) => ({
    rank: index + 1, corner: corner.label || corner.id, issue_type: corner.issue_type, distance_m: corner.distance_m, loss_s: corner.delta_s, action: corner.coaching_note || corner.coaching || '선택한 기준과 코너 입력을 비교하세요.',
    estimated_recovery_s: null, evidence: [`선택 기준 대비 코너 손실 ${formatSigned(corner.delta_s)}`],
  })) : analysis?.coaching?.priorities || [];

  const beginTrial = (adjustment) => {
    if (!canStartTrial || !hasSetupNumericValue(adjustment.original_value) || !hasSetupNumericValue(adjustment.value)
      || Number(adjustment.original_value) === Number(adjustment.value)) return;
    startTrial({ id: `trial-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, started_at: new Date().toISOString(),
      baseline_file: selectedFile, candidate_file: null, adjustment, original_value: Number(adjustment.original_value),
      target_value: Number(adjustment.value), original_text: formatSetupOriginal(adjustment), target_text: formatSetupTarget(adjustment),
      goal, session_mode: sessionMode, reference_mode: analysisSnapshot.referenceMode, reference_files: [...analysisSnapshot.references],
      target_lap: comparison?.target_lap || null, reference_lap: comparison?.reference_lap || null,
      feedback: { phase: feedbackPhase, speed: feedbackSpeed, symptom: feedbackSymptom, text: userFeedback },
      context: { name: selectedFile, car_name: currentContext.car_name, car_path: currentContext.car_path,
        track_name: currentContext.track_name, track_path: currentContext.track_path, track_config: currentContext.track_config },
      conditions: { track_temp_c: session?.track_temp_c, air_temp_c: session?.air_temp_c, weather: session?.weather,
        environment: session?.environment || null, setup: session?.setup || null },
      setup_snapshot: session?.setup || null,
      driver_feedback: { change: '', rating: null, side_effects: '' } });
    setActiveTab('setup');
  };

  const detailContextRef = useRef(null);
  detailContextRef.current = analysisSnapshot;
  const loadComparisonDetail = useCallback(async (range) => {
    detailRequestRef.current.controller?.abort();
    const id = detailRequestRef.current.id + 1;
    const controller = new AbortController(); detailRequestRef.current = { id, controller };
    if (!range || !analysisSnapshot) { setDetail(null); setDetailLoading(false); return; }
    setDetail(null);
    setDetailLoading(true);
    const snapshotKey = analysisContextKey(analysisSnapshot);
    const isCurrent = () => !controller.signal.aborted && selectedFileRef.current === analysisSnapshot.filename && detailRequestRef.current.id === id && detailContextRef.current && analysisContextKey(detailContextRef.current) === snapshotKey;
    try {
      const payload = await requestJson('/api/comparison-detail', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ filename: analysisSnapshot.filename, target_lap: comparison?.target_lap || null,
          reference_lap: comparison?.reference_lap || null, external_reference_files: analysisSnapshot.references,
          reference_mode: analysisSnapshot.referenceMode, start_pct: Array.isArray(range) ? range[0] : range.start_pct, end_pct: Array.isArray(range) ? range[1] : range.end_pct, points: 2400 }) });
      if (isCurrent()) setDetail(payload);
    } catch (requestError) { if (isCurrent() && requestError.name !== 'AbortError') setError(requestError.message); }
    finally { if (isCurrent()) setDetailLoading(false); }
  }, [analysisSnapshot, comparison?.target_lap, comparison?.reference_lap]);

  // Dense trace of one corner window for the annotated corner graph (CornerTrace). The practice card
  // reads the local corners, so it always asks for the local reference; the corner table follows the
  // reference the table is showing.
  const cornerWindowLoader = useCallback((mode) => (start, end) => requestJson('/api/comparison-detail', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: analysisSnapshot?.filename, target_lap: comparison?.target_lap || null,
      reference_lap: comparison?.reference_lap || null, external_reference_files: mode === 'LOCAL' ? [] : analysisSnapshot?.references || [],
      reference_mode: mode, start_pct: start, end_pct: end, points: 800 }),
  }).then((payload) => payload?.trace || []), [analysisSnapshot, comparison?.target_lap, comparison?.reference_lap]);
  const localCornerLoader = useMemo(() => (analysisSnapshot ? cornerWindowLoader('LOCAL') : null), [analysisSnapshot, cornerWindowLoader]);
  const overlayLoader = useCallback((body) => requestJson('/api/lap-overlay', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: analysisSnapshot?.filename, ...body }),
  }), [analysisSnapshot]);
  // Expert view: other drive files of the same track and layout, usable Garage61 laps, and a file's lap list.
  const overlayFiles = useMemo(() => {
    const current = files.find((item) => item.name === analysisSnapshot?.filename);
    if (!current) return [];
    const here = comparisonContext(current);
    const stamp = (name) => { const found = /(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})/.exec(name); return found ? `${found[1]}-${found[2]}-${found[3]} ${found[4]}:${found[5]}` : name; };
    return files.filter((item) => item.name !== current.name && isSelectableRecording(item)
      && comparisonContext(item).track === here.track && comparisonContext(item).config === here.config)
      .map((item) => ({ name: item.name, label: `${stamp(item.name)} · ${item.car_name || ''}` }));
  }, [files, analysisSnapshot?.filename]);
  const overlayReferences = useMemo(() => externalReferences.filter((item) => item.usable !== false && item.compatible !== false && item.name)
    .map((item) => ({ name: item.name, label: `${item.driver_name || '드라이버'} · ${item.lap_time || ''}${item.car_name ? ` · ${item.car_name}` : ''}` })), [externalReferences]);
  const loadFileLaps = useCallback((filename) => requestJson(`/api/file-laps?filename=${encodeURIComponent(filename)}`), []);
  // Garage61 laps the practice card can ask for: the ones chosen on screen, else the recommended ones.
  const practiceGarage61Refs = selectedExternalReferences.length ? selectedExternalReferences
    : (externalReferenceStatus?.recommended_selection || []).slice(0, externalReferenceStatus?.display_limit || externalReferenceStatus?.max_selection || 9);
  // Garage61 fast laps for the practice card: the mode on screen, or their composite when the screen shows the local lap.
  const externalCornerLoader = useMemo(() => (analysisSnapshot?.references?.length
    ? cornerWindowLoader(analysisSnapshot.referenceMode && analysisSnapshot.referenceMode !== 'LOCAL' ? analysisSnapshot.referenceMode : 'AGGREGATE')
    : null), [analysisSnapshot, cornerWindowLoader]);
  const tableCornerLoader = useMemo(() => (analysisSnapshot ? cornerWindowLoader(analysisSnapshot.referenceMode || 'LOCAL') : null), [analysisSnapshot, cornerWindowLoader]);


  // Accept a calculated workspace selection while retaining the existing SSE,
  // collector, experiment and browser preference lifecycle.
  const adoptDesktopAnalysis = (sessionData, result) => {
    const payload = result.analysisResult || sessionData.analysisResult;
    if (!payload) return;
    const filename = sessionData.filename;
    const changedFile = filename !== selectedFileRef.current;
    const nextDraft = changedFile ? readDraft(filename) : null;
    const nextFeedback = nextDraft ? { feedbackPhase: nextDraft.feedbackPhase || '', feedbackSpeed: nextDraft.feedbackSpeed || '',
      feedbackSymptom: nextDraft.feedbackSymptom || '', setupMode: nextDraft.setupMode || 'EXPLORE', userFeedback: nextDraft.userFeedback || '' }
      : { feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, userFeedback };
    analysisRequestRef.current.controller?.abort();
    if (changedFile) restoreFileDraft(filename);
    selectedFileRef.current = filename;
    setSelectedFile(filename);
    const refs = sessionData.external || [];
    const mode = refs.length > 1 ? 'AGGREGATE' : refs.length ? 'SINGLE' : 'LOCAL';
    setSelectedExternalReferences(refs); setReferenceMode(mode);
    setTargetLap(String(sessionData.defaultTarget));
    setReferenceLap(sessionData.defaultReference == null ? '' : String(sessionData.defaultReference));
    setTargetLapManual(true); setReferenceLapManual(sessionData.reference_selection !== 'AUTO');
    setSessionMode(payload.session_policy?.session_mode || initialModeForFile(sessionData.session));
    setAnalysis(payload); setDetail(null); setLoading(false); setError(''); setNewFileNotice('');
    setAnalysisSnapshot({ filename, ...nextFeedback,
      referenceMode: mode, references: refs, targetLap: sessionData.defaultTarget, referenceLap: sessionData.defaultReference,
      referenceFilename: sessionData.reference_filename, referenceSelection: sessionData.reference_selection });
    // Keep the accepted comparison visible while the normal collection policy
    // checks whether this recording needs more matching references.
    void loadExternalReferences(filename, false).then(referencePayload => maybeStartGarage61Collection(filename, referencePayload));
  };

    return {
      analysisProgress, retryInitialization,
      adoptDesktopAnalysis,
      activeTab, activeTrial, analysis, analysisIsCurrent, analysisSnapshot, applyExternalReferences,
      beginTrial, canAttachTrial, canStartTrial, cancelGarage61Collection, classification,
      cleanSectionOnly, comparison, comparisonCorners, comparisonPath, comparisonTrace, completeTrial,
      confirmSessionModeChange, controlsExpanded, currentContext, detail, detailLoading,
      displayComparison, displayPriorities, error, externalCornerLoader, externalReferenceAnalysis,
      externalReferenceStatus, externalReferences, feedbackPhase, feedbackSpeed, feedbackSymptom,
      fileIndex, files, garage61ApiStatus, garage61Collector, garage61CollectorLoading, goal,
      handleExperimentStored, handleFileChange, handleReferenceLapChange, handleSessionModeChange,
      handleTargetLapChange, hasExternalTrace, hoverHelpEnabled, initialLoading, lapRange,
      loadComparisonDetail, loadFileLaps, loadMemory, loading, localCornerLoader, memorySessions,
      memoryStatus, modeChangePrompt, navigateToSection, newFileNotice, overlayFiles, overlayLoader,
      overlayReferences, patchTrial, pendingReferences, pickedLaps, pitRule, practiceGarage61Refs,
      quality, referenceLap, referenceLapManual, referenceMode, refreshExternalReferences,
      requestedCorner, resetAutoLapSelection, runAnalysis, selectTab, selectedAssignedMode,
      selectedExternalReferences, selectedFile, selectedFileEntry, selectedFileRef, selectedStoredModes,
      session, sessionMode, setControlsExpanded, setError, setFeedbackPhase, setFeedbackSpeed,
      setFeedbackSymptom, setGoal, setLapRange, setModeChangePrompt, setPendingReferences, setPitRule,
      setReferenceLapManual, setReferenceMode, setSelectedExternalReferences, setSetupMode,
      setTargetLapManual, setUserFeedback, setupMode, startGarage61Collection, status, tableCornerLoader,
      targetLap, targetLapManual, toggleExternalReference, toggleHoverHelp, trialHistory, userFeedback,
      wantsExternalComparison, workflow
    };
}
