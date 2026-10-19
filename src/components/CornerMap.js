import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { LocateFixed, Maximize, Minus, Pause, Play, Plus } from 'lucide-react';
import { Button } from './ui/button';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { ToggleGroup, ToggleGroupItem } from './ui/toggle-group';
import { COMPARISON_COLORS } from '../chartTheme';
import GeographicMapTilesPlain from './GeographicMapTiles';
import { IMAGERY_CREDIT } from '../lib/geographicMap';
import { colourRuns } from '../lib/lossColours';
import { gapAt, positionAt, replaySpan, timeline } from '../lib/ghost';

const ASPECT = 600 / 360;
// The imagery only changes with the camera; a hover or a moving ghost must not redraw it.
const GeographicMapTiles = React.memo(GeographicMapTilesPlain);
// The driver's choice of plain or loss-coloured line, kept between files (per browser, convenience only).
const LOSS_KEY = 'iracing.map.lossColour';
const savedLoss = () => { try { return window.localStorage.getItem(LOSS_KEY) !== 'off'; } catch { return true; } };
const GHOST_SPEEDS = [1, 2, 4];
const clock = (seconds) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
// A car that has finished (or not yet started) its observed stretch stays at its first or last point.
const heldAt = (line, t) => (line.length < 2 ? null : positionAt(line, Math.min(line[line.length - 1].t, Math.max(line[0].t, t))));
const EMPTY_PATH = [];
const finite = Number.isFinite;
const nearest = (points, pct) => points.reduce((best, point) => (
  !best || Math.abs(point.distance_pct - pct) < Math.abs(best.distance_pct - pct) ? point : best
), null);

export function fitMapView(points, aspect = ASPECT) {
  if (!points.length) return { x: 0, y: 0, width: 600, height: 360 };
  const xs = points.map(point => point.x);
  const ys = points.map(point => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const width = Math.max(maxX - minX, (maxY - minY) * aspect, 1) * 1.28;
  const height = width / aspect;
  return { x: (minX + maxX - width) / 2, y: (minY + maxY - height) / 2, width, height };
}

export function zoomMapView(view, factor, anchor, fullWidth) {
  const width = Math.min(fullWidth * 4, Math.max(fullWidth / 1024, view.width * factor));
  const ratio = width / view.width;
  return {
    x: anchor.x - (anchor.x - view.x) * ratio,
    y: anchor.y - (anchor.y - view.y) * ratio,
    width,
    height: view.height * ratio,
  };
}

// The driven line starts and ends at the line but its two ends never quite meet; like RaceStudio, a
// chequered start/finish bar across the track and an arrow for the direction of travel cover that spot.
// Placed at the lap's first measured point, pointing along its first few metres; null without one.
export function startFinish(points) {
  const first = points[0];
  if (!first || !(first.distance_pct < .01)) return null;
  const ahead = points.find((point, index) => index > 0 && !point.breakBefore && Math.hypot(point.x - first.x, point.y - first.y) > 1e-9
    && point.distance_pct - first.distance_pct > .0015) || points[1];
  if (!ahead) return null;
  const angle = Math.atan2(ahead.y - first.y, ahead.x - first.x) * 180 / Math.PI;
  return Number.isFinite(angle) ? { x: first.x, y: first.y, angle } : null;
}

const CHEQUER = [0, 1, 2, 3, 4, 5, 6].flatMap(row => [0, 1].map(col => ({ row, col })));

function StartFinishMark({ mark, unit }) {
  // Drawn in screen pixels (scaled by the map unit) so it stays the same size at any zoom.
  return <g data-testid="map-start-finish" transform={`translate(${mark.x} ${mark.y}) rotate(${mark.angle}) scale(${unit})`} pointerEvents="none" aria-label="출발선과 진행 방향">
    <rect x={-6} y={-16} width={12} height={32} rx={2} fill="#111" stroke="#fff" strokeWidth="1.5" />
    {CHEQUER.map(({ row, col }) => (row + col) % 2 === 0 && <rect key={`${row}-${col}`} x={-4 + col * 4} y={-14 + row * 4} width={4} height={4} fill="#fff" />)}
    <path d="M10 0 H26" stroke="#111" strokeWidth="5" strokeLinecap="round" />
    <path d="M24 -6 L33 0 L24 6 Z" fill="#111" stroke="#111" strokeWidth="2" strokeLinejoin="round" />
    <path d="M10 0 H26" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" />
    <path d="M25 -4.5 L31.5 0 L25 4.5 Z" fill="#fff" />
  </g>;
}

// The comparison graphs' channels at a lap position (linear between rows, the gear from the nearer row).
export function channelsAt(rows, pct) {
  if (!rows.length || !finite(pct) || pct < rows[0].distance_pct || pct > rows[rows.length - 1].distance_pct) return null;
  let lo = 0, hi = rows.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].distance_pct <= pct) lo = mid; else hi = mid;
  }
  const a = rows[lo], b = rows[hi];
  const f = b.distance_pct === a.distance_pct ? 0 : (pct - a.distance_pct) / (b.distance_pct - a.distance_pct);
  const mix = key => (finite(a[key]) && finite(b[key]) ? a[key] + (b[key] - a[key]) * f : null);
  const near = f < .5 ? a : b;
  return { speed: mix('speed'), throttle: mix('throttle'), brake: mix('brake'), gear: finite(near.gear) ? near.gear : null,
    referenceSpeed: mix('referenceSpeed'), referenceThrottle: mix('referenceThrottle'), referenceBrake: mix('referenceBrake'),
    referenceGear: finite(near.referenceGear) ? near.referenceGear : null };
}

