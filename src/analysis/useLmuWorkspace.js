import { useCallback, useEffect, useRef, useState } from 'react';
import { activateSession, currentSessionResult, labRequest, LMU_SESSION_KEY, rememberSession } from '../desktop/sessionData';
import { useHoverHelpSetting } from '../components/HelpGuide';

const unavailable = error => [400, 404, 409, 422].includes(error.status);

export function useLmuWorkspace() {
  const [hoverHelpEnabled, toggleHoverHelp] = useHoverHelpSetting();
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selectedFile, setSelectedFile] = useState('');
  const initialization = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    initialization.current = controller;
    (async () => {
      const catalog = await labRequest('files', {}, controller.signal);
      let saved; try { saved = JSON.parse(localStorage.getItem(LMU_SESSION_KEY)); } catch { /* No previous LMU selection. */ }
      // The catalog is newest first. A new recording takes precedence over an
      // older saved file; keep the chosen laps when reopening the same file.
      let lastError;
      for (const file of catalog.files || []) {
        if (controller.signal.aborted) return;
        if (file.analyzable !== true) continue;
        const filename = file.name;
        let session;
        try {
          if (filename === saved?.filename) {
            try {
              session = await labRequest('session', { ...saved, filename,
                reference_selection: saved.reference_selection || 'AUTO' }, controller.signal);
            } catch (error) {
              if (controller.signal.aborted || !unavailable(error)) throw error;
              // The file may still be usable after saved lap numbers expire.
            }
          }
          if (!session) session = await labRequest('session', { filename, reference_selection: 'AUTO' }, controller.signal);
          // Comparing the best lap with itself shows nothing to practise: when the
          // automatic personal best is the opened lap, open the latest other lap.
          if (session.personalReference?.same_lap) {
            const other = [...(session.laps || [])].reverse().find(lap => lap.status === 'VALID' && lap.lap_number !== session.defaultReference);
            if (other) session = await labRequest('session', { filename, target_lap: other.lap_number, reference_selection: 'AUTO' }, controller.signal);
          }
        } catch (error) {
          if (controller.signal.aborted || !unavailable(error)) throw error;
          lastError = error;
          continue;
        }
        if (controller.signal.aborted) return;
        activateSession(currentSessionResult(session, session.initialComparison));
        rememberSession(session, session.defaultTarget, session.defaultReference, []);
        setSelectedFile(filename);
        setRevision(v => v + 1);
        return;
      }
      if (lastError) throw lastError;
    })().catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setInitialLoading(false); });
    return () => controller.abort();
  }, []);
  const adoptDesktopAnalysis = useCallback(session => {
    initialization.current?.abort();
    setInitialLoading(false); setSelectedFile(session.filename); setError('');
  }, []);
  return { game: 'lmu', initialLoading, error, setError, revision, selectedFile, adoptDesktopAnalysis, hoverHelpEnabled, toggleHoverHelp,
    loading: false, analysis: null, analysisSnapshot: null, activeTrial: null };
}
