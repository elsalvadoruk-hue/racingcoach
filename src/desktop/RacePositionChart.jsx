import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, RotateCcw, TrendingUp } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription, EmptyContent } from '../components/ui/empty';
import { Alert, AlertDescription } from '../components/ui/alert';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { chartEvents, EVENT_TYPES, RaceEventTraces, RaceEventLabels } from './RacePositionEvents';
import { PanBar } from '../components/LapOverlay';
import { labRequest } from './sessionData';
import { PLOT, pointAt, rankAt, lapTicks, positionPath, positionMarkers, positionX, positionY, raceClock } from './racePositionGeometry';
import './racePositions.css';

const carColor = (row, index) => row.own ? '#64b5f6' : `hsl(${(index * 137.508 + 30) % 360} 67% 66%)`;
const requestHistory = (filename, signal) => labRequest('race-positions', { filename }, signal);

// A moving cursor should not recreate every lap dot in a long endurance race.
const PositionTrace = memo(function PositionTrace({ row, isLap, active, compared, dimmed }) {
  return <g data-series={row.id} opacity={dimmed ? .16 : row.own || active || compared ? 1 : .72}>
    <path d={row.path} fill="none" stroke={row.color} strokeWidth={active ? 1.5 : row.own || compared ? 1.2 : .8} vectorEffect="non-scaling-stroke" data-car={row.id} />
    {isLap && <path d={row.markers} fill="none" stroke={row.color} strokeLinecap="round" strokeWidth={active ? 5.2 : compared ? 4.6 : row.own ? 4.2 : 3.4} data-markers={row.id} />}
  </g>;
});

