const finite = Number.isFinite;

// Return only the observed interval inside the viewport. A comparison across
// the start line uses its short circular span, never almost an entire lap.
export function annotationRange(note, viewport, trackLength) {
  const range = note.range;
  if (!range || !finite(range.from) || !finite(range.to) || !finite(trackLength) || trackLength <= 0 ||
      !viewport.every(finite) || viewport[1] <= viewport[0]) return null;
  const from = range.from;
  const to = range.type === 'comparison' ? from + ((range.to - from + .5) % 1 + 1) % 1 - .5 : range.to;
  if (Math.abs(to - from) * trackLength < .5) return null;
  let best = null;
  for (const offset of [-1, 0, 1]) {
    const a = (from + offset) * trackLength, b = (to + offset) * trackLength;
    const left = Math.max(viewport[0], Math.min(a, b)), right = Math.min(viewport[1], Math.max(a, b));
    if (right <= left || (best && right - left <= best.right - best.left)) continue;
    best = { left, right, from: a, to: b, type: range.type,
      fromVisible: a >= viewport[0] && a <= viewport[1], toVisible: b >= viewport[0] && b <= viewport[1] };
  }
  return best;
}
