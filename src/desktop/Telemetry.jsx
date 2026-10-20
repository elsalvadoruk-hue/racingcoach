import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useId } from 'react';
import { cn } from '../lib/utils';
import { colourRuns } from '../lib/lossColours';
import { Button } from '../components/ui/button';
import { GripVertical } from 'lucide-react';
import { finite, number, signed, length } from './sessionData';
import { controlWindow, controlStatus } from './controlEvents';
import { annotationRange } from './annotationRanges';
import GraphGhostCursor from './GraphGhostCursor';

export function ControlLegend({ controls = {}, range }) {
  return <div className="assist-status"><span data-testid="abs-status"><b>ABS</b><span className="target-control-status"><i className="abs-line-key" aria-hidden="true" />내 랩 {controlStatus(controls.abs?.target, range[0] / length, range[1] / length)}</span><span className="reference-control-status"><i className="abs-fill-key" aria-hidden="true" />기준 {controlStatus(controls.abs?.reference, range[0] / length, range[1] / length)}</span></span></div>;
}

export function TelemetryChart({ channel, rows, position, onPosition, ghost, ghostMotion, range, last, apex, onRange, onZoomBack, notes = [], controls = {}, activeNote, onNote, onLabelDown, onNudge, dragging }) {
  const canvasRef = useRef(null);
  const absClipId = useId();
  const drag = useRef(null);
  const [selection, setSelection] = useState(null);
  const navigation = useRef({ range, onRange });
  navigation.current = { range, onRange };
  const [size, setSize] = useState({ width: 1000, height: 100 });
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(canvasRef.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const canvas = canvasRef.current;
    const wheel = event => {
      const { range: [from, to], onRange: change } = navigation.current;
      if (to - from >= length || !change) return;
      event.preventDefault();
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      const start = Math.max(0, Math.min(length - (to - from), from + Math.max(-.3, Math.min(.3, delta / 667)) * (to - from)));
      change([start, start + to - from], false);
    };
    canvas.addEventListener('wheel', wheel, { passive: false });
    return () => canvas.removeEventListener('wheel', wheel);
  }, []);
  const showAbs = ['brake', 'pedals'].includes(channel.key);
  const ownAbs = controlWindow(controls.abs?.target, range[0] / length, range[1] / length);
  const referenceAbs = controlWindow(controls.abs?.reference, range[0] / length, range[1] / length);
  const plotLeft = 40, plotRight = size.width - 12, plotBottom = Math.max(20, size.height - 20);
  const nearestRow = distance => finite(distance) && distance >= range[0] && distance <= range[1]
    && distance >= rows[0]?.distance && distance <= rows[rows.length - 1]?.distance
    ? rows.reduce((nearest, row) => !nearest || Math.abs(row.distance - distance) < Math.abs(nearest.distance - distance) ? row : nearest, null) : null;
  const current = nearestRow(ghost ? ghost.target : position);
  const reference = ghost ? nearestRow(ghost.reference) : current;
  const pedals = channel.key === 'pedals';
  const traces = pedals ? [{ key: 'referenceBrake', reference: true, pedal: 'brake' }, { key: 'referenceThrottle', reference: true, pedal: 'throttle' }, { key: 'brake', pedal: 'brake' }, { key: 'throttle', pedal: 'throttle' }] : [{ key: channel.reference, reference: true }, { key: channel.key }].filter(t => t.key);
  const values = rows.flatMap(row => traces.map(t => row[t.key])).filter(finite);
  const low = Math.min(0, ...values), high = Math.max(channel.key === 'delta' ? .03 : 1, ...values);
  const steering = channel.key === 'steering';
  const steeringExtent = Math.max(1, Math.abs(low), Math.abs(high));
  const domain = steering ? [-steeringExtent * 1.08, steeringExtent * 1.08]
    : channel.domain || [low - (high - low) * .08, high + (high - low) * .08];
  const ticks = steering ? [-steeringExtent, 0, steeringExtent]
    : channel.domain ? [0, 50, 100] : [low, (high + low) / 2, high];
  const x = value => plotLeft + (value - range[0]) / (range[1] - range[0]) * (plotRight - plotLeft);
  const y = value => plotBottom - (value - domain[0]) / (domain[1] - domain[0]) * (plotBottom - 8);
  const valueAt = (pct, key) => {
    if (!finite(pct)) return null;
    const near = rows.reduce((best, row) => !best || Math.abs(row.distance_pct - pct) < Math.abs(best.distance_pct - pct) ? row : best, null);
    // Do not snap a missing point across a distant gap or beyond this window.
    return near && Math.abs(near.distance_pct - pct) * length <= 12 && finite(near[key]) ? near[key] : null;
  };
  const line = key => {
    let connected = false;
    return rows.map(row => { if (!finite(row[key])) { connected = false; return ''; } const command = connected ? channel.key === 'gear' ? 'H' : 'L' : 'M'; connected = true; return command === 'H' ? `H${x(row.distance).toFixed(2)} V${y(row[key]).toFixed(2)}` : `${command}${x(row.distance).toFixed(2)},${y(row[key]).toFixed(2)}`; }).join(' ');
  };
  const at = event => {
    const rect = event.currentTarget.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - plotLeft) / (plotRight - plotLeft)));
    return range[0] + fraction * (range[1] - range[0]);
  };
  const referenceKey = key => ({ speed: 'referenceSpeed', brake: 'referenceBrake', throttle: 'referenceThrottle', steering: 'referenceSteering', gear: 'referenceGear' }[key]);
  const chartNotes = notes.filter(note => (note.channel === channel.key || (pedals && ['brake', 'throttle'].includes(note.channel))) &&
    (annotationRange(note, range, length) || (finite(note.jumpAt ?? note.at) && (note.jumpAt ?? note.at) * length >= range[0] && (note.jumpAt ?? note.at) * length <= range[1])));
  const lanes = [-Infinity, -Infinity];
  const badgePositions = new Map([...chartNotes].sort((a, b) => (a.jumpAt ?? a.at) - (b.jumpAt ?? b.at)).map(note => {
    const span = annotationRange(note, range, length);
    const px = Math.max(plotLeft + 10, Math.min(plotRight - 10, x(span ? (span.left + span.right) / 2 : (note.jumpAt ?? note.at) * length)));
    const lane = lanes[0] + 24 < px ? 0 : lanes[1] + 24 < px ? 1 : lanes[0] < lanes[1] ? 0 : 1;
    lanes[lane] = px;
    return [note.id, { x: px, y: lane ? Math.max(13, Math.min(34, plotBottom - 10)) : 13 }];
  }));
  return <div className={cn('telemetry-row', channel.key === 'speed' && 'speed-row', dragging && 'is-reordering')} data-channel-row={channel.key}>
    <button className="channel-label" aria-label={`${channel.label} 순서 이동`} aria-pressed={dragging} title="좌클릭 드래그로 채널 순서 변경 · Alt+↑/↓로 이동 · 자동 저장" onPointerDown={onLabelDown} onKeyDown={event => { if (event.altKey && ['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); onNudge?.(event.key === 'ArrowUp' ? -1 : 1); } }}><GripVertical aria-hidden="true" /><strong>{channel.label}</strong><span>{channel.unit}</span></button>
    <div className="trace-canvas" ref={canvasRef}>
      <svg viewBox={`0 0 ${size.width} ${size.height}`} data-range={range.join(',')} onPointerMove={e => { onPosition(at(e)); if (drag.current) setSelection([drag.current.from, at(e)]); }}
        onPointerDown={e => { if (e.button !== 0) return; drag.current = { from: at(e), x: e.clientX }; setSelection([at(e), at(e)]); e.currentTarget.setPointerCapture(e.pointerId); onPosition(at(e)); }}
        onPointerUp={e => { const start = drag.current; drag.current = null; setSelection(null); if (!start || Math.abs(e.clientX - start.x) < 8) return; if (e.clientX < start.x) onZoomBack?.(Math.min(1, Math.abs(e.clientX - start.x) / Math.max(1, plotRight - plotLeft))); else if (at(e) - start.from >= 10) onRange?.([start.from, at(e)], true); }}
        onPointerCancel={() => { drag.current = null; setSelection(null); }} aria-label={`${channel.label} 거리별 비교 그래프`} role="img">
        {[0, .2, .4, .6, .8, 1].map(t => <line key={t} x1={plotLeft + (plotRight - plotLeft) * t} x2={plotLeft + (plotRight - plotLeft) * t} y1="5" y2={plotBottom + 4} className="chart-grid" />)}
        {ticks.map((tick, index) => <g key={index}><line data-testid={steering && tick === 0 ? 'steering-zero-guide' : undefined} x1={plotLeft} x2={plotRight} y1={y(tick)} y2={y(tick)} className="chart-grid" /><text x={plotLeft - 8} y={y(tick) + 4} textAnchor="end" className="axis-label">{tick.toFixed(channel.key === 'delta' ? 2 : 0)}</text></g>)}
        {channel.domain && [25, 75].map(tick => <line key={tick} data-testid="pedal-quarter-guide" data-value={tick} x1={plotLeft} x2={plotRight} y1={y(tick)} y2={y(tick)} className="chart-quarter-grid" />)}
        {showAbs && <g data-control-kind="abs" data-control-side="reference" className="control-events control-reference" pointerEvents="none">
            <title>ABS · 기준 · {controlStatus(controls.abs?.reference, range[0] / length, range[1] / length)}</title>
            {referenceAbs.runs.map(([from, to], i) => <g key={i} data-testid="control-event" data-from={from} data-to={to}>
              <rect x={x(from * length)} y="5" width={Math.max(2, x(to * length) - x(from * length))} height={Math.max(1, plotBottom - 5)} className="control-shade" />
              <line x1={x(from * length)} x2={Math.max(x(from * length) + 2, x(to * length))} y1={plotBottom - 7} y2={plotBottom - 7} className="control-strip" />
            </g>)}
          </g>}
        {channel.key === 'speed' && rows.some(r => r.speedBand) && rows.slice(1).map((r, i) => r.speedBand && rows[i].speedBand ? <path key={i} d={`M${x(rows[i].distance)},${y(rows[i].speedBand[0])} L${x(r.distance)},${y(r.speedBand[0])} L${x(r.distance)},${y(r.speedBand[1])} L${x(rows[i].distance)},${y(rows[i].speedBand[1])} Z`} fill="var(--lab-reference)" opacity=".14" /> : null)}
        {traces.map(trace => <path key={trace.key} data-channel={trace.key} d={line(trace.key)} className={cn(trace.reference ? 'reference-trace' : 'target-trace', channel.key === 'delta' && 'delta-trace', trace.pedal && `pedal-${trace.pedal}`)} />)}
        {showAbs && ownAbs.runs.length > 0 && <>
          <defs><clipPath id={absClipId} clipPathUnits="userSpaceOnUse">{ownAbs.runs.map(([from, to], i) => <rect key={i} data-testid="target-abs-window" data-from={from} data-to={to} x={x(from * length)} y="4" width={Math.max(2, x(to * length) - x(from * length))} height={Math.max(1, plotBottom - 3)} />)}</clipPath></defs>
          <g data-control-kind="abs" data-control-side="target" className="control-events control-target" clipPath={`url(#${absClipId})`} pointerEvents="none">
            <title>ABS · 내 랩 · {controlStatus(controls.abs?.target, range[0] / length, range[1] / length)}</title>
            <path data-testid="target-abs-trace" d={line('brake')} className="abs-target-line" />
            <path d={line('brake')} className="abs-target-line abs-target-dashes" />
          </g>
        </>}
        {finite(apex) && apex >= range[0] && apex <= range[1] && <line data-testid="apex-marker" x1={x(apex)} x2={x(apex)} y1="4" y2={plotBottom + 4} stroke="var(--lab-reference)" strokeDasharray="3 4" opacity=".55" />}
        {selection && <rect x={x(Math.min(...selection))} y="4" width={Math.abs(x(selection[1]) - x(selection[0]))} height={plotBottom} fill="var(--lab-target)" opacity=".15" />}
        {!ghost && <line x1={x(position)} x2={x(position)} y1="4" y2={plotBottom + 4} className="cursor-line" />}
        {(ghost || ghostMotion) && <GraphGhostCursor motion={ghostMotion} ghost={ghost} rows={rows} channelKey={channel.key} range={range} x={x} y={y} bottom={plotBottom} />}
        {!ghost && current && finite(current[channel.key]) && <circle cx={x(current.distance)} cy={y(current[channel.key])} r="3" className="cursor-dot" />}
        {chartNotes.map(note => {
          const anchor = note.at ?? note.refAt, refKey = referenceKey(note.channel);
          const value = valueAt(anchor, finite(note.at) ? note.channel : refKey);
          const span = annotationRange(note, range, length);
          if (!span && !finite(value)) return null;
          const px = x(anchor * length), py = y(value), refValue = valueAt(note.refAt, refKey);
          const { x: badgeX, y: badgeY } = badgePositions.get(note.id);
          const targetVisible = finite(note.at) && note.at * length >= range[0] && note.at * length <= range[1] && finite(value);
          const referenceVisible = finite(note.refAt) && note.refAt * length >= range[0] && note.refAt * length <= range[1] && finite(refValue);
          const bracketY = badgeY + 8;
          return <g key={note.key} className={cn('trace-note', activeNote === note.id && 'active')} role="button" tabIndex={0} aria-label={`${note.id}. ${note.label}: ${note.value}`} data-testid={`trace-note-${note.id}`} data-note-key={note.key} data-pct={anchor}
            onPointerDown={e => e.stopPropagation()} onClick={e => { e.stopPropagation(); onNote?.(note); }} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); onNote?.(note); } }}>
            <title>{note.label}: {note.text}</title>
            {span && <g data-testid="comparison-range" data-note-range={note.key} data-range-type={span.type} data-from={span.from} data-to={span.to} data-left={span.left} data-right={span.right} pointerEvents="none">
              <rect x={x(span.left)} y={bracketY} width={x(span.right) - x(span.left)} height={Math.max(1, plotBottom - bracketY)} className="note-range-fill" />
              <line x1={x(span.left)} x2={x(span.right)} y1={bracketY} y2={bracketY} className="note-range-bar" />
              {span.type === 'comparison' ? <>
                {span.fromVisible && targetVisible && <line x1={x(span.from)} x2={x(span.from)} y1={bracketY} y2={py} className="note-range-target" />}
                {span.toVisible && referenceVisible && <line x1={x(span.to)} x2={x(span.to)} y1={bracketY} y2={y(refValue)} className="note-range-reference" />}
              </> : <>
                {span.fromVisible && <line x1={x(span.from)} x2={x(span.from)} y1={bracketY} y2={plotBottom} className="note-range-bar" />}
                {span.toVisible && <line x1={x(span.to)} x2={x(span.to)} y1={bracketY} y2={plotBottom} className="note-range-bar" />}
              </>}
            </g>}
            {referenceVisible && <>{!span && <line x1={x(note.refAt * length)} x2={x(note.refAt * length)} y1="5" y2={plotBottom} className="note-reference" />}<circle cx={x(note.refAt * length)} cy={y(refValue)} r="3.5" className="note-reference-point" /></>}
            {!span && <line x1={px} x2={badgeX} y1={py} y2={badgeY} className="note-leader" />}
            {targetVisible && <circle cx={px} cy={py} r="3" className="note-point" />}
            <circle cx={badgeX} cy={badgeY} r="9" className="note-badge" /><text x={badgeX} y={badgeY + 3.5} textAnchor="middle">{note.id}</text>
          </g>;
        })}
        {last && [0, .25, .5, .75, 1].map(t => <text key={t} x={plotLeft + (plotRight - plotLeft) * t} y={size.height - 2} textAnchor={t === 0 ? 'start' : t === 1 ? 'end' : 'middle'} className="axis-label">{Math.round(range[0] + (range[1] - range[0]) * t).toLocaleString()} m</text>)}
      </svg>
    </div>
    <div className="channel-value">{pedals ? <><strong className="brake-value">{number(current?.brake, 0)}<small>{number(reference?.referenceBrake, 0)}</small></strong><strong className="throttle-value">{number(current?.throttle, 0)}<small>{number(reference?.referenceThrottle, 0)}</small></strong></> : <><strong>{channel.key === 'delta' ? signed(current?.delta) : number(current?.[channel.key], 0)}</strong>{channel.reference && <span>{number(reference?.[channel.reference], 0)}</span>}</>}</div>
  </div>;
}

