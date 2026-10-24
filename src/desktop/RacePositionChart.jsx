import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, RotateCcw, TrendingUp } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription, EmptyContent } from '../components/ui/empty';
import { Alert, AlertDescription } from '../components/ui/alert';
import { PanBar } from '../components/LapOverlay';
import { labRequest } from './sessionData';
import { PLOT, pointAt, positionPath, positionX, positionY, raceClock } from './racePositionGeometry';
import './racePositions.css';

const COLORS = ['#64b5f6', '#f6bd60', '#bc8cf2', '#70d6b2', '#ed8096', '#b3cf62', '#80c7db', '#dfa0f1', '#e39462', '#a6b5ef', '#e6ce70', '#66c6ba'];
const requestHistory = (filename, signal) => labRequest('race-positions', { filename }, signal);

export default function RacePositionChart({ filename, onClose, load = requestHistory }) {
  const [payload, setPayload] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [windowRange, setWindowRange] = useState([0, 1]), [history, setHistory] = useState([]);
  const [selected, setSelected] = useState(''), [hover, setHover] = useState(null), [drag, setDrag] = useState(null);
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
    .filter(row => String(row.class_id) === String(ownClass)).map((row, i) => ({ ...row, color: COLORS[i % COLORS.length] })), [payload, ownClass]);
  const start = payload?.start_s ?? 0, end = Math.max(start + 1, payload?.end_s ?? 1), duration = end - start;
  const range = useMemo(() => [start + duration * windowRange[0], start + duration * windowRange[1]], [start, duration, windowRange]);
  const maxRank = useMemo(() => Math.max(8, ...series.map(row => row.points.reduce((max, p) => Math.max(max, p[metric] || 0), 0))), [series, metric]);
  const paths = useMemo(() => series.map(row => ({ ...row, path: positionPath(row.points, metric, range) })), [series, metric, range]);
  const highlighted = hover?.id || selected;
  const active = series.find(row => row.id === highlighted);
  const activePoint = hover && active ? pointAt(active.points, hover.time) : null;
  const reset = () => { setWindowRange([0, 1]); setHistory([]); setHover(null); };
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
  const move = event => {
    const pct = xAt(event.clientX), time = range[0] + pct * (range[1] - range[0]);
    const current = gesture.current;
    if (current) {
      if (!(event.buttons & 1) || event.pointerId !== current.pointerId) { stopDrag(); return; }
      setDrag({ from: current.pct, to: pct }); return;
    }
    const box = svg.current.getBoundingClientRect();
    const y = event.clientY - box.top;
    let nearest = null, distance = 14;
    for (const row of series) {
      const point = pointAt(row.points, time), rank = point?.[metric];
      if (!Number.isFinite(rank)) continue;
      const delta = Math.abs(positionY(rank) - y);
      if (delta < distance) { nearest = row; distance = delta; }
    }
    setHover({ time, id: nearest?.id || '' });
  };
  const release = event => {
    const current = gesture.current;
    if (!current || event.pointerId !== current.pointerId) return;
    const pct = xAt(event.clientX), dx = event.clientX - current.x;
    stopDrag();
    if (Math.abs(dx) < 8) { if (hover?.id) setSelected(value => value === hover.id ? '' : hover.id); return; }
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
          <Badge variant="outline">{series.length}대</Badge><span className="race-position-range" data-testid="race-position-range">{raceClock(range[0] - start)} – {raceClock(range[1] - start)}</span>
          <Button variant="ghost" size="sm" disabled={!history.length && windowRange[0] === 0 && windowRange[1] === 1} onClick={zoomBack}>이전 범위</Button><Button variant="outline" size="sm" onClick={reset}><RotateCcw />전체 보기</Button></div>
        <div className="race-position-body"><div className="race-position-plot">
          <svg className="race-position-axis" viewBox={`0 0 ${PLOT.width} 30`} preserveAspectRatio="none" aria-hidden="true">{Array.from({ length: 7 }, (_, i) => {
            const time = range[0] + (range[1] - range[0]) * i / 6;
            return <text key={i} x={positionX(time, range)} y="20" textAnchor="middle">{raceClock(time - start)}</text>;
          })}</svg>
          <div className="race-position-scroll" id="race-position-chart" ref={scroller} tabIndex={0} role="region" aria-label="순위 그래프 · 휠 또는 위아래 화살표로 스크롤" onKeyDown={event => { if (event.key === 'Escape') { stopDrag(); setSelected(''); } }}>
            <svg ref={svg} viewBox={`0 0 ${PLOT.width} ${positionY(maxRank) + PLOT.top}`} style={{ height: positionY(maxRank) + PLOT.top }} preserveAspectRatio="none" role="img" aria-label="기록 경과 시간에 따른 내 클래스 차량별 순위. 1위가 위쪽입니다."
              onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture?.(event.pointerId); gesture.current = { x: event.clientX, pct: xAt(event.clientX), pointerId: event.pointerId }; setDrag({ from: xAt(event.clientX), to: xAt(event.clientX) }); }}
              onPointerMove={move} onPointerUp={release} onPointerCancel={stopDrag} onLostPointerCapture={stopDrag} onPointerLeave={() => { if (!gesture.current) setHover(null); }}>
              {Array.from({ length: maxRank }, (_, i) => <g key={i}><line x1={PLOT.left} x2={PLOT.width - PLOT.right} y1={positionY(i + 1)} y2={positionY(i + 1)} className="race-position-grid" /><text x="32" y={positionY(i + 1) + 4} textAnchor="end" className="race-position-label">{i + 1}</text></g>)}
              {paths.map(row => <path key={row.id} d={row.path} fill="none" stroke={row.color} strokeWidth={row.own || row.id === highlighted ? 2.8 : 1.5} opacity={highlighted && row.id !== highlighted ? .16 : row.own || row.id === highlighted ? 1 : .72} vectorEffect="non-scaling-stroke" data-car={row.id} />)}
              {hover && <line x1={positionX(hover.time, range)} x2={positionX(hover.time, range)} y1="0" y2={positionY(maxRank) + PLOT.top} className="race-position-cursor" />}
              {drag && <rect x={positionX(range[0] + Math.min(drag.from, drag.to) * (range[1] - range[0]), range)} y="0" width={Math.abs(drag.to - drag.from) * (PLOT.width - PLOT.left - PLOT.right)} height={positionY(maxRank) + PLOT.top} className="race-position-selection" />}
            </svg></div>
          <PanBar range={windowRange} onChange={value => { setWindowRange(value); setHover(null); }} wheelPan={false} controls="race-position-chart" />
          <output className="race-position-readout">{hover ? `${raceClock(hover.time - start)}${active ? ` · ${active.label}${active.own ? ' · 내 차량' : ''} · ${activePoint?.[metric] ?? '—'}위` : ''}` : selected ? `${series.find(row => row.id === selected)?.label || ''} 강조 중 · 다시 클릭하면 해제` : '선을 가리키거나 차량을 선택하면 강조됩니다.'}</output>
        </div><aside className="race-position-legend" aria-label="차량 강조 선택"><strong>참가 차량</strong>{series.map(row => <Button key={row.id} variant={selected === row.id ? 'secondary' : 'ghost'} size="sm" aria-pressed={selected === row.id} onClick={() => { setSelected(value => value === row.id ? '' : row.id); setHover(null); }}><i style={{ background: row.color }} /><span>{row.label}</span>{row.own && <Badge variant="outline">나</Badge>}</Button>)}</aside></div>
        <p className="race-position-source">{payload.reason} · {payload.source === 'IBT' ? 'IBT 순위 채널' : '이 PC의 경기 타이밍 기록'} · 기록 시작부터의 경과 시간</p>
      </>}
  </section>;
}
