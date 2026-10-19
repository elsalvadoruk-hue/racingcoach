import React, { useEffect, useMemo, useState } from 'react';

// One corner as a graph a beginner can read: this lap (solid) against the reference lap (dashed) for
// speed, brake and throttle, with numbered notes placed where the two laps did something differently.
// Every note comes from the corner's measured points (analysis corner.target / corner.reference); a point
// missing on either lap gives no note. The dense trace is fetched for the corner window only.
const BEFORE_M = 120;
const AFTER_M = 80;
const DISTANCE_NOTE_M = 3;      // smaller differences than this are within the grid spacing / noise
const SPEED_NOTE_KPH = 1;

// Root cause (closed-loop coach / practice) -> the note that is the focus.
export const CAUSE_EVENT = {
  BRAKE_POINT_EARLY: 'brake_start',
  BRAKE_RELEASE_LATE: 'brake_release',
  OVER_BRAKING_ABS: 'brake_release',
  THROTTLE_RETURN_LATE: 'full_throttle',
  EXIT_INSTABILITY: 'full_throttle',
  EXCESS_COASTING: 'brake_release',
  ROTATION_DEFICIT: 'min_speed',
};

const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const wrap = (value) => ((value % 1) + 1.5) % 1 - 0.5;           // signed lap-fraction difference

export function cornerNotes(corner, trackLength) {
  const t = corner?.target || {};
  const r = corner?.reference || {};
  if (!corner?.reference) return [];
  const notes = [];
  const place = (key, label, later, earlier) => {
    const a = t[`${key}_pct`];
    const b = r[`${key}_pct`];
    if (!finite(a) || !finite(b) || !(trackLength > 0)) return;
    const metres = wrap(a - b) * trackLength;
    if (Math.abs(metres) < DISTANCE_NOTE_M) return;
    notes.push({ key, label, at: a, refAt: b, metres,
      text: `${label}: 기준보다 ${Math.round(Math.abs(metres))}m ${metres > 0 ? later : earlier}` });
  };
  place('brake_start', '제동 시작', '늦게 밟았습니다.', '일찍 밟았습니다.');
  place('brake_release', '브레이크 놓음', '늦게 놓았습니다.', '일찍 놓았습니다.');
  place('full_throttle', '풀스로틀', '늦게 열었습니다.', '일찍 열었습니다.');
  const speed = (key, label, at) => {
    if (!finite(t[key]) || !finite(r[key])) return;
    const diff = t[key] - r[key];
    if (Math.abs(diff) < SPEED_NOTE_KPH) return;
    notes.push({ key: key.replace('_kph', ''), label, at, speed: true,
      text: `${label}: 기준보다 ${Math.abs(diff).toFixed(1)}km/h ${diff < 0 ? '낮습니다.' : '높습니다.'}` });
  };
  speed('min_speed_kph', '최저 속도', corner.apex_pct);
  speed('exit_speed_kph', '탈출 속도', corner.exit_pct);
  return notes.sort((a, b) => wrap(a.at - corner.entry_pct) - wrap(b.at - corner.entry_pct));
}

function segments(rows, x, y, key) {
  const out = [];
  let current = '';
  rows.forEach((row) => {
    const value = row[key];
    if (!finite(value)) { if (current) out.push(current); current = ''; return; }
    current += `${current ? 'L' : 'M'}${x(row).toFixed(1)} ${y(value).toFixed(1)}`;
  });
  if (current) out.push(current);
  return out;
}

const W = 760;
const LANES = { speed: [14, 150], brake: [168, 214], throttle: [232, 278] };
const H = 300;

