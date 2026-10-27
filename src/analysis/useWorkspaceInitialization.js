import { useEffect } from "react";
import { initialModeForFile } from './analysisShared';
import { readDesktopLaps } from './desktopSelection';
import { isSelectableRecording } from '../lib/recordingFiles';

export function useWorkspaceInitialization({
  initializationAttempt = 0,
  loadExternalReferences, loadFiles, loadGarage61ApiStatus, loadMemory, loadStatus,
  maybeStartGarage61Collection, restoreFileDraft, runAnalysisRef, selectedFileRef, setError,
  setInitialLoading, setPitRule, setSelectedFile, setSessionMode, activeTrialRef, setTargetLapManual, setReferenceLapManual,
  signatureRef, waitingFileRef
}) {
  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      setInitialLoading(true);
      try {
        const [filePayload, statusPayload] = await Promise.all([loadFiles(), loadStatus()]);
        if (cancelled) return;
        const telemetry = statusPayload.telemetry;
        // SSE sends the latest file on each subscription. Remember the startup
        // snapshot so it cannot replace the restored file as if it were new.
        // Seed before analysis so files changed during it are still detected.
        signatureRef.current = telemetry ? JSON.stringify(telemetry.file_signature || []) : null;
        waitingFileRef.current = telemetry?.retryable ? telemetry.file_name : '';
        void loadMemory();
        void loadGarage61ApiStatus();
        if (cancelled) return;
        const selected = selectedFileRef.current;
        const available = filePayload.filter(isSelectableRecording).map(file => file.name);
        const candidates = [...new Set([
          ...(selected && !filePayload.some(file => file.name === selected && !isSelectableRecording(file)) ? [selected] : []),
          ...available,
          ...(telemetry?.file_name && isSelectableRecording(telemetry) ? [telemetry.file_name] : []),
        ])];
        if (activeTrialRef.current) candidates.splice(selected ? 1 : 0);
        let ownedFile = selected;
        for (const firstFile of candidates) {
          if (cancelled || selectedFileRef.current !== ownedFile) return;
          const firstEntry = filePayload.find((item) => item.name === firstFile);
          selectedFileRef.current = firstFile; ownedFile = firstFile;
          setSelectedFile(firstFile); restoreFileDraft(firstFile);
          const initialMode = initialModeForFile(firstEntry);
          const initialPitRule = initialMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
          setSessionMode(initialMode);
          setPitRule(initialPitRule);
          const referencePayload = await loadExternalReferences(firstFile, true);
          if (cancelled || selectedFileRef.current !== ownedFile) return;
          const references = (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9);
          const savedLaps = activeTrialRef?.current ? null : readDesktopLaps(firstFile, references);
          const options = {
            includeAI: false,
            quiet: true,
            filename: firstFile,
            sessionModeOverride: initialMode,
            pitRuleOverride: initialPitRule,
            persistMemory: false,
            forceAutoLaps: true,
            externalReferencesOverride: references,
          };
          let failure = null;
          let restoredLaps = Boolean(savedLaps);
          const onFailure = error => { failure = error; };
          let result = await runAnalysisRef.current?.({ ...options, ...savedLaps, onFailure });
          if (cancelled || selectedFileRef.current !== ownedFile) return;
          if (savedLaps && !result && [400, 404, 409, 422].includes(failure?.status)) {
            failure = null;
            restoredLaps = false;
            result = await runAnalysisRef.current?.({ ...options, onFailure });
          }
          if (cancelled || selectedFileRef.current !== ownedFile) return;
          if (result) {
            setTargetLapManual?.(restoredLaps);
            setReferenceLapManual?.(restoredLaps);
            void maybeStartGarage61Collection(firstFile, referencePayload);
            return;
          }
          // Retry older recordings only for file/lap availability failures.
          // Network/server failures must not scan every large IBT in the folder.
          if (![400, 404, 409, 422].includes(failure?.status)) return;
        }
        if (!candidates.length) setError(telemetry?.reason || filePayload[0]?.reason || '');
      } catch (initializeError) {
        if (!cancelled) setError(initializeError.message);
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    };
    initialize();
    return () => {
      cancelled = true;
    };
  }, [initializationAttempt, loadExternalReferences, loadFiles, loadGarage61ApiStatus, loadMemory, loadStatus, maybeStartGarage61Collection, restoreFileDraft,
    runAnalysisRef, selectedFileRef, setError, setInitialLoading, setPitRule, setSelectedFile, setSessionMode,
    activeTrialRef, setTargetLapManual, setReferenceLapManual, signatureRef, waitingFileRef]);
}