export default function RacePositionChart({ filename, onClose, load = requestHistory }) {
  const [payload, setPayload] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [windowRange, setWindowRange] = useState([0, 1]), [history, setHistory] = useState([]);
  const [selected, setSelected] = useState(''), [hover, setHover] = useState(null), [drag, setDrag] = useState(null);
  const [shownEvents, setShownEvents] = useState(['events', 'records']);
  const svg = useRef(null), scroller = useRef(null), gesture = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setPayload(null); setWindowRange([0, 1]); setHistory([]); setHover(null); setSelected('');
    load(filename, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setPayload(result);
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message || '순위 기록을 불러오지 못했습니다.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filename, attempt, load]);
  const metric = 2;
  const ownClass = payload?.own_class_id ?? payload?.series?.find(row => row.own)?.class_id;
  // Filter here as well: a stale server must never display another class.
  const series = useMemo(() => ownClass == null ? [] : (payload?.series || [])
    .filter(row => String(row.class_id) === String(ownClass)).map((row, i) => ({ ...row, color: carColor(row, i) })), [payload, ownClass]);
  // An older running backend may still return time observations until restarted.
  const isLap = payload?.x_axis === 'lap';
  const start = (isLap ? payload.start_lap : payload?.start_s) ?? 0;
  const end = Math.max(start + 1, (isLap ? payload.end_lap : payload?.end_s) ?? 1), duration = end - start;
  const axisLabel = value => isLap ? `${Number(value.toFixed(1))}랩` : raceClock(value - start);
  const range = useMemo(() => [start + duration * windowRange[0], start + duration * windowRange[1]], [start, duration, windowRange]);
  const maxRank = useMemo(() => Math.max(8, ...series.map(row => row.points.reduce((max, p) => Math.max(max, p[metric] || 0), 0))), [series, metric]);
  const paths = useMemo(() => series.map(row => {
    const dots = row.points.filter(p => Number.isFinite(p[metric]) && p[0] >= range[0] && p[0] <= range[1]);
    return { ...row, dots, path: positionPath(row.points, metric, range), markers: positionMarkers(dots, metric, range) };
  }), [series, metric, range]);
  // A click pins the primary trace; hovering only adds a temporary comparison.
  const highlighted = selected || hover?.id;
  const compared = hover?.id && hover.id !== highlighted ? hover.id : '';
  const active = series.find(row => row.id === (hover?.id || selected));
  const activePoint = hover && active ? pointAt(active.points, hover.time) : null;
  const renderedPaths = [...paths.filter(row => !row.own && row.id !== highlighted && row.id !== compared),
    ...paths.filter(row => row.own && row.id !== highlighted && row.id !== compared),
    ...paths.filter(row => row.id === compared), ...paths.filter(row => row.id === highlighted)];
  const selectedRow = paths.find(row => row.id === selected);
  const events = useMemo(() => isLap ? chartEvents(selectedRow, shownEvents) : [], [selectedRow, shownEvents, isLap]);
  const eventLanes = isLap && Boolean(selectedRow?.events?.length);
  const plotBottom = positionY(maxRank) + PLOT.top;
  const canvasHeight = plotBottom;
  const revealedCar = useRef('');
  useEffect(() => {
    if (revealedCar.current === selected) return;
    revealedCar.current = selected;
    const element = scroller.current, point = selectedRow?.dots.find(p => Number.isFinite(p[2]));
    if (!element?.clientHeight || !point) return;
    const y = positionY(point[2]);
    if (y < element.scrollTop + 12 || y > element.scrollTop + element.clientHeight - 12) {
      element.scrollTop = Math.max(0, y - element.clientHeight / 2);
    }
  }, [selected, selectedRow]);
  const ticks = isLap ? lapTicks(range) : Array.from({ length: 7 }, (_, i) => range[0] + (range[1] - range[0]) * i / 6);
  const reset = () => { setWindowRange([0, 1]); setHistory([]); setHover(null); setSelected(''); };
  const zoomBack = () => { setWindowRange(history[history.length - 1] || [0, 1]); setHistory(previous => previous.slice(0, -1)); };
  const xAt = clientX => {
    const box = svg.current?.getBoundingClientRect();
    return box?.width ? Math.max(0, Math.min(1, ((clientX - box.left) / box.width * PLOT.width - PLOT.left) / (PLOT.width - PLOT.left - PLOT.right))) : 0;
  };
  const stopDrag = () => { gesture.current = null; setDrag(null); };
  useEffect(() => {
    const stop = () => { gesture.current = null; setDrag(null); };
    window.addEventListener('blur', stop);
    return () => window.removeEventListener('blur', stop);
  }, []);
  const hitAt = event => {
    const pct = xAt(event.clientX), time = range[0] + pct * (range[1] - range[0]);
    const checkpoints = event.target.closest?.('path[data-markers]');
    if (checkpoints) {
      const row = paths.find(item => item.id === checkpoints.dataset.markers);
      const point = row?.dots.length ? pointAt(row.dots, Math.max(row.dots[0][0], Math.min(row.dots.at(-1)[0], time))) : null;
      return { time: point?.[0] ?? time, id: checkpoints.dataset.markers };
    }
    const trace = event.target.closest?.('path[data-car]');
    if (trace) return { time, id: trace.dataset.car };
    const box = svg.current.getBoundingClientRect();
    const y = event.clientY - box.top;
    let nearest = null, distance = 14;
    for (const row of series) {
      const rank = rankAt(row.points, time, metric);
      if (!Number.isFinite(rank)) continue;
      const delta = Math.abs(positionY(rank) - y);
      if (delta < distance) { nearest = row; distance = delta; }
    }
    return { time, id: nearest?.id || '' };
  };
  const move = event => {
    const current = gesture.current;
    if (current) {
      if (!(event.buttons & 1) || event.pointerId !== current.pointerId) { stopDrag(); return; }
      setDrag({ from: current.pct, to: xAt(event.clientX) }); return;
    }
    setHover(hitAt(event));
  };
  const release = event => {
    const current = gesture.current;
    if (!current || event.pointerId !== current.pointerId) return;
    const pct = xAt(event.clientX), dx = event.clientX - current.x;
    stopDrag();
    if (Math.abs(dx) < 8) { if (Math.abs(event.clientY - current.y) < 8 && current.car) setSelected(current.car); return; }
    if (dx < 0) { zoomBack(); return; }
    const span = windowRange[1] - windowRange[0];
    const next = [windowRange[0] + current.pct * span, windowRange[0] + pct * span];
    if (next[1] - next[0] < .00001) return;
    setHistory(previous => [...previous.slice(-19), windowRange]); setWindowRange(next); setHover(null);
  };
  return <section className="race-position-workspace" aria-labelledby="race-position-title">
    <div className="race-position-heading"><div><h2 id="race-position-title"><TrendingUp size={19} />경기 순위 변화</h2><p>왼쪽 버튼 + 오른쪽 드래그: 확대 · 왼쪽 드래그: 이전 범위 · 휠: 위아래 · 하단 막대: 좌우</p></div>
      <Button variant="outline" size="sm" onClick={onClose}><ArrowLeft />분석으로 돌아가기</Button></div>
    {loading ? <div className="race-position-state" role="status">순위 기록을 불러오는 중…</div> : error ? <div className="race-position-state"><Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert><Button variant="outline" onClick={() => setAttempt(value => value + 1)}>다시 시도</Button></div>
      : payload?.status !== 'READY' ? <Empty className="race-position-state"><EmptyHeader><EmptyTitle>순위 이력이 없습니다.</EmptyTitle><EmptyDescription>{payload?.reason}</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" onClick={onClose}>분석으로 돌아가기</Button></EmptyContent></Empty> : <>
        <div className="race-position-toolbar"><Badge variant="secondary">{payload.classes?.find(item => String(item.id) === String(ownClass))?.label || '내 클래스'} · 클래스 순위</Badge>
          <Badge variant="outline">{series.length}대</Badge>
          <ToggleGroup type="multiple" size="sm" className="race-position-event-toggles" value={shownEvents} onValueChange={setShownEvents} aria-label="그래프 표시">
            <ToggleGroupItem value="events" aria-label="주행 사건 표시">주행 사건</ToggleGroupItem><ToggleGroupItem value="records" aria-label="랩 기록 표시">랩 기록</ToggleGroupItem>
          </ToggleGroup>
          <span className="race-position-range" data-testid="race-position-range">{axisLabel(range[0])} – {axisLabel(range[1])}</span>
          <Button variant="ghost" size="sm" disabled={!history.length && windowRange[0] === 0 && windowRange[1] === 1} onClick={zoomBack}>이전 범위</Button><Button variant="outline" size="sm" onClick={reset}><RotateCcw />전체 보기</Button></div>
        <div className="race-position-body"><div className={`race-position-plot${eventLanes ? ' has-events' : ''}`}>
          <div className="race-position-event-key" aria-label="사건 표시 색상">{['incident', 'pit_in', 'invalid', 'personal', 'fastest'].map(kind => <span key={kind} data-kind={kind}><i />{kind === 'pit_in' ? '피트' : EVENT_TYPES[kind].label}</span>)}</div>
          {/* HTML labels keep the app font's proportions when the SVG stretches. */}
          <div className="race-position-axis" aria-hidden="true">{ticks.map(value =>
            <span key={value} style={{ left: `${positionX(value, range) / PLOT.width * 100}%` }}>{axisLabel(value)}</span>)}</div>
          <div className="race-position-scroll" id="race-position-chart" ref={scroller} tabIndex={0} role="region" aria-label="순위 그래프 · 휠 또는 위아래 화살표로 스크롤" onKeyDown={event => { if (event.key === 'Escape') { stopDrag(); setHover(null); } }}>
            <div className="race-position-canvas">
            <div className="race-position-ranks" aria-hidden="true">{Array.from({ length: maxRank }, (_, i) =>
              <span key={i} style={{ top: positionY(i + 1) }}>{i + 1}</span>)}</div>
            <svg ref={svg} viewBox={`0 0 ${PLOT.width} ${canvasHeight}`} style={{ height: canvasHeight }} preserveAspectRatio="none" role="img" aria-label={`${isLap ? '각 차량의 완료 랩' : '기록 경과 시간'}에 따른 내 클래스 차량별 순위. 1위가 위쪽입니다.`}
              onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); const hit = hitAt(event); event.currentTarget.setPointerCapture?.(event.pointerId); gesture.current = { x: event.clientX, y: event.clientY, pct: xAt(event.clientX), pointerId: event.pointerId, car: hit.id }; setDrag({ from: xAt(event.clientX), to: xAt(event.clientX) }); }}
              onPointerMove={move} onPointerUp={release} onPointerCancel={stopDrag} onLostPointerCapture={stopDrag} onPointerLeave={() => { if (!gesture.current) setHover(null); }}>
              {Array.from({ length: maxRank }, (_, i) => <line key={i} x1={PLOT.left} x2={PLOT.width - PLOT.right} y1={positionY(i + 1)} y2={positionY(i + 1)} className="race-position-grid" />)}
              {renderedPaths.map(row => <PositionTrace key={row.id} row={row} isLap={isLap} active={row.id === highlighted} compared={row.id === compared} dimmed={Boolean(highlighted && row.id !== highlighted && row.id !== compared)} />)}
              {eventLanes && <RaceEventTraces row={selectedRow} events={events} range={range} bottom={plotBottom} />}
              {hover && <line x1={positionX(hover.time, range)} x2={positionX(hover.time, range)} y1="0" y2={positionY(maxRank) + PLOT.top} className="race-position-cursor" />}
              {drag && <rect x={positionX(range[0] + Math.min(drag.from, drag.to) * (range[1] - range[0]), range)} y="0" width={Math.abs(drag.to - drag.from) * (PLOT.width - PLOT.left - PLOT.right)} height={positionY(maxRank) + PLOT.top} className="race-position-selection" />}
            </svg></div></div>
          {eventLanes && <div className="race-position-event-strip"><RaceEventLabels row={selectedRow} events={events} range={range} bottom={0} /></div>}
          <PanBar range={windowRange} onChange={value => { setWindowRange(value); setHover(null); }} wheelPan={false} controls="race-position-chart" />
          <output className="race-position-readout">{hover ? `${axisLabel(activePoint?.[0] ?? hover.time)}${active ? ` · ${active.label}${active.own ? ' · 내 차량' : ''} · ${activePoint?.[metric] ?? '—'}위` : ''}` : selected ? `${series.find(row => row.id === selected)?.label || ''} 강조 고정 · 다른 차량을 클릭하면 변경 · 전체 보기로 해제` : '점을 찍은 완료 랩의 순위입니다. 선이나 참가 차량을 클릭하면 강조가 고정됩니다.'}</output>
        </div><aside className="race-position-legend" aria-label="차량 강조 선택"><strong>참가 차량</strong>{series.map(row => <Button key={row.id} variant={selected === row.id ? 'secondary' : 'ghost'} size="sm" aria-pressed={selected === row.id} onClick={() => { setSelected(row.id); setHover(null); }}><i style={{ background: row.color }} /><span>{row.label}</span>{row.own && <Badge variant="outline">나</Badge>}</Button>)}</aside></div>
        <p className="race-position-source">{payload.reason} · {payload.source === 'IBT' ? 'IBT 순위 채널' : payload.source === 'IBT+LOCAL_TIMING' ? 'IBT + 이 PC의 경기 타이밍 기록' : '이 PC의 경기 타이밍 기록'} · {isLap ? '가로축: 각 차량의 스타트·피니시 라인 통과 완료 랩' : '가로축: 기록 시작부터의 경과 시간'} · 선택한 차량의 확인된 사건만 표시 · 개인 베스트는 선택한 기록의 유효 랩 기준</p>
      </>}
  </section>;
}
