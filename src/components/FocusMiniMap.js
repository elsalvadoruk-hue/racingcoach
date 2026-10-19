import React, { useMemo, useState } from 'react';

// Where the practice corners are: the whole lap with the focus corners numbered, and one corner up close
// coloured by what the target lap did there (brake / full throttle / in between). Drawn from the analysed
// lap's own GPS points in local metres (small numbers, see AGENTS 6-39); without GPS nothing is drawn.
// Self-contained on purpose: the comparison map components belong to the road-map work.
const MARGIN_M = 70;
const BRAKE = 0.08;
const FULL = 0.95;

function project(path) {
  const points = (path || []).filter((p) => Number.isFinite(p?.lat) && Number.isFinite(p?.lon) && Number.isFinite(p?.distance_pct));
  if (points.length < 20) return [];
  const lat0 = points.reduce((sum, p) => sum + p.lat, 0) / points.length;
  const lon0 = points.reduce((sum, p) => sum + p.lon, 0) / points.length;
  const kx = Math.cos((lat0 * Math.PI) / 180) * 111320;
  return points.map((p) => ({ ...p, x: (p.lon - lon0) * kx, y: -(p.lat - lat0) * 110540 }));
}

const inside = (pct, from, to) => (from <= to ? pct >= from && pct <= to : pct >= from || pct <= to);

function frame(points, size, pad) {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const scale = Math.min((size.w - pad * 2) / Math.max(1, maxX - minX), (size.h - pad * 2) / Math.max(1, maxY - minY));
  const ox = (size.w - (maxX - minX) * scale) / 2;
  const oy = (size.h - (maxY - minY) * scale) / 2;
  return (p) => [ox + (p.x - minX) * scale, oy + (p.y - minY) * scale];
}

const line = (points, at) => points.map((p, i) => `${i ? 'L' : 'M'}${at(p).map((v) => v.toFixed(1)).join(' ')}`).join('');

function runs(points) {
  // consecutive points with the same input state become one coloured piece (sharing the joining point)
  const state = (p) => (p.brake > BRAKE ? 'brake' : p.throttle >= FULL ? 'full' : 'part');
  const out = [];
  points.forEach((p, i) => {
    const s = state(p);
    if (!out.length || out[out.length - 1].state !== s) out.push({ state: s, points: i ? [points[i - 1], p] : [p] });
    else out[out.length - 1].points.push(p);
  });
  return out;
}

const COLOUR = { brake: 'text-destructive', full: 'text-success', part: 'text-warning' };

