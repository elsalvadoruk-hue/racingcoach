import React, { memo, useId, useLayoutEffect, useMemo, useRef } from 'react';

const finite = Number.isFinite;
const GREEN = '#4ade80', RED = '#f87171';
const CURRENT_POSITION = .8;
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

function usePanelDrag() {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const panel = ref.current, parent = panel?.parentElement;
    if (!parent) return undefined;
    let placement = null, held = null, frame = null;
    // Keep the panel inside the map and its visible scroll/viewport boundaries.
    const ancestors = [];
    for (let element = parent.parentElement; element; element = element.parentElement) ancestors.push(element);
    const keepInside = desired => {
      const map = parent.getBoundingClientRect();
      if (!map.width || !map.height) return;
      const viewport = window.visualViewport;
      const origin = { x: map.left + parent.clientLeft - parent.scrollLeft, y: map.top + parent.clientTop - parent.scrollTop };
      let left = Math.max(map.left + parent.clientLeft, viewport?.offsetLeft ?? 0);
      let top = Math.max(map.top + parent.clientTop, viewport?.offsetTop ?? 0);
      let right = Math.min(map.left + parent.clientLeft + (parent.clientWidth || map.width), (viewport?.offsetLeft ?? 0) + (viewport?.width ?? window.innerWidth));
      let bottom = Math.min(map.top + parent.clientTop + (parent.clientHeight || map.height), (viewport?.offsetTop ?? 0) + (viewport?.height ?? window.innerHeight));
      for (const element of ancestors) {
        const style = getComputedStyle(element);
        const clipX = /auto|scroll|hidden|clip/.test(style.overflowX || style.overflow);
        const clipY = /auto|scroll|hidden|clip/.test(style.overflowY || style.overflow);
        if (!clipX && !clipY) continue;
        const box = element.getBoundingClientRect();
        if (clipX) { left = Math.max(left, box.left + element.clientLeft); right = Math.min(right, box.left + element.clientLeft + (element.clientWidth || box.width)); }
        if (clipY) { top = Math.max(top, box.top + element.clientTop); bottom = Math.min(bottom, box.top + element.clientTop + (element.clientHeight || box.height)); }
      }
      if (right - left < 300 || bottom - top < 120) {
        panel.style.visibility = 'hidden';
        finish({});
        return;
      }
      panel.style.visibility = 'visible';
      const margin = Math.min(10, (right - left) / 4, (bottom - top) / 4);
      panel.style.width = `${Math.min(370, right - left - margin * 2)}px`;
      panel.style.maxHeight = `${bottom - top - margin * 2}px`;
      const box = panel.getBoundingClientRect();
      const minX = left + margin - origin.x, maxX = right - margin - box.width - origin.x;
      const minY = top + margin - origin.y, maxY = bottom - margin - box.height - origin.y;
      placement = { x: clamp(desired?.x ?? maxX, minX, maxX), y: clamp(desired?.y ?? maxY, minY, maxY) };
      panel.style.left = `${placement.x}px`; panel.style.top = `${placement.y}px`;
      panel.style.right = 'auto'; panel.style.bottom = 'auto';
    };
    const recover = () => {
      if (frame !== null) return;
      frame = requestAnimationFrame(() => { frame = null; keepInside(placement); });
    };
    const stop = event => event.stopPropagation();
    const finish = event => {
      if (!held || (event.pointerId != null && event.pointerId !== held.id)) return;
      const id = held.id;
      held = null;
      panel.style.cursor = 'grab';
      if (panel.hasPointerCapture?.(id)) panel.releasePointerCapture(id);
    };
    const down = event => {
      stop(event);
      if (event.button !== 0 || event.isPrimary === false || held) return;
      event.preventDefault();
      keepInside(placement);
      if (panel.style.visibility === 'hidden') return;
      const box = panel.getBoundingClientRect();
      held = { id: event.pointerId, x: event.clientX - box.left, y: event.clientY - box.top };
      panel.focus({ preventScroll: true });
      panel.setPointerCapture?.(event.pointerId);
      panel.style.cursor = 'grabbing';
    };
    const move = event => {
      stop(event);
      if (!held || held.id !== event.pointerId) return;
      const map = parent.getBoundingClientRect();
      keepInside({ x: event.clientX - held.x - map.left - parent.clientLeft + parent.scrollLeft,
        y: event.clientY - held.y - map.top - parent.clientTop + parent.scrollTop });
    };
    const key = event => {
      const offsets = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      if (event.key === 'Escape') { finish(event); return; }
      if (event.key !== 'Home' && !offsets[event.key]) return;
      event.preventDefault(); stop(event);
      if (event.key === 'Home') keepInside(null);
      else {
        const [x, y] = offsets[event.key], step = event.shiftKey ? 30 : 10;
        keepInside({ x: (placement?.x ?? 0) + x * step, y: (placement?.y ?? 0) + y * step });
      }
    };
    const listeners = { pointerdown: down, pointermove: move, pointerup: finish, pointercancel: finish, lostpointercapture: finish,
      click: stop, dblclick: stop, keydown: key };
    for (const [type, handler] of Object.entries(listeners)) panel.addEventListener(type, handler);
    window.addEventListener('resize', recover);
    window.addEventListener('scroll', recover, true);
    window.addEventListener('blur', finish);
    window.visualViewport?.addEventListener('resize', recover);
    window.visualViewport?.addEventListener('scroll', recover);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(recover);
    [panel, parent, ...ancestors].forEach(element => observer?.observe(element));
    keepInside(null);
    return () => {
      finish({});
      for (const [type, handler] of Object.entries(listeners)) panel.removeEventListener(type, handler);
      window.removeEventListener('resize', recover);
      window.removeEventListener('scroll', recover, true);
      window.removeEventListener('blur', finish);
      window.visualViewport?.removeEventListener('resize', recover);
      window.visualViewport?.removeEventListener('scroll', recover);
      observer?.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, []);
  return ref;
}

