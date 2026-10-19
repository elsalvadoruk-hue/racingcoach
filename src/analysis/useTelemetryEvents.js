import { useEffect } from "react";
import { initialModeForFile } from './analysisShared';
import { API_BASE } from './analysisClient';

export function useTelemetryEvents({
  initialLoading, activeTrialRef, analysisRequestRef, loadExternalReferences, loadFiles, loadStatus,
  maybeStartGarage61Collection, restoreFileDraft, runAnalysisRef, selectedFileRef, sessionMode,
  setAnalysis, setError, setLoading, setNewFileNotice, setPitRule, setReferenceLap,
  setReferenceLapManual, setSelectedFile, setSessionMode, setStatus, setTargetLap, setTargetLapManual,
  signatureRef, waitingFileRef
}) {
  useEffect(() => {
    let eventSource = null;
    let polling = null;
    let cancelled = false;
    let eventGeneration = 0;

    const handleStatus = async (payload) => {
      const telemetry = payload?.telemetry;
      if (!telemetry) return;
      setStatus(payload);
      // Initialization owns restoration. A fresh SSE subscription announces
      // the latest file again after that restoration has finished.
      if (initialLoading) return;
      if (telemetry.retryable) {
        waitingFileRef.current = telemetry.file_name;
        await loadFiles();
        return;
      }
      const unlocked = waitingFileRef.current === telemetry.file_name;
      waitingFileRef.current = '';
      const signature = JSON.stringify(telemetry.file_signature || []);
      if (unlocked || (signatureRef.current ? signature !== signatureRef.current : telemetry.file_name !== selectedFileRef.current)) {
        signatureRef.current = signature;
        const generation = ++eventGeneration;
        if (activeTrialRef.current) {
          setNewFileNotice(telemetry.file_name);
          await loadFiles();
          return;
        }
        const previousFile = selectedFileRef.current;
        const refreshedFiles = await loadFiles();
        const fileEntry = refreshedFiles.find((item) => item.name === telemetry.file_name);
        const selectedByRefresh = !previousFile && selectedFileRef.current === telemetry.file_name;
        if (cancelled || generation !== eventGeneration || (selectedFileRef.current !== previousFile && !selectedByRefresh)) return;
        // An empty/out/locked recording must never erase the last usable run.
        if (fileEntry?.analyzable === false || telemetry.analyzable === false) return;
        analysisRequestRef.current.controller?.abort();
        setLoading(true);
        setSelectedFile(telemetry.file_name);
        selectedFileRef.current = telemetry.file_name;
        restoreFileDraft(telemetry.file_name);
        const nextMode = initialModeForFile(fileEntry);
        const nextPitRule = nextMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
        const referencePayload = await loadExternalReferences(telemetry.file_name, true);
        if (cancelled || generation !== eventGeneration || selectedFileRef.current !== telemetry.file_name) return;
        const runner = runAnalysisRef.current;
        if (runner) {
          const result = await runner({
            includeAI: false,
            quiet: true,
            filename: telemetry.file_name,
            sessionModeOverride: nextMode,
            pitRuleOverride: nextPitRule,
            persistMemory: false,
            forceAutoLaps: true,
            externalReferencesOverride: (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9),
          });
          if (cancelled || generation !== eventGeneration || selectedFileRef.current !== telemetry.file_name) return;
          if (!result) {
            selectedFileRef.current = previousFile;
            setSelectedFile(previousFile);
            restoreFileDraft(previousFile);
            return;
          }
          setSessionMode(nextMode);
          setPitRule(nextPitRule);
          setTargetLapManual(false);
          setReferenceLapManual(false);
        }
        void maybeStartGarage61Collection(telemetry.file_name, referencePayload);
      } else if (!signatureRef.current) {
        signatureRef.current = signature;
      }
    };

    try {
      eventSource = new EventSource(`${API_BASE}/api/events`);
      eventSource.onmessage = (event) => {
        try {
          void handleStatus(JSON.parse(event.data)).catch(() => {});
        } catch {
          // Ignore malformed keepalive/event payloads.
        }
      };
      eventSource.onerror = () => {
        eventSource?.close();
        if (!polling) {
          polling = window.setInterval(async () => {
            try {
              const payload = await loadStatus();
              await handleStatus(payload);
            } catch {
              // Keep the last valid UI state during a temporary backend outage.
            }
          }, 10000);
        }
      };
    } catch {
      polling = window.setInterval(loadStatus, 10000);
    }

    return () => {
      cancelled = true;
      eventSource?.close();
      if (polling) window.clearInterval(polling);
    };
  }, [initialLoading, loadExternalReferences, loadFiles, loadStatus, maybeStartGarage61Collection, restoreFileDraft, sessionMode,
    activeTrialRef, analysisRequestRef, runAnalysisRef, selectedFileRef, setAnalysis, setError, setLoading,
    setNewFileNotice, setPitRule, setReferenceLap, setReferenceLapManual, setSelectedFile, setSessionMode,
    setStatus, setTargetLap, setTargetLapManual, signatureRef, waitingFileRef]);
}
