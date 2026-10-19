import { useEffect } from "react";
import { initialModeForFile } from './analysisShared';
import { API_BASE } from './analysisClient';

export function useTelemetryEvents({
  activeTrialRef, analysisRequestRef, loadExternalReferences, loadFiles, loadStatus,
  maybeStartGarage61Collection, restoreFileDraft, runAnalysisRef, selectedFileRef, sessionMode,
  setAnalysis, setError, setLoading, setNewFileNotice, setPitRule, setReferenceLap,
  setReferenceLapManual, setSelectedFile, setSessionMode, setStatus, setTargetLap, setTargetLapManual,
  signatureRef, waitingFileRef
}) {
  useEffect(() => {
    let eventSource = null;
    let polling = null;

    const handleStatus = async (payload) => {
      const telemetry = payload?.telemetry;
      if (!telemetry) return;
      setStatus(payload);
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
        if (activeTrialRef.current) {
          setNewFileNotice(telemetry.file_name);
          await loadFiles();
          return;
        }
        analysisRequestRef.current.controller?.abort();
        setAnalysis(null);
        setLoading(true);
        setSelectedFile(telemetry.file_name);
        selectedFileRef.current = telemetry.file_name;
        restoreFileDraft(telemetry.file_name);
        const refreshedFiles = await loadFiles();
        const fileEntry = refreshedFiles.find((item) => item.name === telemetry.file_name);
        if (selectedFileRef.current !== telemetry.file_name) return;
        if (fileEntry?.analyzable === false) { setLoading(false); setError(fileEntry.reason || '새 주행의 유효랩이 부족합니다.'); return; }
        const nextMode = initialModeForFile(fileEntry);
        const nextPitRule = nextMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
        setSessionMode(nextMode);
        setPitRule(nextPitRule);
        setTargetLap('');
        setReferenceLap('');
        setTargetLapManual(false);
        setReferenceLapManual(false);
        const referencePayload = await loadExternalReferences(telemetry.file_name, true);
        if (selectedFileRef.current !== telemetry.file_name) return;
        const runner = runAnalysisRef.current;
        if (runner) {
          await runner({
            includeAI: false,
            quiet: true,
            filename: telemetry.file_name,
            sessionModeOverride: nextMode,
            pitRuleOverride: nextPitRule,
            persistMemory: false,
            forceAutoLaps: true,
            externalReferencesOverride: (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9),
          });
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
      eventSource?.close();
      if (polling) window.clearInterval(polling);
    };
  }, [loadExternalReferences, loadFiles, loadStatus, maybeStartGarage61Collection, restoreFileDraft, sessionMode,
    activeTrialRef, analysisRequestRef, runAnalysisRef, selectedFileRef, setAnalysis, setError, setLoading,
    setNewFileNotice, setPitRule, setReferenceLap, setReferenceLapManual, setSelectedFile, setSessionMode,
    setStatus, setTargetLap, setTargetLapManual, signatureRef, waitingFileRef]);
}