export default function FocusMiniMap({ path, corners, trackLength, pick: chosenPick, onPick, vertical = false }) {
  const points = useMemo(() => project(path), [path]);
  const [ownPick, setOwnPick] = useState(0);
  const pick = chosenPick ?? ownPick;
  const setPick = onPick || setOwnPick;
  const usable = (corners || []).filter((c) => Number.isFinite(c?.entry_pct) && Number.isFinite(c?.exit_pct));
  if (!points.length || !usable.length) return null;
  const chosen = usable[Math.min(pick, usable.length - 1)];
  const overview = { w: 260, h: 200 };
  const at = frame(points, overview, 10);
  const nearest = (pct) => points.reduce((best, p) => (Math.abs(p.distance_pct - pct) < Math.abs(best.distance_pct - pct) ? p : best), points[0]);

  const margin = trackLength > 0 ? MARGIN_M / trackLength : 0.012;
  const from = (chosen.entry_pct - margin + 1) % 1;
  const to = (chosen.exit_pct + margin) % 1;
  let zoomPoints = points.filter((p) => inside(p.distance_pct, from, to));
  if (from > to) zoomPoints = [...zoomPoints.filter((p) => p.distance_pct >= from), ...zoomPoints.filter((p) => p.distance_pct <= to)];
  const zoom = { w: 260, h: 200 };
  const zat = zoomPoints.length > 2 ? frame(zoomPoints, zoom, 16) : null;
  const corner = zoomPoints.filter((p) => inside(p.distance_pct, chosen.entry_pct, chosen.exit_pct));
  // corners close together (or both beside the start line) would hide each other's number
  const dots = [];
  usable.forEach((c) => {
    const [x0, y0] = at(nearest(c.apex_pct ?? (c.entry_pct + c.exit_pct) / 2));
    const clear = (step) => !dots.some(([dx, dy]) => Math.hypot(dx - (x0 + step * 12), dy - (y0 + step * 10)) < 17);
    const step = [0, 1, 2, 3, 4, 5].find(clear) ?? 6;
    dots.push([Math.min(overview.w - 9, x0 + step * 12), Math.min(overview.h - 9, y0 + step * 10)]);
  });
  const start = at(points[0]);
  const ahead = at(points[Math.min(points.length - 1, 6)]);
  const angle = (Math.atan2(ahead[1] - start[1], ahead[0] - start[0]) * 180) / Math.PI;

  return (
    <div className={vertical ? 'flex flex-wrap gap-3 xl:flex-col' : 'flex flex-wrap gap-3'} data-testid="focus-mini-map">
      <figure className="m-0">
        <svg viewBox={`0 0 ${overview.w} ${overview.h}`} width={overview.w} height={overview.h} role="img" aria-label="트랙 전체에서 과제 코너 위치" className="rounded-lg border border-border bg-muted/30">
          <path d={line(points, at)} fill="none" stroke="currentColor" className="text-muted-foreground/60" strokeWidth="2.5" strokeLinejoin="round" />
          {usable.map((c, i) => {
            const seg = points.filter((p) => inside(p.distance_pct, c.entry_pct, c.exit_pct));
            return seg.length > 1 && <path key={`seg-${c.label}`} d={line(seg, at)} fill="none" stroke="currentColor" className={i === pick ? 'text-primary' : 'text-primary/50'} strokeWidth="4.5" strokeLinecap="round" />;
          })}
          <g transform={`translate(${start[0]} ${start[1]}) rotate(${angle})`} className="text-foreground">
            <path d="M-4 -6 L-4 6 M2 -4 L8 0 L2 4" fill="none" stroke="currentColor" strokeWidth="1.6" />
          </g>
          {dots.map(([x, y], i) => {
            const c = usable[i];
            return (
              <g key={`dot-${c.label}`} onClick={() => setPick(i)} style={{ cursor: 'pointer' }}>
                <circle cx={x} cy={y} r="8" fill="currentColor" className={i === pick ? 'text-primary' : 'text-primary/70'} />
                <text x={x} y={y + 3.5} textAnchor="middle" fontSize="10" fontWeight="700" fill="currentColor" className="text-primary-foreground">{i + 1}</text>
              </g>
            );
          })}
        </svg>
        <figcaption className="mt-1 max-w-[260px] text-xs text-muted-foreground">전체 트랙 · 번호를 누르면 확대 그림과 그래프가 그 코너로 바뀝니다. |→ 는 출발선과 진행 방향입니다.</figcaption>
      </figure>
      {zat && (
        <figure className="m-0">
          <svg viewBox={`0 0 ${zoom.w} ${zoom.h}`} width={zoom.w} height={zoom.h} role="img" aria-label={`${chosen.label} 확대`} className="rounded-lg border border-border bg-muted/30">
            <defs>
              <marker id="focus-zoom-arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
              </marker>
            </defs>
            {corner.length > 1 && <path d={line(corner, zat)} fill="none" stroke="currentColor" className="text-primary/25" strokeWidth="14" strokeLinecap="round" strokeLinejoin="round" />}
            {runs(zoomPoints).map((piece, i) => (
              <path key={i} d={line(piece.points, zat)} fill="none" stroke="currentColor" className={COLOUR[piece.state]} strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
            ))}
            <path d={line(zoomPoints.slice(-3), zat)} fill="none" stroke="currentColor" className="text-foreground" strokeWidth="1.5" markerEnd="url(#focus-zoom-arrow)" />
          </svg>
          <figcaption className="mt-1 max-w-[260px] text-xs text-muted-foreground">
            <span className="font-semibold text-foreground">{pick + 1}. {chosen.label}</span> 확대 · 이번 대상 랩:
            <span className="text-destructive"> ■ 제동</span><span className="text-warning"> ■ 부분·코스팅</span><span className="text-success"> ■ 풀스로틀</span>
          </figcaption>
        </figure>
      )}
    </div>
  );
}
