import { useEffect, useRef } from 'react';

// The radio host already polls this status for both games. Keep the cursor above
// the analysis workspace, which remounts whenever a new recording is loaded.
export function useDriveEndNavigation(lifecycle, selectedGame) {
  const seen = useRef(null);
  const { lifecycle_id: id, completed_serial: serial, phase, active_drive: active,
    last_completed: completed } = lifecycle || {};
  const { serial: eventSerial, game } = completed || {};
  useEffect(() => {
    if (!id || !Number.isSafeInteger(serial) || serial < 0) return;
    const previous = seen.current;
    seen.current = { id, serial: previous?.id === id ? Math.max(previous.serial, serial) : serial, selectedGame };
    // Opening the app or changing backend/game establishes a baseline. An old
    // completion must not replace the driver's chosen starting page.
    if (!previous || previous.id !== id || previous.selectedGame !== selectedGame || serial <= previous.serial) return;
    if (phase !== 'ENDED' || active !== false || eventSerial !== serial || game !== selectedGame) return;
    window.dispatchEvent(new CustomEvent('coach-navigate', { detail: 'summary' }));
  }, [id, serial, phase, active, eventSerial, game, selectedGame]);
}
