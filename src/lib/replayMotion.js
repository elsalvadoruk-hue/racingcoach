// One shared frame snapshot keeps every channel on the same measured replay clock.
// SVG markers subscribe directly so numeric readouts need fewer React renders.
export function createReplayMotion() {
  let snapshot = null;
  const listeners = new Set();
  return {
    read: () => snapshot,
    publish(next) { snapshot = next; listeners.forEach(listener => listener(next)); },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}
