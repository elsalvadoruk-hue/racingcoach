import React, { useLayoutEffect, useRef } from 'react';

function valueAt(rows, distance, key) {
  if (!Number.isFinite(distance) || !rows.length || distance < rows[0].distance || distance > rows[rows.length - 1].distance) return null;
  let lo = 0, hi = rows.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (rows[mid].distance <= distance) lo = mid; else hi = mid; }
  const a = rows[lo], b = rows[hi];
  if (distance === a.distance) return Number.isFinite(a[key]) ? a[key] : null;
  if (distance === b.distance) return Number.isFinite(b[key]) ? b[key] : null;
  if (!Number.isFinite(a[key]) || !Number.isFinite(b[key])) return null;
  // Gear traces are steps; other markers follow the same line drawn between samples.
  return key === 'gear' ? a[key] : a[key] + (b[key] - a[key]) * (distance - a.distance) / (b.distance - a.distance);
}

export default function GraphGhostCursor({ motion, ghost, rows, channelKey, range, x, y, bottom }) {
  const target = useRef(null), reference = useRef(null), dot = useRef(null);
  useLayoutEffect(() => {
    const draw = snapshot => {
      for (const [key, node] of [['target', target.current], ['reference', reference.current]]) {
        const distance = snapshot?.[key];
        const visible = Number.isFinite(distance) && distance >= range[0] && distance <= range[1];
        node.setAttribute('visibility', visible ? 'visible' : 'hidden');
        if (visible) {
          node.setAttribute('x1', x(distance)); node.setAttribute('x2', x(distance));
          node.setAttribute('data-distance', distance);
        }
      }
      const distance = snapshot?.target, value = valueAt(rows, distance, channelKey);
      const visible = Number.isFinite(value) && distance >= range[0] && distance <= range[1];
      dot.current.setAttribute('visibility', visible ? 'visible' : 'hidden');
      if (visible) { dot.current.setAttribute('cx', x(distance)); dot.current.setAttribute('cy', y(value)); }
    };
    draw(motion ? motion.read() : ghost);
    return motion?.subscribe(draw);
  }, [motion, ghost, rows, channelKey, range, x, y]);
  return <g pointerEvents="none" aria-hidden="true">
    <line ref={target} data-testid="graph-ghost-target" y1="4" y2={bottom + 4} visibility="hidden" className="graph-ghost-target" />
    <line ref={reference} data-testid="graph-ghost-reference" y1="4" y2={bottom + 4} visibility="hidden" className="graph-ghost-reference" />
    <circle ref={dot} data-testid="graph-ghost-dot" r="3" visibility="hidden" className="cursor-dot" />
  </g>;
}