export default function CornerTrace({ corner, trackLength, load, focusEvent, referenceLabel = '기준 랩' }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState('');
  const length = trackLength > 0 ? trackLength : null;
  const range = useMemo(() => {
    if (!corner || !length || !finite(corner.entry_pct) || !finite(corner.exit_pct)) return null;
    const start = corner.entry_pct - BEFORE_M / length;
    const span = Math.min(1, ((corner.exit_pct - corner.entry_pct + 1) % 1) + (BEFORE_M + AFTER_M) / length);
    return { start: ((start % 1) + 1) % 1, span };
  }, [corner, length]);

  useEffect(() => {
    let live = true;
    setRows(null); setError('');
    if (!range || !load) return undefined;
    const end = range.start + range.span;
    const parts = end <= 1 ? [[range.start, end]] : [[range.start, 1], [0, end - 1]];
    Promise.all(parts.map(([a, b]) => load(a, b)))
      .then((pieces) => { if (live) setRows(pieces.flat()); })
      .catch((failure) => { if (live) setError(failure?.message || '그래프 자료를 불러오지 못했습니다.'); });
    return () => { live = false; };
  }, [range, load]);

  const notes = useMemo(() => cornerNotes(corner, length), [corner, length]);
  if (!corner) return null;
  if (!length) return <p className="text-sm text-muted-foreground">트랙 길이를 몰라 그래프를 그리지 않습니다.</p>;
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!rows) return <p className="text-sm text-muted-foreground">{corner.label} 그래프를 불러오는 중입니다…</p>;

  const offset = (pct) => ((((pct - range.start) % 1) + 1) % 1) * length;
  const total = range.span * length;
  const x = (row) => 40 + (offset(row.distance_pct) / total) * (W - 56);
  const xp = (pct) => 40 + (offset(pct) / total) * (W - 56);
  const speeds = rows.flatMap((row) => [row.target_speed_kph, row.reference_speed_kph]).filter(finite);
  if (!speeds.length) return <p className="text-sm text-muted-foreground">이 구간의 속도 기록이 없습니다.</p>;
  const lo = Math.floor(Math.min(...speeds) / 10) * 10;
  const hi = Math.ceil(Math.max(...speeds) / 10) * 10;
  const ySpeed = (v) => LANES.speed[1] - ((v - lo) / Math.max(1, hi - lo)) * (LANES.speed[1] - LANES.speed[0]);
  const lane = (name) => (v) => LANES[name][1] - Math.max(0, Math.min(1, v)) * (LANES[name][1] - LANES[name][0]);
  const hasReference = rows.some((row) => finite(row.reference_speed_kph));
  const draw = (key, y, className, dashed) => segments(rows, x, y, key).map((d, i) => (
    <path key={`${key}-${i}`} d={d} fill="none" stroke="currentColor" className={className} strokeWidth={dashed ? 1.6 : 2.2} strokeDasharray={dashed ? '5 4' : undefined} />
  ));
  const speedAt = (pct) => {
    const near = rows.reduce((best, row) => (Math.abs(wrap(row.distance_pct - pct)) < Math.abs(wrap(best.distance_pct - pct)) ? row : best), rows[0]);
    return near.target_speed_kph;
  };

  return (
    <div className="flex flex-col gap-3" data-testid="corner-trace">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg border border-border bg-muted/20" role="img" aria-label={`${corner.label} 속도·브레이크·스로틀 그래프`}>
        {[['speed', `속도 ${lo}~${hi}`], ['brake', '브레이크'], ['throttle', '스로틀']].map(([name, label]) => (
          <g key={name}>
            <line x1="40" x2={W - 16} y1={LANES[name][1]} y2={LANES[name][1]} stroke="currentColor" className="text-border" />
            <text x="4" y={LANES[name][0] + 10} fontSize="10" fill="currentColor" className="text-muted-foreground">{label}</text>
          </g>
        ))}
        {[['진입', corner.entry_pct], ['정점', corner.apex_pct], ['탈출', corner.exit_pct]].map(([label, pct]) => finite(pct) && (
          <g key={label}>
            <line x1={xp(pct)} x2={xp(pct)} y1={LANES.speed[0]} y2={LANES.throttle[1]} stroke="currentColor" className="text-border" strokeDasharray="2 4" />
            <text x={xp(pct) + 3} y={H - 6} fontSize="10" fill="currentColor" className="text-muted-foreground">{label}</text>
          </g>
        ))}
        {hasReference && draw('reference_speed_kph', ySpeed, 'text-muted-foreground', true)}
        {draw('target_speed_kph', ySpeed, 'text-primary', false)}
        {hasReference && draw('reference_brake', lane('brake'), 'text-muted-foreground', true)}
        {draw('target_brake', lane('brake'), 'text-destructive', false)}
        {hasReference && draw('reference_throttle', lane('throttle'), 'text-muted-foreground', true)}
        {draw('target_throttle', lane('throttle'), 'text-success', false)}
        {notes.map((note, i) => {
          const focus = note.key === focusEvent;
          const tone = focus ? 'text-warning' : 'text-foreground';
          if (note.speed) {
            const v = speedAt(note.at);
            if (!finite(v)) return null;
            return (
              <g key={note.key} className={tone}>
                <circle cx={xp(note.at)} cy={ySpeed(v)} r="4" fill="currentColor" />
                <circle cx={xp(note.at)} cy={ySpeed(v) - 16} r="8" fill="currentColor" />
                <text x={xp(note.at)} y={ySpeed(v) - 12.5} textAnchor="middle" fontSize="10" fontWeight="700" fill="currentColor" className="text-background">{i + 1}</text>
              </g>
            );
          }
          const laneName = note.key === 'full_throttle' ? 'throttle' : 'brake';
          const [top, bottom] = LANES[laneName];
          const a = xp(note.at);
          const b = xp(note.refAt);
          return (
            <g key={note.key} className={tone}>
              <line x1={b} x2={b} y1={top - 4} y2={bottom} stroke="currentColor" strokeDasharray="3 3" strokeWidth="1.2" />
              <line x1={a} x2={a} y1={top - 4} y2={bottom} stroke="currentColor" strokeWidth={focus ? 2.4 : 1.6} />
              <line x1={Math.min(a, b)} x2={Math.max(a, b)} y1={top - 4} y2={top - 4} stroke="currentColor" strokeWidth={focus ? 2.4 : 1.6} />
              <circle cx={(a + b) / 2} cy={top - 4} r="8" fill="currentColor" />
              <text x={(a + b) / 2} y={top - 0.5} textAnchor="middle" fontSize="10" fontWeight="700" fill="currentColor" className="text-background">{i + 1}</text>
            </g>
          );
        })}
      </svg>
      <p className="text-xs text-muted-foreground">
        실선 = 이번 랩{hasReference ? ` · 점선 = ${referenceLabel}` : ''} · 위에서부터 <span className="text-primary">속도</span>, <span className="text-destructive">브레이크</span>, <span className="text-success">스로틀</span> · 오른쪽이 진행 방향 · 번호는 아래 설명과 같습니다
      </p>
      {!corner.reference ? (
        <p className="text-sm text-muted-foreground">기준 랩이 없어 차이를 표시하지 않습니다.</p>
      ) : notes.length ? (
        <ol className="flex flex-col gap-1 text-sm" data-testid="corner-trace-notes">
          {notes.map((note, i) => (
            <li key={note.key} className={note.key === focusEvent ? 'font-semibold text-warning' : 'text-foreground'}>
              <span className="mr-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-muted text-xs font-bold text-foreground">{i + 1}</span>
              {note.text}{note.key === focusEvent ? ' ← 오늘의 과제' : ''}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-muted-foreground">제동·풀스로틀 지점과 속도가 기준과 거의 같습니다(3m·1km/h 안).</p>
      )}
    </div>
  );
}
