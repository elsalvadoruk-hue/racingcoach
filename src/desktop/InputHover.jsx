import React, { memo, useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import wheelImage from '../assets/steering-wheel.png';

const supported = new Set(['steering', 'brake', 'throttle', 'pedals']);
const finite = Number.isFinite;

// Use the same nearest measured row as the graph readout; never extrapolate.
function measuredRow(rows, distance, from, to) {
  if (!finite(distance) || distance < from || distance > to || !rows.length
    || distance < rows[0].distance || distance > rows[rows.length - 1].distance) return null;
  let lo = 0, hi = rows.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].distance <= distance) lo = mid; else hi = mid;
  }
  return Math.abs(rows[lo].distance - distance) <= Math.abs(rows[hi].distance - distance) ? rows[lo] : rows[hi];
}

function ringPoint(angle, radius) {
  const radians = -angle * Math.PI / 180;
  return [35 + Math.sin(radians) * radius, 35 - Math.cos(radians) * radius];
}

function angleArc(angle) {
  const remaining = angle % 360;
  if (!remaining) return '';
  const [x, y] = ringPoint(remaining, 33);
  return `M35,2 A33,33 0 ${Math.abs(remaining) > 180 ? 1 : 0},${remaining < 0 ? 1 : 0} ${x},${y}`;
}

function Steering({ row, unit }) {
  const clip = useId();
  const own = row?.steering, reference = row?.referenceSteering;
  // LMU's plotted % is not a measured wheel angle. Keep the wheel neutral there.
  const angle = unit === '°' && finite(own) ? own : null;
  const referenceAngle = unit === '°' && finite(reference) ? reference : null;
  const mark = finite(angle) ? ringPoint(angle, 33) : null;
  const refFrom = finite(referenceAngle) ? ringPoint(referenceAngle, 29) : null;
  const refTo = finite(referenceAngle) ? ringPoint(referenceAngle, 36) : null;
  return <svg viewBox="0 0 70 70" width="70" height="70" aria-hidden="true" data-testid="hover-steering">
    <defs><clipPath id={clip}><circle cx="35" cy="35" r="28" /></clipPath></defs>
    <circle cx="35" cy="35" r="34" className="input-wheel-background" />
    <g clipPath={`url(#${clip})`}>
      <image href={wheelImage} x="5" y="5" width="60" height="60" transform={`rotate(${finite(angle) ? -angle : 0} 35 35)`} data-testid="hover-wheel-image" />
    </g>
    <circle cx="35" cy="35" r="33" className="input-wheel-ring" />
    <line x1="35" y1="1" x2="35" y2="5" className="input-wheel-zero" />
    {finite(angle) && <><path d={angleArc(angle)} className="input-wheel-own" />
      <circle cx={mark[0]} cy={mark[1]} r="2" className="input-wheel-own-dot" /></>}
    {refFrom && <line x1={refFrom[0]} y1={refFrom[1]} x2={refTo[0]} y2={refTo[1]} className="input-reference-mark" data-testid="hover-steering-reference" />}
    <text x="35" y="38" textAnchor="middle" className="input-wheel-value">{finite(own) ? `${Math.round(own)}${unit}` : '—'}</text>
  </svg>;
}

function Pedal({ row, kind }) {
  const own = row?.[kind], reference = row?.[kind === 'brake' ? 'referenceBrake' : 'referenceThrottle'];
  const fill = finite(own) ? Math.max(0, Math.min(100, own)) / 100 * 54 : null;
  const referenceY = finite(reference) ? 3 + (1 - Math.max(0, Math.min(100, reference)) / 100) * 54 : null;
  return <svg viewBox="0 0 30 70" width="30" height="70" aria-hidden="true" data-pedal={kind}>
    <rect x="6" y="2" width="18" height="56" rx="3" className="input-pedal-track" />
    {finite(fill) && <rect x="7" y={57 - fill} width="16" height={fill} rx="2" className={`input-pedal-fill input-pedal-${kind}`} data-testid={`hover-${kind}-fill`} />}
    {finite(referenceY) && <line x1="3" x2="27" y1={referenceY} y2={referenceY} className="input-reference-mark" data-testid={`hover-${kind}-reference`} />}
    <text x="15" y="68" textAnchor="middle" className="input-pedal-value">{kind === 'brake' ? 'B' : 'T'} {finite(own) ? Math.round(own) : '—'}</text>
  </svg>;
}