const validGear = value => Number.isInteger(value) && value >= -1 && value <= 20;
const gear = value => !validGear(value) ? '—' : value === 0 ? 'N' : value === -1 ? 'R' : value;
// Match the game overlay: compare only measured forward gears.
function gearComparison(current, reference) {
  if (!validGear(current) || !validGear(reference) || current <= 0 || reference <= 0) return { color: '#bed0e6', label: '비교 불가' };
  return current < reference ? { color: '#ff4040', label: '기준보다 낮음' }
    : current > reference ? { color: '#4dff6a', label: '기준보다 높음' } : { color: '#f6faff', label: '기준과 같음' };
}
const point = (angle, radius) => {
  const radians = -angle * Math.PI / 180;
  return [48 + Math.sin(radians) * radius, 48 - Math.cos(radians) * radius];
};
function arc(angle) {
  const bounded = Math.max(-359.99, Math.min(359.99, angle));
  const [x, y] = point(bounded, 43);
  return `M48 5 A43 43 0 ${Math.abs(bounded) > 180 ? 1 : 0} ${bounded < 0 ? 1 : 0} ${x} ${y}`;
}

// Static paths are prepared once per trace. Moving the viewport and cursor does
// not rebuild thousands of points or redraw the map's imagery every frame.
function pedalPath(rows, key, area = false) {
  let path = '', connected = false, first = null, last = null;
  const close = () => {
    if (area && connected) path += `L${last} 100 H${first} Z `;
    connected = false;
  };
  for (const row of rows) {
    if (row.breakBefore) close();
    if (!finite(row[key]) || !finite(row.distance_pct)) { close(); continue; }
    const x = (row.distance_pct * 10000).toFixed(2);
    if (!connected) first = x;
    path += `${connected ? 'L' : 'M'}${x} ${100 - Math.max(0, Math.min(100, row[key]))} `;
    connected = true; last = x;
  }
  close();
  return path;
}
const PedalPaths = memo(function PedalPaths({ paths }) {
  return <>
    <path d={paths[2]} fill={GREEN} fillOpacity=".16" stroke={GREEN} strokeOpacity=".4" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
    <path d={paths[3]} fill={RED} fillOpacity=".16" stroke={RED} strokeOpacity=".4" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
    <path d={paths[0]} fill="none" stroke={GREEN} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
    <path d={paths[1]} fill="none" stroke={RED} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
  </>;
});

