import { useCallback, useEffect, useRef, useState } from 'react';
import { activateSession, currentSessionResult, labRequest, LMU_SESSION_KEY, rememberSession } from '../desktop/sessionData';
import { useHoverHelpSetting } from '../components/HelpGuide';

const unavailable = error => [400, 404, 409, 422].includes(error.status);
export const LMU_RECORDING_POLL_MS = 5000;
const fileVersion = file => JSON.stringify([file.file_signature, file.status, file.analyzable,
  file.detected_laps, file.complete_laps, file.valid_laps]);
const versions = files => new Map(files.map(file => [file.name, fileVersion(file)]));

export function useLmuWorkspace() {
  const [hoverHelpEnabled, toggleHoverHelp] = useHoverHelpSetting();
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selectedFile, setSelectedFile] = useState('');
  const automatic = useRef({ controller: null, catalog: null, handled: new Map(), paused: 0, manualBaselinePending: false });
  useEffect(() => {
    const state = automatic.current;
    let disposed = false, timer, first = true;
    const poll = async () => {
      const controller = new AbortController();
      state.controller = controller;
      try {
        const catalog = await labRequest('files', {}, controller.signal);
        if (disposed || controller.signal.aborted) return;
        const initial = first;
        first = false;
        const files = catalog.files || [];
        const previous = state.catalog || new Map();
        state.catalog = versions(files);
        if (state.manualBaselinePending) {
          state.handled = new Map(state.catalog); state.manualBaselinePending = false;
          return;
        }
        if (state.paused) return;
        let saved; try { saved = JSON.parse(localStorage.getItem(LMU_SESSION_KEY)); } catch { /* No previous LMU selection. */ }
        // The catalog is newest first. A new recording takes precedence over an
        // older saved file; keep the chosen laps when reopening the same file.
        let lastError;
        for (const [index, file] of files.entries()) {
          if (controller.signal.aborted) return;
          // A recording without one valid lap has no lap times or reference to show:
          // open the newest one that does. It stays available in the file picker.
          if (file.analyzable !== true || !(file.valid_laps > 0)) continue;
          if (!initial && (file.auto_load === false || state.handled.get(file.name) === fileVersion(file)
            || previous.get(file.name) !== fileVersion(file))) continue;
          const filename = file.name;
          if (file.auto_load === false && filename !== saved?.filename) continue;
          let session;
          try {
            if (initial && filename === saved?.filename) {
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
          if (disposed || controller.signal.aborted) return;
          activateSession(currentSessionResult(session, session.initialComparison));
          rememberSession(session, session.defaultTarget, session.defaultReference, []);
          setSelectedFile(filename);
          setError('');
          setRevision(v => v + 1);
          // Older catalog entries must not reopen after the newest one succeeds.
          files.slice(index).forEach(row => state.handled.set(row.name, fileVersion(row)));
          return;
        }
        if (lastError) throw lastError;
        setError('');
      } catch (e) {
        if (!disposed && !controller.signal.aborted) setError(e.message);
      } finally {
        if (!disposed) {
          if (!controller.signal.aborted) setInitialLoading(false);
          timer = setTimeout(poll, LMU_RECORDING_POLL_MS);
        }
      }
    };
    poll();
    return () => { disposed = true; clearTimeout(timer); state.controller?.abort(); };
  }, []);
  const adoptDesktopAnalysis = useCallback(session => {
    const state = automatic.current;
    state.controller?.abort();
    state.handled = new Map(state.catalog || []);
    state.manualBaselinePending = state.catalog === null;
    setInitialLoading(false); setSelectedFile(session.filename); setError('');
  }, []);
  const pauseAutomaticSelection = useCallback(() => {
    const state = automatic.current;
    state.paused += 1; state.controller?.abort();
    return () => { state.paused -= 1; };
  }, []);
  return { game: 'lmu', initialLoading, error, setError, revision, selectedFile, adoptDesktopAnalysis, pauseAutomaticSelection, hoverHelpEnabled, toggleHoverHelp,
    loading: false, analysis: null, analysisSnapshot: null, activeTrial: null };
}
