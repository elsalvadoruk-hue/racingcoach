import { useEffect, useState } from 'react';

// One browser tab speaks. With two app tabs open every call was heard twice, and a muted
// second tab would also acknowledge calls the first one had not said yet. The tab that holds
// this lock speaks; the others stand by and take over when it closes.
const LOCK = 'iracing-coach-audio-host';

export const STANDBY_MESSAGE = '다른 탭에서 음성을 재생하고 있습니다. 이 탭에서는 음성이 나오지 않습니다. 그 탭을 닫으면 이 탭이 이어받습니다.';

export default function useAudioHost() {
  const supported = typeof navigator !== 'undefined' && typeof navigator.locks?.request === 'function';
  // Without the Web Locks API (old browsers, tests) every tab speaks, as before.
  const [host, setHost] = useState(!supported);
  useEffect(() => {
    if (!supported) return undefined;
    let active = true, release;
    const held = new Promise((resolve) => { release = resolve; });
    navigator.locks.request(LOCK, () => {
      if (active) setHost(true);
      return held;
    }).catch(() => { if (active) setHost(true); });
    return () => { active = false; release(); setHost(false); };
  }, [supported]);
  return host;
}
