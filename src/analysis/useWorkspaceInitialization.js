import { useEffect } from "react";
import { initialModeForFile } from './analysisShared';

export function useWorkspaceInitialization({
  loadExternalReferences, loadFiles, loadGarage61ApiStatus, loadMemory, loadStatus,
  maybeStartGarage61Collection, restoreFileDraft, runAnalysisRef, selectedFileRef, setError,
  setInitialLoading, setPitRule, setSelectedFile, setSessionMode
}) {
  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      setInitialLoading(true);
      try {
        const [filePayload, statusPayload] = await Promise.all([loadFiles(), loadStatus()]);
        void loadMemory();
        void loadGarage61ApiStatus();
        if (cancelled) return;
        const firstFile = selectedFileRef.current || filePayload.find((file) => file.analyzable !== false)?.name || (statusPayload.telemetry?.analyzable !== false ? statusPayload.telemetry?.file_name : null);
        if (firstFile) {
          const firstEntry = filePayload.find((item) => item.name === firstFile);
          if (!selectedFileRef.current) { selectedFileRef.current = firstFile; setSelectedFile(firstFile); restoreFileDraft(firstFile); }
          const initialMode = initialModeForFile(firstEntry);
          const initialPitRule = initialMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
          setSessionMode(initialMode);
          setPitRule(initialPitRule);
          const referencePayload = await loadExternalReferences(firstFile, true);
          await runAnalysisRef.current?.({
            includeAI: false,
            quiet: true,
            filename: firstFile,
            sessionModeOverride: initialMode,
            pitRuleOverride: initialPitRule,
            persistMemory: false,
            forceAutoLaps: true,
            externalReferencesOverride: (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9),
          });
          void maybeStartGarage61Collection(firstFile, referencePayload);
        }
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
  }, [loadExternalReferences, loadFiles, loadGarage61ApiStatus, loadMemory, loadStatus, maybeStartGarage61Collection, restoreFileDraft,
    runAnalysisRef, selectedFileRef, setError, setInitialLoading, setPitRule, setSelectedFile, setSessionMode]);
}
