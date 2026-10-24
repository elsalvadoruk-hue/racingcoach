import React, { memo, useEffect, useId, useMemo, useRef, useState } from 'react';
import { PLOT, positionPath, positionX, positionY, rankAt } from './racePositionGeometry';

export const EVENT_TYPES = {
  incident: { label: '인시던트', lane: 0, symbol: '◇' },
  pit_in: { label: '피트 진입', lane: 0, symbol: '↘' },
  pit_out: { label: '피트 복귀', lane: 0, symbol: '↗' },
  invalid: { label: '무효 랩', lane: 1, symbol: '⊘' },
  personal: { label: '개인 베스트', lane: 1, symbol: '♢' },
  fastest: { label: '클래스 최고', lane: 1, symbol: '☆' },
};
const color = kind => `hsl(var(--race-event-${kind.startsWith('pit_') ? 'pit' : kind}))`;
export const eventText = event => `${EVENT_TYPES[event.kind]?.label || ''}${event.kind === 'incident' && Number.isFinite(event.delta) ? ` +${event.delta}` : ''}`;
const lapTime = seconds => Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(3).padStart(6, '0')}` : '';
export const eventTitle = event => `${event.lap}랩 · ${eventText(event)}${event.time_s ? ` · ${lapTime(event.time_s)}` : ''}${event.duration_s != null ? ` · 피트 레인 ${event.duration_s}초` : ''}${event.kind === 'invalid' ? ' · 선택한 기록의 랩 판정 기준' : ''}`;
export function chartEvents(row, shown) {
  return (row?.events || []).filter(event => EVENT_TYPES[event.kind] && Number.isFinite(event.x)
    && shown.includes(EVENT_TYPES[event.kind].lane ? 'records' : 'events'));
}

export const RaceEventTraces = memo(function RaceEventTraces({ row, events, range, bottom }) {
  const id = useId().replace(/:/g, '');
  if (!row) return null;
  return <g className="race-event-traces" pointerEvents="none" aria-hidden="true">
    {events.map((event, i) => {
      const { kind, x } = event, type = EVENT_TYPES[kind], visible = x >= range[0] && x <= range[1];
      const span = kind === 'pit_in' && Number.isFinite(event.end_x) && event.end_x > range[0] && x < range[1];
      if (!visible && !span && !(type.lane && x >= range[0] && x - 1 <= range[1])) return null;
      const rank = rankAt(row.points, x), at = positionX(x, range), y = Number.isFinite(rank) ? positionY(rank) : null;
      const laneY = bottom - 2;
      // A missing checkpoint remains a break even under a colored lap segment.
      const segment = type.lane ? positionPath(row.points.filter(p => p[0] >= x - 1 && p[0] <= x), 2, range) : '';
      return <g key={`${kind}-${event.at_s}-${i}`} style={{ '--event-color': color(kind) }} data-event-trace={kind}>
        {span && <rect x={positionX(Math.max(range[0], x), range)} y="0" width={Math.max(0, positionX(Math.min(range[1], event.end_x), range) - positionX(Math.max(range[0], x), range))} height={bottom} className="race-event-pit-span" />}
        {segment && <>
          <defs><linearGradient id={`${id}-${i}`} x1={positionX(x - 1, range)} x2={positionX(x, range)} y1="0" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0" stopColor={row.color} /><stop offset=".55" stopColor={color(kind)} /><stop offset="1" stopColor={color(kind)} /></linearGradient></defs>
          {kind === 'invalid' && <path d={segment} className="race-event-segment-mask" />}
          <path d={segment} stroke={`url(#${id}-${i})`} className={`race-event-segment ${kind === 'invalid' ? 'is-invalid' : ''}`} />
        </>}
        {visible && y !== null && <>
          <line x1={at} x2={at} y1={y + 6} y2={laneY} className="race-event-guide" />
          {kind === 'fastest' ? <path d={`M${at},${y - 5}l1.5,3.2 3.5,.5 -2.5,2.4 .6,3.5 -3.1,-1.7 -3.1,1.7 .6,-3.5 -2.5,-2.4 3.5,-.5Z`} className="race-event-symbol" /> : <circle cx={at} cy={y} r={type.lane ? 3.3 : 3} className="race-event-symbol" />}
        </>}
      </g>;
    })}
    <line x1={PLOT.left} x2={PLOT.width - PLOT.right} y1={bottom} y2={bottom} className="race-event-divider" />
  </g>;
});

export const RaceEventLabels = memo(function RaceEventLabels({ row, events, range, bottom }) {
  const root = useRef(null), [width, setWidth] = useState(1000);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const update = () => setWidth(element.clientWidth || 1000);
    update();
    if (!window.ResizeObserver) return;
    const observer = new ResizeObserver(update); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const labels = useMemo(() => {
    const plotWidth = width * (PLOT.width - PLOT.left - PLOT.right) / PLOT.width;
    const slots = Math.max(1, Math.floor(plotWidth / 140)), groups = new Map();
    for (const event of events) {
      if (event.x < range[0] || event.x > range[1]) continue;
      const lane = EVENT_TYPES[event.kind].lane;
      const slot = Math.max(0, Math.min(slots - 1, Math.floor((event.x - range[0]) / (range[1] - range[0]) * slots)));
      const key = `${lane}-${slot}`;
      if (!groups.has(key)) groups.set(key, { lane, slot, items: [] });
      groups.get(key).items.push(event);
    }
    return [...groups.values()].map(group => {
      const { lane, slot, items } = group, event = items[0], left = PLOT.left / PLOT.width * width + slot * plotWidth / slots;
      const text = items.length === 1 ? `${eventText(event)} · ${event.lap}랩` : items.every(e => e.kind.startsWith('pit_')) ? '피트 진입·복귀' : `${lane ? '랩 기록' : '사건'} ${items.length}건`;
      return { key: `${lane}-${slot}`, lane, event, text, title: items.map(eventTitle).join('\n'),
        left: Math.max(left, Math.min(left + plotWidth / slots - 126, positionX(event.x, range) / PLOT.width * width - 63)) };
    });
  }, [events, range, width]);
  return <div ref={root} className="race-event-labels" aria-label={`${row.label} 주행 사건과 랩 기록`}>
    <span className="race-event-lane" style={{ top: bottom + 8 }}>사건</span>
    <span className="race-event-lane" style={{ top: bottom + 44 }}>랩 기록</span>
    {labels.map(item => <span key={item.key} title={item.title} className="race-event-label" data-event-kind={item.event.kind}
      style={{ left: item.left, top: bottom + 8 + item.lane * 36, '--event-color': color(item.event.kind) }}><i aria-hidden="true">{EVENT_TYPES[item.event.kind].symbol}</i>{item.text}</span>)}
  </div>;
});