export function TrackMap({ points, loss, corners, selected, onSelect, position, large = false, names = {}, segmentMode = false, rotatable = false, schematic = false }) {
  const host = useRef(null), svg = useRef(null), drag = useRef(null);
  const [size, setSize] = useState({ width: 320, height: 220 });
  const [view, setView] = useState(null);
  const [rotation, setRotation] = useState(0);
  const viewRef = useRef(null);
  const suppressClick = useRef(false);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setSize({ width: Math.max(1, entry.contentRect.width), height: Math.max(1, entry.contentRect.height) }));
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const geometry = useMemo(() => {
    if (!points.length) return null;
    const width = size.width, height = size.height, pad = large ? 24 : 8;
    let best = null;
    // Rotate only the overview to use its shallow viewport. Keep scale uniform:
    // GPS shape is unchanged and the compass shows the resulting orientation.
    for (let degrees = 0; degrees <= (large ? 0 : 175); degrees += 5) {
      const angle = degrees * Math.PI / 180;
      const rotated = points.map(p => ({ ...p, x: p.x * Math.cos(angle) - p.y * Math.sin(angle), y: p.x * Math.sin(angle) + p.y * Math.cos(angle) }));
      const xs = rotated.map(p => p.x), ys = rotated.map(p => p.y);
      const minX = Math.min(...xs), minY = Math.min(...ys), w = Math.max(1, Math.max(...xs) - minX), h = Math.max(1, Math.max(...ys) - minY);
      const scale = Math.min((width - pad * 2) / w, (height - pad * 2) / h);
      const score = w * scale + .15 * h * scale;
      if (!best || score > best.score) best = { scale, score, degrees, points: rotated.map(p => ({ ...p, x: (width - w * scale) / 2 + (p.x - minX) * scale, y: (height - h * scale) / 2 + (p.y - minY) * scale })) };
    }
    return best;
  }, [points, size, large]);
  const frame = view || [0, 0, size.width, size.height];
  viewRef.current = frame;
  useEffect(() => setView(null), [points, size]);
  const zoom = (factor, fraction = [.5, .5]) => setView(previous => {
    const [x, y, w, h] = previous || [0, 0, size.width, size.height];
    const scale = Math.max(.12, Math.min(2, w * factor / size.width));
    const nw = size.width * scale, nh = size.height * scale;
    return [x + (w - nw) * fraction[0], y + (h - nh) * fraction[1], nw, nh];
  });
  const wheelRef = useRef(zoom); wheelRef.current = zoom;
  const hasGeometry = Boolean(geometry);
  useEffect(() => {
    const element = svg.current;
    if (!element) return undefined;
    const wheel = e => { e.preventDefault(); const box = element.getBoundingClientRect(); wheelRef.current(Math.exp(Math.max(-250, Math.min(250, e.deltaY)) * .002), [(e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height]); };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [hasGeometry]);
  const displayPts = useMemo(() => {
    const points = geometry?.points || [];
    if (!rotation) return points;
    const angle = rotation * Math.PI / 180;
    const cx = size.width / 2, cy = size.height / 2;
    return points.map(point => {
      const dx = point.x - cx, dy = point.y - cy;
      return { ...point, x: cx + dx * Math.cos(angle) - dy * Math.sin(angle), y: cy + dx * Math.sin(angle) + dy * Math.cos(angle) };
    });
  }, [geometry, rotation, size.width, size.height]);
  const nearest = pct => displayPts.reduce((best, p) => !best || Math.abs(p.distance_pct - pct) < Math.abs(best.distance_pct - pct) ? p : best, null);
  const runs = colourRuns(displayPts, pct => {
    if (!segmentMode) return loss?.colourAt(pct) || null;
    const index = corners.findIndex(c => pct >= c.entry_pct && pct <= c.exit_pct);
    return index < 0 ? null : index % 2 ? 'var(--lab-reference)' : 'var(--lab-target)';
  });
  const active = selected ? displayPts.filter(p => p.distance_pct >= selected.entry_pct && p.distance_pct <= selected.exit_pct) : [];
  const line = values => values.map(p => `${p.x},${p.y}`).join(' ');
  const cursor = finite(position) ? nearest(position / length) : null;
  return <div className={cn('track-map', large && 'large-gps-map')} data-rotation={rotation}>
    <div className="gps-map-controls"><span>{rotatable ? '좌클릭 드래그 · Ctrl+좌클릭 회전 · 휠 확대' : '좌클릭 드래그 · 휠 확대'}</span><Button variant="outline" size="sm" aria-label="자동 구간 지도 축소" onClick={() => zoom(1.3)}>−</Button><Button variant="outline" size="sm" aria-label="자동 구간 지도 확대" onClick={() => zoom(1 / 1.3)}>+</Button><Button variant="ghost" size="sm" onClick={() => { setView(null); setRotation(0); }}>전체 맞춤</Button>{rotatable && <Button variant="ghost" size="sm" onClick={() => setRotation(0)}>방향 초기화</Button>}</div>
    <div className="gps-viewport" ref={host}>{geometry ? <><svg ref={svg} viewBox={frame.join(' ')} role="group" tabIndex={0} aria-label={large ? '자동 구간 번호 선택 지도' : '전체 GPS 시간 이득 손실 지도'}
      onContextMenu={e => e.preventDefault()} onKeyDown={e => { if (e.key === 'Home') { e.preventDefault(); setView(null); } else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoom(1 / 1.3); } else if (e.key === '-') { e.preventDefault(); zoom(1.3); } }}
      onClickCapture={event => { if (suppressClick.current && event.detail) { event.stopPropagation(); suppressClick.current = false; } }}
      onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); suppressClick.current = false; drag.current = rotatable && event.ctrlKey ? { mode: 'rotate', x: event.clientX, y: event.clientY, rotation } : { mode: 'pan', x: event.clientX, y: event.clientY, view: frame }; }}
      onPointerMove={event => { const start = drag.current; if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) < 4) return; suppressClick.current = true; if (!event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.setPointerCapture(event.pointerId); if (start.mode === 'rotate') setRotation(start.rotation + (event.clientY - start.y) * .5); else setView([start.view[0] - (event.clientX - start.x) * start.view[2] / size.width, start.view[1] - (event.clientY - start.y) * start.view[3] / size.height, start.view[2], start.view[3]]); }}
      onPointerUp={() => { drag.current = null; }} onPointerLeave={() => { if (!suppressClick.current) drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
      {colourRuns(active, () => 'selected').map((run, i) => <polyline key={`selected-${i}`} points={line(run.points)} fill="none" stroke="var(--lab-target)" strokeWidth="11" opacity=".3" vectorEffect="non-scaling-stroke" />)}
      {runs.map((run, i) => <polyline key={i} data-testid="overview-loss-run" data-colour={run.colour || ''} points={line(run.points)} fill="none" stroke={run.colour || 'hsl(var(--muted-foreground))'} strokeWidth={large ? 2.5 : 3} strokeLinecap="round" vectorEffect="non-scaling-stroke" />)}
      {corners.map(corner => {
        const point = nearest(corner.apex_pct), picked = selected?.id === corner.id;
        return <g key={corner.id} className={cn('gps-pin', picked && 'is-selected')} role="button" tabIndex={0} aria-pressed={picked} aria-label={`${corner.id} 선택`} onClick={() => onSelect(corner.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); onSelect(corner.id); } }} transform={`translate(${point.x} ${point.y}) scale(${frame[2] / size.width})`}>
          <title>{corner.id} · {names[corner.id]?.number || names[corner.id]?.name || '미입력'} · {signed(corner.delta_s)} s</title><circle r={large ? 11 : 8} /><text textAnchor="middle" dy="3.5">{corner.id.slice(1)}</text>
        </g>;
      })}
      {cursor && <circle cx={cursor.x} cy={cursor.y} r={6 * frame[2] / size.width} fill="none" stroke="var(--lab-target)" strokeWidth="2" vectorEffect="non-scaling-stroke" pointerEvents="none" />}
    </svg>{!schematic && <span className="gps-compass" title="GPS 북쪽 방향"><i style={{ transform: `rotate(${geometry.degrees + rotation}deg)` }}>↑</i>N</span>}</> : <p>좌표가 없어 표시할 수 없습니다.</p>}</div>
    <div className="map-caption"><span>{(length / 1000).toFixed(3)} km · {schematic ? '기록 좌표 개략도' : 'GPS 실측'}</span><span>자동 구간 {corners.length}개</span></div>
    {loss && <div className="loss-legend"><span className="gain">● 시간 이득</span><span className="loss">● 시간 손실</span><small>진한 색 ≥ {loss.scale.toFixed(2)} s / 100m · 회색: 측정 없음</small></div>}
  </div>;
}