function Pedal({ value, colour }) {
  return <span className="block h-1.5 overflow-hidden rounded bg-white/15"><span className="block h-full rounded" style={{ width: `${finite(value) ? Math.max(0, Math.min(100, value)) : 0}%`, background: colour, transition: 'width 100ms linear' }} /></span>;
}

// Ghost replay readings over the map: each car's speed, gear and pedals at its own position on the lap.
// One grid for both cars so the numbers line up; one font with even-width digits; the long reference name
// goes to the foot of the panel instead of squeezing the numbers.
function GhostPanel({ target, reference, gap, lineLabel, aggregate }) {
  const cells = (name, colour, speed, gear, throttle, brake) => <>
    <span className="truncate font-semibold" style={{ color: colour }}>● {name}</span>
    <span className="text-right text-foreground">{finite(speed) ? Math.round(speed) : '--'}<span className="ml-0.5 text-[10px] text-muted-foreground">km/h</span></span>
    <span className="text-right text-foreground">{finite(gear) ? gear : '--'}<span className="ml-0.5 text-[10px] text-muted-foreground">단</span></span>
    <Pedal value={throttle} colour="rgb(34,197,94)" />
    <Pedal value={brake} colour="rgb(239,68,68)" />
  </>;
  const speedGap = target && reference && finite(target.speed) && finite(reference.referenceSpeed) ? target.speed - reference.referenceSpeed : null;
  return <div data-testid="ghost-panel" className="pointer-events-none absolute left-2.5 top-2.5 flex w-[19rem] flex-col gap-2 rounded-lg border border-white/10 p-3 text-xs tabular-nums shadow-lg"
    style={{ background: 'rgba(17, 24, 33, .85)' }}>
    <div className="grid grid-cols-[minmax(0,1fr)_3.75rem_2.25rem_3rem_3rem] items-center gap-x-2 gap-y-1.5 whitespace-nowrap">
      <span />
      <span className="text-right text-[10px] text-muted-foreground">속도</span>
      <span className="text-right text-[10px] text-muted-foreground">기어</span>
      <span className="text-[10px] text-muted-foreground">스로틀</span>
      <span className="text-[10px] text-muted-foreground">브레이크</span>
      {cells('내 랩', COMPARISON_COLORS.target, target?.speed, target?.gear, target?.throttle, target?.brake)}
      {cells('기준 랩', COMPARISON_COLORS.reference, reference?.referenceSpeed, reference?.referenceGear, reference?.referenceThrottle, reference?.referenceBrake)}
    </div>
    <div className="flex justify-between gap-2 whitespace-nowrap border-t border-white/10 pt-2 text-foreground">
      <span>{finite(gap) ? (Math.abs(gap) < .005 ? '시간 차이 없음' : gap > 0 ? `기준보다 ${gap.toFixed(2)}초 느림` : `기준보다 ${(-gap).toFixed(2)}초 빠름`) : '시간 차이 --'}</span>
      {finite(speedGap) && <span className="text-muted-foreground">속도 {speedGap > 0 ? '+' : ''}{Math.round(speedGap)} km/h</span>}
    </div>
    <div className="truncate text-[10px] text-muted-foreground" title={lineLabel}>기준: {lineLabel}</div>
    {aggregate && <div className="text-[10px] leading-snug text-muted-foreground">기준 점은 대표 실제 랩, 기준 값은 그래프와 같은 여러 랩 종합입니다.</div>}
  </div>;
}

function segments(points) {
  return points.reduce((groups, point) => {
    if (!groups.length || point.breakBefore) groups.push([]);
    groups[groups.length - 1].push(point);
    return groups;
  }, []);
}