function description(row, key, unit) {
  const reading = (value, suffix) => finite(value) ? `${Math.round(value)}${suffix}` : '확인 불가';
  const pair = (name, own, reference, suffix) => `${name} 내 랩 ${reading(own, suffix)}, 비교 기준 ${reading(reference, suffix)}`;
  if (key === 'steering') return pair('조향', row?.steering, row?.referenceSteering, unit) + (unit === '%' ? ', 핸들 회전각 확인 불가' : '');
  return (key === 'pedals' ? ['brake', 'throttle'] : [key]).map(kind => pair(kind === 'brake' ? '브레이크' : '스로틀', row?.[kind], row?.[kind === 'brake' ? 'referenceBrake' : 'referenceThrottle'], '%')).join(' · ');
}

function InputHover({ host, rows, channelKey, unit, range, width, height }) {
  const [point, setPoint] = useState(null);
  const enabled = supported.has(channelKey);
  const from = range[0], to = range[1];
  useEffect(() => {
    setPoint(null);
    if (!enabled) return undefined;
    const node = host.current;
    let pending = null, frame = null;
    const hide = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null; pending = null; setPoint(null);
    };
    const move = event => {
      if (event.pointerType === 'touch' || event.buttons || event.target.closest('[role="button"]')) { hide(); return; }
      const rect = node.getBoundingClientRect();
      if (!rect.width || !rect.height) { hide(); return; }
      const x = (event.clientX - rect.left) * width / rect.width;
      const y = (event.clientY - rect.top) * height / rect.height;
      if (x < 40 || x > width - 12 || y < 4 || y > Math.max(20, height - 20) + 4) { hide(); return; }
      pending = { x: event.clientX, y: event.clientY, distance: from + (x - 40) / (width - 52) * (to - from) };
      if (frame === null) frame = requestAnimationFrame(() => { frame = null; setPoint(pending); });
    };
    const key = event => { if (event.key === 'Escape') hide(); };
    node.addEventListener('pointermove', move);
    for (const name of ['pointerleave', 'pointerdown', 'pointercancel', 'lostpointercapture']) node.addEventListener(name, hide);
    window.addEventListener('blur', hide);
    window.addEventListener('resize', hide);
    window.addEventListener('scroll', hide, true);
    document.addEventListener('visibilitychange', hide);
    document.addEventListener('focusin', hide);
    document.addEventListener('keydown', key);
    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
      node.removeEventListener('pointermove', move);
      for (const name of ['pointerleave', 'pointerdown', 'pointercancel', 'lostpointercapture']) node.removeEventListener(name, hide);
      window.removeEventListener('blur', hide);
      window.removeEventListener('resize', hide);
      window.removeEventListener('scroll', hide, true);
      document.removeEventListener('visibilitychange', hide);
      document.removeEventListener('focusin', hide);
      document.removeEventListener('keydown', key);
    };
  }, [host, rows, enabled, channelKey, unit, from, to, width, height]);

  if (!point || !enabled) return null;
  const row = measuredRow(rows, point.distance, from, to);
  if (!row) return null;
  const keys = channelKey === 'pedals' ? ['brake', 'throttle'] : [channelKey];
  if (!keys.some(key => finite(row[key]) || finite(row[{ steering: 'referenceSteering', brake: 'referenceBrake', throttle: 'referenceThrottle' }[key]]))) return null;
  const boxWidth = channelKey === 'steering' ? 70 : keys.length * 30;
  const place = (coordinate, span, viewport) => Math.max(4, Math.min(viewport - span - 4, coordinate + 9 + span <= viewport - 4 ? coordinate + 9 : coordinate - span - 9));
  return createPortal(<div className="input-hover" role="img" aria-label={description(row, channelKey, unit)} data-testid="input-hover" data-channel={channelKey}
    style={{ left: place(point.x, boxWidth, window.innerWidth), top: place(point.y, 70, window.innerHeight), width: boxWidth, height: 70 }}>
    {channelKey === 'steering' ? <Steering row={row} unit={unit} /> : keys.map(kind => <Pedal key={kind} kind={kind} row={row} />)}
  </div>, document.body);
}

export default memo(InputHover);