function MapInputs({ rows, values, pct, range, angleAvailable = true, aggregate = false }) {
  const panel = usePanelDrag();
  const hintId = useId();
  const gearText = useRef(null);
  const gearLabel = gear(values?.gear);
  useLayoutEffect(() => {
    const text = gearText.current;
    if (!text || typeof CanvasRenderingContext2D === 'undefined') return;
    let active = true;
    const center = () => {
      if (!active) return;
      const style = getComputedStyle(text);
      const context = document.createElement('canvas').getContext('2d');
      if (!context) return;
      context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      const ink = context.measureText(String(gearLabel));
      if (![ink.width, ink.actualBoundingBoxAscent, ink.actualBoundingBoxDescent,
        ink.actualBoundingBoxLeft, ink.actualBoundingBoxRight].every(finite)) return;
      // Center the visible glyph, not the font's line box or alphabetic baseline.
      text.setAttribute('x', 48 + (ink.width + ink.actualBoundingBoxLeft - ink.actualBoundingBoxRight) / 2);
      text.setAttribute('y', 48 + (ink.actualBoundingBoxAscent - ink.actualBoundingBoxDescent) / 2);
    };
    center();
    document.fonts?.ready.then(center);
    document.fonts?.addEventListener('loadingdone', center);
    return () => { active = false; document.fonts?.removeEventListener('loadingdone', center); };
  }, [gearLabel]);
  const paths = useMemo(() => ['throttle', 'brake', 'referenceThrottle', 'referenceBrake'].map((key, index) => pedalPath(rows, key, index > 1)), [rows]);
  const from = range?.[0] ?? rows[0]?.distance_pct ?? 0;
  const to = range?.[1] ?? rows[rows.length - 1]?.distance_pct ?? 1;
  const cursor = finite(pct) && pct >= from && pct <= to ? pct * 10000 : null;
  // Keep the current point 20% from the right. Only the viewport moves; both drivers
  // keep the same distance axis and the memoized paths stay unchanged.
  const width = Math.max(.001, Math.min(.08, to - from)) * 10000;
  const left = (finite(cursor) ? cursor : from * 10000) - width * CURRENT_POSITION;
  const right = left + width;
  const own = angleAvailable && finite(values?.steering) ? values.steering : null;
  const reference = angleAvailable && finite(values?.referenceSteering) ? values.referenceSteering : null;
  const refFrom = finite(reference) ? point(reference, 36) : null;
  const refTo = finite(reference) ? point(reference, 47) : null;
  const comparedGear = gearComparison(values?.gear, values?.referenceGear);
  return <div ref={panel} data-testid="map-inputs" data-position={finite(pct) ? pct : ''} className="absolute bottom-2.5 right-2.5 touch-none select-none overflow-hidden rounded-xl border border-white/15 p-2 text-white shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/60"
    style={{ width: 'min(370px, calc(100% - 20px))', background: 'rgba(12,18,23,.91)', cursor: 'grab' }} tabIndex={0}
    role="group" aria-label="같은 위치 조향·기어·페달 비교" aria-describedby={hintId}>
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1">
        <svg viewBox={`${left} -5 ${width} 110`} preserveAspectRatio="none" className="block h-16 w-full overflow-hidden rounded bg-white/5" aria-hidden="true" data-testid="map-input-pedals">
          <path d={`M${left} 50 H${right}`} stroke="white" strokeOpacity=".1" vectorEffect="non-scaling-stroke" />
          <PedalPaths paths={paths} />
          {finite(cursor) && <line x1={cursor} x2={cursor} y1="-5" y2="105" stroke="white" strokeWidth="3" vectorEffect="non-scaling-stroke" data-testid="map-input-cursor" />}
        </svg>
      </div>
      <div className="shrink-0 text-center tabular-nums" style={{ width: 'min(80px, 24%)' }}>
        <svg viewBox="0 0 96 96" className="block aspect-square w-full" aria-hidden="true" data-testid="map-input-steering">
          <circle cx="48" cy="48" r="43" fill="none" stroke="white" strokeOpacity=".18" strokeWidth="5" />
          <line x1="48" y1="2" x2="48" y2="10" stroke="white" strokeOpacity=".35" />
          {finite(own) && <path d={arc(own)} fill="none" stroke={GREEN} strokeWidth="5" strokeLinecap="round" data-testid="map-input-own-steering" />}
          {refFrom && <line x1={refFrom[0]} y1={refFrom[1]} x2={refTo[0]} y2={refTo[1]} stroke="white" strokeWidth="3" data-testid="map-input-reference-steering" />}
          <text ref={gearText} x="48" y="64" textAnchor="middle" fill={comparedGear.color} fontSize="48" fontWeight="700" data-testid="map-input-gear">{gearLabel}</text>
        </svg>
        <span className="sr-only">내 기어 {gear(values?.gear)} · {comparedGear.label}</span>
      </div>
    </div>
    <span id={hintId} className="sr-only">끌어서 이동. 방향키로 이동, Home으로 오른쪽 아래 복귀.
      기준은 면, 내 입력은 선. 흰 선은 오른쪽에서 20%인 현재 위치이며 그래프는 왼쪽으로 흐릅니다.
      {!angleAvailable && 'LMU 핸들 회전각 미제공.'}
      {aggregate && '입력은 여러 랩 종합 기준이며 지도 점은 대표 실제 랩입니다.'}</span>
  </div>;
}

export default memo(MapInputs);