export default function CornerMap({ projected, referenceProjected = EMPTY_PATH, trackMap, courseDiagram, geographic = false, geographicMetersPerUnit, geographicOrigin, mapStatus = 'unavailable', referenceLineSource, onFullLap, onSatellite, onRoadMap, referenceLabel = '기준 주행', detailLoading = false, corners, selected, selectedRange, activePct, onSelect, onHover, focusVersion, loss = null, traceRows = EMPTY_PATH }) {
  const svgRef = useRef(null);
  const drag = useRef(null);
  const manual = useRef(false);
  const previousFocus = useRef(null);
  const suppressClick = useRef(false);
  const hoverAt = useRef(null), hoverFrame = useRef(null), lastHoverPct = useRef(null);
  useEffect(() => () => { if (hoverFrame.current) window.cancelAnimationFrame(hoverFrame.current); }, []);
  const [dragging, setDragging] = useState(false);
  const [viewport, setViewport] = useState({ width: 600, height: 360 });
  const [displayMode, setDisplayMode] = useState('comparison');
  const diagramMode = Boolean(courseDiagram) && (displayMode === 'course' || !projected.length);
  const [backgroundMode, setBackgroundMode] = useState(geographic ? 'satellite' : 'road');
  const [imageryStatus, setImageryStatus] = useState('loading');
  const [failedRoad, setFailedRoad] = useState(null);
  const [failedSatellite, setFailedSatellite] = useState(null);
  const [failedDiagram, setFailedDiagram] = useState(null);
  const [lossOn, setLossOn] = useState(savedLoss);
  // Ghost replay: both laps' cars on the map at the same time since their lap start.
  const targetLine = useMemo(() => timeline(projected), [projected]);
  const referenceLine = useMemo(() => timeline(referenceProjected), [referenceProjected]);
  const rangeStart = selectedRange?.[0], rangeEnd = selectedRange?.[1];
  const span = useMemo(() => replaySpan(targetLine, referenceLine, finite(rangeStart) ? [rangeStart, rangeEnd] : null),
    [targetLine, referenceLine, rangeStart, rangeEnd]);
  const [ghost, setGhost] = useState(null);           // the shown time (controls, readout), ten times a second
  const [playing, setPlaying] = useState(false);
  const [ghostSpeed, setGhostSpeed] = useState(1);
  const [follow, setFollow] = useState(false);
  const ghostRef = useRef(null);                      // the replay's own clock, ahead of React between commits
  const targetDot = useRef(null), referenceDot = useRef(null);
  const followView = useRef(null);
  const spanStart = span?.[0], spanEnd = span?.[1];
  const showGhost = (t) => { ghostRef.current = t; setGhost(t); };
  useEffect(() => { ghostRef.current = null; setGhost(null); setPlaying(false); }, [spanStart, spanEnd]);
  // While playing, the dots (and a following camera) move every frame by writing the SVG directly. React
  // re-renders the map only ten times a second and the graphs not at all, so they do not hold the dots back.
  useEffect(() => {
    if (!playing || !finite(spanStart)) return undefined;
    let frame, last = null, committed = 0;
    const step = (now) => {
      const from = finite(ghostRef.current) ? ghostRef.current : spanStart;
      const next = Math.min(spanEnd, from + (last === null ? 0 : (now - last) / 1000 * ghostSpeed));
      last = now;
      ghostRef.current = next;
      const mine = heldAt(targetLine, next), theirs = heldAt(referenceLine, next);
      if (mine && targetDot.current) { targetDot.current.setAttribute('cx', mine.x); targetDot.current.setAttribute('cy', mine.y); }
      if (theirs && referenceDot.current) { referenceDot.current.setAttribute('cx', theirs.x); referenceDot.current.setAttribute('cy', theirs.y); }
      if (follow && mine && svgRef.current) {
        const current = viewRef.current;
        followView.current = { ...current, x: mine.x - current.width / 2, y: mine.y - current.height / 2 };
        const moved = followView.current;
        svgRef.current.setAttribute('viewBox', [moved.x, moved.y, moved.width, moved.height].join(' '));
      }
      const done = next >= spanEnd;
      if (done || now - committed > 100) {
        committed = now;
        setGhost(next);
        if (follow && followView.current) { manual.current = true; setView(followView.current); }
      }
      if (done) setPlaying(false); else frame = window.requestAnimationFrame(step);
    };
    frame = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(frame);
  }, [playing, spanStart, spanEnd, ghostSpeed, follow, targetLine, referenceLine]);
  // The graphs' cursor goes to the player's car when the replay stops or is moved by hand.
  useEffect(() => {
    if (playing || !finite(ghost)) return;
    const mine = heldAt(targetLine, ghost);
    if (mine) onHover(mine.pct);
  }, [playing, ghost]); // eslint-disable-line react-hooks/exhaustive-deps
  const playGhost = () => {
    if (playing) { setPlaying(false); return; }
    if (!finite(ghostRef.current) || ghostRef.current >= spanEnd) showGhost(spanStart);
    setPlaying(true);
  };
  const lossColoured = Boolean(!diagramMode && loss && lossOn);
  const rangeKey = selectedRange ? selectedRange.join(':') : '';
  // The driven and reference lines are drawn once per change of path, range or colouring, not per frame.
  const lineLayer = useMemo(() => {
    const inRange = points => selectedRange ? points.filter(point => point.distance_pct >= selectedRange[0] && point.distance_pct <= selectedRange[1]) : points;
    const highlighted = inRange(projected);
    const referenceHighlighted = inRange(referenceProjected);
    const line = points => points.map(point => point.x + ',' + point.y).join(' ');
    return <>
      {lossColoured ? <>
        {/* Loss colouring: the whole lap faint when a corner is picked, the picked range (or the lap) bold. */}
        {selectedRange && colourRuns(projected, loss.colourAt).map((run, index) => <polyline key={'loss-all-' + index} points={line(run.points)} fill="none" stroke={run.colour || 'var(--coach-muted)'} strokeOpacity=".45" strokeWidth="3" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />)}
        {colourRuns(highlighted, loss.colourAt).map((run, index) => <polyline key={'loss-' + index} data-testid="map-loss-run" data-colour={run.colour || ''} points={line(run.points)} fill="none" stroke={run.colour || 'var(--coach-muted)'} strokeWidth="5" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />)}
      </> : <>
        {segments(projected).map((segment, index) => <polyline key={index} points={line(segment)} fill="none" stroke="var(--coach-muted)" strokeOpacity=".55" strokeWidth="3" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />)}
        {segments(highlighted).map((segment, index) => <polyline key={'h-' + index} points={line(segment)} fill="none" stroke={COMPARISON_COLORS.target} strokeWidth="5" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />)}
      </>}
      {selectedRange && segments(referenceProjected).map((segment, index) => <polyline key={'reference-background-' + index} points={line(segment)} fill="none" stroke={COMPARISON_COLORS.reference} strokeOpacity=".25" strokeWidth="2" strokeDasharray="7 5" vectorEffect="non-scaling-stroke" />)}
      {segments(referenceHighlighted).filter(segment => segment.length > 1).map((segment, index) => <polyline key={'reference-' + index} data-testid="map-reference-line" points={line(segment)} fill="none" stroke={COMPARISON_COLORS.reference} strokeWidth="3" strokeDasharray="7 5" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />)}
    </>;
  }, [projected, referenceProjected, rangeKey, lossColoured, loss]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggleLoss = () => setLossOn((value) => {
    try { window.localStorage.setItem(LOSS_KEY, value ? 'off' : 'on'); } catch { /* convenience only */ }
    return !value;
  });
  useEffect(() => { setBackgroundMode(geographic ? 'satellite' : 'road'); }, [trackMap?.trackId, geographic]);
  const hintId = useId();
  const aspect = viewport.width / viewport.height;
  const hasPoints = projected.length > 0 || Boolean(courseDiagram);
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const measure = () => {
      const rect = svg.getBoundingClientRect();
      if (rect.width && rect.height) setViewport(previous => (
        previous.width === rect.width && previous.height === rect.height ? previous : { width: rect.width, height: rect.height }
      ));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(svg);
    return () => observer.disconnect();
  }, [hasPoints]);
  const allPoints = useMemo(() => {
    if (!diagramMode) return [...projected, ...referenceProjected];
    const [x, y, width, height] = courseDiagram.viewBox;
    return [{ x, y }, { x: x + width, y: y + height }];
  }, [projected, referenceProjected, diagramMode, courseDiagram]);
  const fullView = useMemo(() => fitMapView(allPoints, aspect), [allPoints, aspect]);
  const focusedPoints = useMemo(() => {
    if (diagramMode || !selected) return allPoints;
    const points = allPoints.filter(point => point.distance_pct >= selected.entry_pct && point.distance_pct <= selected.exit_pct);
    if (points.length > 1) return points;
    return allPoints.filter(point => selectedRange && point.distance_pct >= selectedRange[0] && point.distance_pct <= selectedRange[1]);
  }, [allPoints, selected, selectedRange, diagramMode]);
  const fittedView = useMemo(() => fitMapView(focusedPoints.length ? focusedPoints : allPoints, aspect), [focusedPoints, allPoints, aspect]);
  const [view, setView] = useState(fullView);
  const viewRef = useRef(view);
  viewRef.current = view;
  const fullViewRef = useRef(fullView);
  fullViewRef.current = fullView;
  const focusKey = `${diagramMode ? `official-${courseDiagram.trackId}` : trackMap?.trackId || 'gps'}:${diagramMode ? 'course' : selected?.id || 'lap'}:${focusVersion || 0}`;
  useEffect(() => {
    if (previousFocus.current !== focusKey) {
      previousFocus.current = focusKey;
      manual.current = false;
    }
    // Detail may refine the geometry; keep a camera the driver has already moved.
    if (!manual.current) setView(fittedView);
    else setView(current => {
      const height = current.width / aspect;
      return Math.abs(current.height - height) < 1e-8 ? current : { ...current, y: current.y + (current.height - height) / 2, height };
    });
  }, [focusKey, fittedView, aspect]);
  const pointFromClient = useCallback((clientX, clientY) => {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM?.();
    if (matrix && svg.createSVGPoint) {
      const point = svg.createSVGPoint();
      point.x = clientX;
      point.y = clientY;
      return point.matrixTransform(matrix.inverse());
    }
    const rect = svg?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return null;
    const camera = viewRef.current;
    const scale = Math.min(rect.width / camera.width, rect.height / camera.height);
    return {
      x: camera.x + (clientX - rect.left - (rect.width - camera.width * scale) / 2) / scale,
      y: camera.y + (clientY - rect.top - (rect.height - camera.height * scale) / 2) / scale,
    };
  }, []);
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const wheel = event => {
      event.preventDefault();
      if (drag.current || !event.deltaY) return;
      const anchor = pointFromClient(event.clientX, event.clientY);
      if (!anchor) return;
      const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? svg.clientHeight : 1);
      manual.current = true;
      setView(current => zoomMapView(current, Math.exp(Math.max(-240, Math.min(240, pixels)) * .0025), anchor, fullViewRef.current.width));
    };
    svg.addEventListener('wheel', wheel, { passive: false });
    return () => svg.removeEventListener('wheel', wheel);
  }, [hasPoints, pointFromClient]);
  const reset = (camera = fittedView) => {
    manual.current = camera !== fittedView;
    setView(camera);
    svgRef.current?.focus({ preventScroll: true });
  };
  const zoom = factor => {
    manual.current = true;
    setView(current => zoomMapView(current, factor, { x: current.x + current.width / 2, y: current.y + current.height / 2 }, fullView.width));
  };
  const handleDown = event => {
    if (event.button !== 0 || drag.current) return;
    suppressClick.current = false;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const current = viewRef.current;
    const scale = Math.min(rect.width / current.width, rect.height / current.height);
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, view: current, scale, moved: false, owner: event.target };
    event.target.setPointerCapture?.(event.pointerId);
  };
  const handleMove = event => {
    const held = drag.current;
    if (held) {
      if (held.id !== event.pointerId) return;
      const dx = event.clientX - held.x, dy = event.clientY - held.y;
      if (!held.moved && Math.hypot(dx, dy) < 4) return;
      held.moved = true;
      manual.current = true;
      setDragging(true);
      setView({ ...held.view, x: held.view.x - dx / held.scale, y: held.view.y - dy / held.scale });
      return;
    }
    if (diagramMode) return;
    // At most one hover per screen frame, and only when the nearest point changes.
    hoverAt.current = { x: event.clientX, y: event.clientY };
    if (hoverFrame.current) return;
    hoverFrame.current = window.requestAnimationFrame(() => {
      hoverFrame.current = null;
      const point = pointFromClient(hoverAt.current.x, hoverAt.current.y);
      if (!point) return;
      const closest = allPoints.reduce((best, next) => (
        !best || Math.hypot(next.x - point.x, next.y - point.y) < Math.hypot(best.x - point.x, best.y - point.y) ? next : best
      ), null);
      if (closest && closest.distance_pct !== lastHoverPct.current) { lastHoverPct.current = closest.distance_pct; onHover(closest.distance_pct); }
    });
  };
  const handleUp = event => {
    const held = drag.current;
    if (!held || held.id !== event.pointerId) return;
    suppressClick.current = held.moved;
    drag.current = null;
    setDragging(false);
    if (held.owner.hasPointerCapture?.(event.pointerId)) held.owner.releasePointerCapture(event.pointerId);
  };
  const handleKey = event => {
    if (event.target !== event.currentTarget) return;
    if (['+', '=', '-'].includes(event.key)) { event.preventDefault(); zoom(event.key === '-' ? 1.25 : .8); }
    else if (event.key === '0' || event.key === 'Home') { event.preventDefault(); reset(); }
    else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      manual.current = true;
      setView(current => ({ ...current,
        x: current.x + (event.key === 'ArrowLeft' ? -.15 : event.key === 'ArrowRight' ? .15 : 0) * current.width,
        y: current.y + (event.key === 'ArrowUp' ? -.15 : event.key === 'ArrowDown' ? .15 : 0) * current.height }));
    }
  };
  if (!projected.length && !courseDiagram) return <div className="flex min-h-36 items-center justify-center rounded-xl bg-background p-4 text-sm text-muted-foreground">GPS 좌표가 없어 지도는 표시할 수 없습니다. 아래 코너 버튼으로 비교하세요.</div>;
  const unit = Math.min(view.width / viewport.width, view.height / viewport.height);
  const cursor = finite(activePct) ? nearest(projected, activePct) : null;
  const referenceCursor = finite(activePct) ? nearest(referenceProjected, activePct) : null;
  const inRange = points => selectedRange ? points.filter(point => point.distance_pct >= selectedRange[0] && point.distance_pct <= selectedRange[1]) : points;
  const referenceHighlighted = inRange(referenceProjected);
  const hasReference = referenceHighlighted.length > 1;
  const startMark = diagramMode ? null : startFinish(projected);
  // the dots are drawn at the replay's own clock, so a re-render never puts them back a step
  const ghostNow = finite(ghost) ? (finite(ghostRef.current) ? ghostRef.current : ghost) : null;
  const ghostTarget = finite(ghostNow) ? heldAt(targetLine, ghostNow) : null;
  const ghostReference = finite(ghostNow) ? heldAt(referenceLine, ghostNow) : null;
  const ghostGap = finite(ghost) ? gapAt(targetLine, referenceLine, ghost) : null;
  const lineLabel = referenceLineSource?.driver_name
    ? `Garage61 · ${referenceLineSource.driver_name}${referenceLineSource.lap_time ? ` · ${referenceLineSource.lap_time}` : ''}`
    : referenceLineSource?.label || referenceLabel;
  const visibleCorners = (diagramMode ? [] : corners).map(corner => ({ corner, point: finite(corner.apex_pct) ? nearest(projected, corner.apex_pct) : null }))
    .filter(({ point }) => point && point.x >= view.x && point.x <= view.x + view.width && point.y >= view.y && point.y <= view.y + view.height);
  const cursorOnMarker = cursor && visibleCorners.some(({ point }) => Math.hypot(point.x - cursor.x, point.y - cursor.y) < 16 * unit);
  const roadAvailable = !diagramMode && trackMap && failedRoad !== trackMap.assets.active;
  const satelliteFailed = trackMap && failedSatellite === trackMap.assets.background;
  const satelliteVisible = roadAvailable && backgroundMode === 'satellite' && !satelliteFailed;
  const geographicVisible = !diagramMode && geographic && backgroundMode === 'satellite';
  const assetUrl = path => `${process.env.PUBLIC_URL || ''}${path}`;
  const mapBox = trackMap?.viewBox || [];
  const backgroundCredit = trackMap?.provenance?.background;
  const backgroundLicense = backgroundCredit?.copyrightReview;
  const creditProvider = backgroundCredit?.provider?.split(' / ')[0];
  const creditLicense = backgroundLicense?.license?.match(/CC BY [\d.]+(?: AU)?/)?.[0] || '공개 항공사진';
  const metersPerUnit = diagramMode ? null : trackMap ? Math.hypot(trackMap.transform.a, trackMap.transform.b) : geographicMetersPerUnit;
  const scaleTarget = metersPerUnit ? 90 * unit * metersPerUnit : 0;
  const scaleOrder = scaleTarget > 0 ? 10 ** Math.floor(Math.log10(scaleTarget)) : 1;
  const scaleMeters = [5, 2, 1].map(value => value * scaleOrder).find(value => value <= scaleTarget) || scaleOrder;
  return <div className="coach-map-workspace">
    <div className="coach-map-toolbar">
      <div><h3>{diagramMode ? courseDiagram.displayName : selected ? selected.label || selected.id : '전체 트랙'}</h3><p id={hintId}>휠로 확대·축소 · 왼쪽 버튼을 누른 채 드래그하여 이동</p></div>
      <div className="flex flex-wrap items-center gap-2">
        {courseDiagram && <ToggleGroup type="single" size="sm" variant="outline" value={diagramMode ? 'course' : 'comparison'} onValueChange={value => { if (value) setDisplayMode(value); }} aria-label="지도 보기">
          <ToggleGroupItem value="comparison" aria-label="주행선 비교 보기" disabled={!projected.length}>주행 비교</ToggleGroupItem>
          <ToggleGroupItem value="course" aria-label="전체 코스 안내 보기">코스 안내</ToggleGroupItem>
        </ToggleGroup>}
        {roadAvailable && <ToggleGroup type="single" size="sm" variant="outline" value={backgroundMode} onValueChange={value => { if (value === 'satellite' && onSatellite) onSatellite(); else if (value) setBackgroundMode(value); }} aria-label="지도 배경">
          <ToggleGroupItem value="road" aria-label="도로 지도">도로</ToggleGroupItem>
          <ToggleGroupItem value="satellite" aria-label="위성 지도" disabled={satelliteFailed}>위성</ToggleGroupItem>
        </ToggleGroup>}
        {!diagramMode && geographic && <ToggleGroup type="single" size="sm" variant="outline" value={backgroundMode === 'satellite' ? 'satellite' : 'line'} onValueChange={value => { if (value === 'road' && onRoadMap) onRoadMap(); else if (value) setBackgroundMode(value); }} aria-label="지도 배경">
          {onRoadMap && <ToggleGroupItem value="road" aria-label="도로 지도">도로</ToggleGroupItem>}
          <ToggleGroupItem value="line" aria-label="GPS 라인 지도">라인</ToggleGroupItem>
          <ToggleGroupItem value="satellite" aria-label="위성 지도">위성</ToggleGroupItem>
        </ToggleGroup>}
        <Button variant="outline" size="icon" aria-label="지도 축소" onClick={() => zoom(1.25)}><Minus aria-hidden="true" /></Button>
        <Button variant="outline" size="icon" aria-label="지도 확대" onClick={() => zoom(.8)}><Plus aria-hidden="true" /></Button>
        <Button variant="outline" onClick={() => reset()}><LocateFixed data-icon="inline-start" aria-hidden="true" />{diagramMode ? '코스 맞춤' : selected ? '선택 구간 맞춤' : '지도 맞춤'}</Button>
        <Button variant="outline" onClick={() => reset(fullView)}><Maximize data-icon="inline-start" aria-hidden="true" />전체 지도</Button>
        {/* "전체 지도" only zooms out; this leaves the corner, so the graphs show the whole lap too. */}
        {selected && !diagramMode && onFullLap && <Button variant="outline" onClick={onFullLap}>전체 랩으로</Button>}
        {!diagramMode && loss && <Button variant="outline" aria-pressed={lossColoured} onClick={toggleLoss} data-testid="map-loss-toggle">{lossColoured ? '손실 색칠 끄기' : '손실 색칠'}</Button>}
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3" data-testid="map-background-source">
      <Badge variant="secondary">{diagramMode ? 'iRacing 코스 안내' : geographicVisible ? 'GPS 위성 지도' : roadAvailable ? (trackMap.provenance?.geometry === 'traced' ? '항공사진으로 그린 도로 지도' : trackMap.provenance?.geometry === 'laps' ? '주행 랩으로 그린 도로 지도' : trackMap.provenance?.geometry === 'fitted' ? 'iRacing 공식 트랙맵' : 'DZ replay 도로 지도') : 'GPS 라인 비교'}</Badge>
      {diagramMode ? <>
        <Badge variant="outline">GPS 미정렬</Badge><span className="text-xs text-muted-foreground">전체 코스용 지도 · 주행선은 주행 비교에서 확인하세요.</span>
      </> : geographicVisible ? <>
        <a className="text-xs text-muted-foreground underline underline-offset-4" href="https://goto.arcgisonline.com/maps/World_Imagery" target="_blank" rel="noreferrer" title={IMAGERY_CREDIT}>{IMAGERY_CREDIT}</a>
        <span className="text-xs text-muted-foreground" title="실세계 항공사진입니다. 촬영 시점과 iRacing 코스 모델의 차이가 있을 수 있으며, 게임의 공식 트랙 경계를 나타내지는 않습니다.">실세계 항공사진</span>
        {imageryStatus !== 'ready' && <span role="status" className="text-xs text-muted-foreground">{imageryStatus === 'loading' ? '위성 배경 불러오는 중…' : imageryStatus === 'partial' ? '일부 위성 배경을 불러오지 못했습니다.' : '위성 배경 연결 실패 · GPS 주행선은 계속 비교할 수 있습니다.'}</span>}
      </> : roadAvailable ? <>
        <span className="text-xs text-muted-foreground">{trackMap.displayName}</span>
        <Badge variant="outline" title="지도와 기준 주행선 사이의 정렬 잔차입니다. 실제 도로 경계의 측량 정확도를 의미하지 않습니다.">{finite(trackMap.fit?.p95Meters) ? `정렬 오차 P95 ${trackMap.fit.p95Meters.toFixed(1)} m` : '정렬 오차 미제공'}</Badge>
        {satelliteVisible && backgroundCredit && <span className="text-xs text-muted-foreground" title={backgroundCredit.attribution}>위성: <a href={backgroundCredit.sourceUrl} target="_blank" rel="noreferrer" className="underline underline-offset-4">{creditProvider}</a> · <a href={backgroundLicense?.licenseUrl || backgroundCredit.sourceUrl} target="_blank" rel="noreferrer" className="underline underline-offset-4">{creditLicense}</a>{trackMap.provenance?.geometry !== 'traced' && ' · DZ replay'}</span>}
      </> : <span className="text-xs text-muted-foreground">{trackMap ? '도로 지도 파일을 불러오지 못했습니다.' : mapStatus === 'gps-mismatch' ? 'GPS 위치가 지도 범위와 맞지 않아 주행선만 표시합니다.' : '이 레이아웃에는 등록된 도로 지도가 없습니다.'}</span>}
      {!diagramMode && satelliteFailed && <span role="status" className="text-xs text-muted-foreground">위성 배경을 불러오지 못해 도로 지도를 표시합니다.</span>}
    </div>
    {!diagramMode && <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-4 pb-3 text-sm" data-testid="map-line-source">
      <span style={{ color: COMPARISON_COLORS.target }}>━ 내 실제 주행</span>
      {hasReference && <span style={{ color: COMPARISON_COLORS.reference }} title={referenceLineSource?.reference_file}>┄ {lineLabel} · 실제 랩</span>}
      {hasReference && referenceLineSource?.chart_basis === 'AGGREGATE' && <span className="text-xs text-muted-foreground">지도: 대표 실제 랩 · 그래프: 여러 랩 종합 기준</span>}
      {lossColoured && <span className="flex items-center gap-2 text-xs text-muted-foreground" data-testid="map-loss-legend">
        <span aria-hidden="true" className="inline-block h-2 w-16 rounded" style={{ background: 'linear-gradient(90deg, rgb(34,197,94), rgb(229,231,235), rgb(239,68,68))' }} />
        초록 = 기준보다 시간을 번 곳 · 빨강 = 잃은 곳 (100m당 {loss.scale.toFixed(2)}초 이상이면 가장 진함)
      </span>}
      {detailLoading && <span role="status" className="text-xs text-muted-foreground">정밀 GPS 경로 불러오는 중…</span>}
    </div>}
    {/* Fixed widths throughout: the label of the play button, the clock and the gap change while playing and
        must not push the other controls around. Buttons on one row, the time bar and readout on the next. */}
    {!diagramMode && span && <div className="flex flex-col gap-2 px-4 pb-3 text-sm" data-testid="ghost-controls">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={playGhost} aria-pressed={playing} data-testid="ghost-play" className="w-44 justify-start">
          {playing ? <Pause data-icon="inline-start" aria-hidden="true" /> : <Play data-icon="inline-start" aria-hidden="true" />}{playing ? '멈춤' : finite(ghost) ? '이어서 재생' : selectedRange ? '이 구간 고스트 재생' : '고스트 재생'}
        </Button>
        <ToggleGroup type="single" size="sm" variant="outline" value={String(ghostSpeed)} onValueChange={value => { if (value) setGhostSpeed(Number(value)); }} aria-label="재생 속도">
          {GHOST_SPEEDS.map(speed => <ToggleGroupItem key={speed} value={String(speed)} aria-label={`${speed}배속`}>{speed}×</ToggleGroupItem>)}
        </ToggleGroup>
        {finite(ghost) && <>
          <Button variant="outline" size="sm" aria-pressed={follow} onClick={() => setFollow(value => !value)} className="w-28">{follow ? '따라가기 끄기' : '따라가기'}</Button>
          <Button variant="outline" size="sm" onClick={() => { setPlaying(false); showGhost(null); }}>고스트 끄기</Button>
          <span className="whitespace-nowrap text-xs"><span style={{ color: COMPARISON_COLORS.target }}>● 내 랩</span> <span style={{ color: COMPARISON_COLORS.reference }}>● {lineLabel}</span></span>
        </>}
      </div>
      {finite(ghost) && <div className="flex items-center gap-3">
        <input type="range" aria-label="고스트 시간" data-testid="ghost-time" className="min-w-0 flex-1" min={span[0]} max={span[1]} step=".05" value={ghost}
          onChange={event => { setPlaying(false); showGhost(Number(event.target.value)); }} />
        <span className="w-64 shrink-0 whitespace-nowrap font-mono text-xs tabular-nums" data-testid="ghost-readout">
          {clock(ghost)}{finite(ghostGap) && ` · ${Math.abs(ghostGap) < .005 ? '같음' : ghostGap > 0 ? `기준보다 ${ghostGap.toFixed(2)}초 느림` : `기준보다 ${(-ghostGap).toFixed(2)}초 빠름`}`}
        </span>
      </div>}
    </div>}
    {diagramMode && failedDiagram === courseDiagram.assets.active && <Alert className="mb-3"><AlertDescription>공식 코스 지도를 불러오지 못했습니다. 주행 비교에서 GPS 라인을 확인하세요.</AlertDescription></Alert>}
    {!diagramMode && !hasReference && !detailLoading && <Alert className="mb-3" data-testid="map-reference-unavailable"><AlertDescription>이 구간에 비교 가능한 기준 GPS 좌표가 없습니다. 기준 라인을 표시할 수 없습니다.</AlertDescription></Alert>}
    <div className="relative">
    <svg ref={svgRef} viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`} className="coach-interactive-map" data-dragging={dragging} data-testid="comparison-map" data-map-mode={diagramMode ? 'course' : 'comparison'} role="img" tabIndex={0} aria-label={diagramMode ? '공식 코스 안내 지도' : '코너 선택 지도'} aria-describedby={hintId}
      onPointerDown={handleDown} onPointerMove={handleMove} onPointerUp={handleUp} onPointerCancel={handleUp} onLostPointerCapture={handleUp} onKeyDown={handleKey}
      onClickCapture={event => { if (suppressClick.current && event.detail !== 0) { event.preventDefault(); event.stopPropagation(); } }}>
      {diagramMode && ['background', 'inactive', 'pitroad', 'active', 'start-finish', 'turns'].filter(layer => courseDiagram.assets[layer]).map(layer => <image key={layer} data-testid={`map-course-${layer}`} href={assetUrl(courseDiagram.assets[layer])} x={courseDiagram.viewBox[0]} y={courseDiagram.viewBox[1]} width={courseDiagram.viewBox[2]} height={courseDiagram.viewBox[3]} preserveAspectRatio="none" pointerEvents="none" onError={() => { if (layer === 'active') setFailedDiagram(courseDiagram.assets.active); }} />)}
      {geographicVisible && <GeographicMapTiles view={view} viewport={viewport} origin={geographicOrigin} onStatus={setImageryStatus} />}
      {satelliteVisible && <image data-testid="map-satellite-background" href={assetUrl(trackMap.assets.background)} x={mapBox[0]} y={mapBox[1]} width={mapBox[2]} height={mapBox[3]} preserveAspectRatio="none" pointerEvents="none" onError={() => { setFailedSatellite(trackMap.assets.background); setBackgroundMode('road'); }} />}
      {roadAvailable && <image data-testid="map-road-background" href={assetUrl(trackMap.assets.active)} x={mapBox[0]} y={mapBox[1]} width={mapBox[2]} height={mapBox[3]} preserveAspectRatio="none" opacity={satelliteVisible ? .25 : 1} pointerEvents="none" onError={() => setFailedRoad(trackMap.assets.active)} />}
      {!diagramMode && <g>
      {lineLayer}
      {startMark && <StartFinishMark mark={startMark} unit={unit} />}
      {visibleCorners.map(({ corner, point }) => {
        const active = corner.id === selected?.id;
        return <g key={corner.id} role="button" tabIndex={0} aria-label={`${corner.label || corner.id} 확대`} aria-pressed={active} onClick={() => onSelect(corner.id)} onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(corner.id); }
        }} className="coach-map-marker">
          <circle cx={point.x} cy={point.y} r={(active ? 14 : 11) * unit} fill={active ? COMPARISON_COLORS.target : COMPARISON_COLORS.cursor} stroke={COMPARISON_COLORS.canvas} strokeWidth="2" vectorEffect="non-scaling-stroke" />
          <text x={point.x} y={point.y + 4 * unit} textAnchor="middle" fill={COMPARISON_COLORS.canvas} fontSize={11 * unit} fontWeight="700">{String(corner.id).replace(/^C/, '')}</text>
        </g>;
      })}
      {cursor && !ghostTarget && <circle cx={cursor.x} cy={cursor.y} r={(cursorOnMarker ? 18 : 7) * unit} fill="none" stroke={COMPARISON_COLORS.target} strokeWidth="2" vectorEffect="non-scaling-stroke" pointerEvents="none" />}
      {hasReference && referenceCursor && !ghostTarget && <circle data-testid="map-reference-cursor" cx={referenceCursor.x} cy={referenceCursor.y} r={7 * unit} fill="none" stroke={COMPARISON_COLORS.reference} strokeWidth="2" vectorEffect="non-scaling-stroke" pointerEvents="none" />}
      {ghostReference && <circle ref={referenceDot} data-testid="ghost-reference" cx={ghostReference.x} cy={ghostReference.y} r={7 * unit} fill={COMPARISON_COLORS.reference} stroke={COMPARISON_COLORS.canvas} strokeWidth="2" vectorEffect="non-scaling-stroke" pointerEvents="none" />}
      {ghostTarget && <circle ref={targetDot} data-testid="ghost-target" cx={ghostTarget.x} cy={ghostTarget.y} r={8 * unit} fill={COMPARISON_COLORS.target} stroke={COMPARISON_COLORS.canvas} strokeWidth="2" vectorEffect="non-scaling-stroke" pointerEvents="none" />}
      </g>}
    </svg>
    {ghostTarget && <GhostPanel target={channelsAt(traceRows, ghostTarget.pct)} reference={ghostReference ? channelsAt(traceRows, ghostReference.pct) : null} gap={ghostGap} lineLabel={lineLabel} aggregate={referenceLineSource?.chart_basis === 'AGGREGATE'} />}
    {/* The scale sits on the screen, not in the map: a camera that moves every frame (the ghost replay's
        "따라가기") would otherwise drag it along a step behind. */}
    {metersPerUnit > 0 && <div data-testid="map-distance-scale" aria-label={`${scaleMeters}미터 눈금`} className="pointer-events-none absolute bottom-2.5 left-2.5 rounded-[5px] px-2 pb-1.5 pt-1 text-[11px] leading-tight"
      style={{ background: COMPARISON_COLORS.canvas, opacity: .9, color: COMPARISON_COLORS.cursor }}>
      <div>{scaleMeters >= 1000 ? `${scaleMeters / 1000} km` : `${scaleMeters} m`}</div>
      <svg width={scaleMeters / metersPerUnit / unit + 2} height="6" className="block" aria-hidden="true"><path d={`M1 1 V5 H${scaleMeters / metersPerUnit / unit + 1} V1`} fill="none" stroke={COMPARISON_COLORS.cursor} strokeWidth="2" /></svg>
    </div>}
    </div>
  </div>;
}
