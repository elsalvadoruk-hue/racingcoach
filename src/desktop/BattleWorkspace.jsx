import React, { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Pause, Play } from 'lucide-react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from '../components/ui/empty';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { cn } from '../lib/utils';
import { BATTLE_REASONS, OUTCOMES, VERDICTS, useBattleReport } from './battleReport';
import './battle.css';

const RacePositionChart = lazy(() => import('./RacePositionChart'));

const fmt = (v, d = 1, sign = false) => v == null || !Number.isFinite(v) ? '—' : `${sign && v > 0 ? '+' : ''}${v.toFixed(d)}`;
const brakeAt = v => v == null ? '제동 안 함' : v < 0 ? `입구 ${(-v).toFixed(0)}m 전` : `입구 ${v.toFixed(0)}m 지나서`;
const throttleAt = v => v == null ? '—' : v < 0 ? `정점 ${(-v).toFixed(0)}m 전` : `정점 ${v.toFixed(0)}m 뒤`;
const gapText = g => g == null ? '—' : g > .05 ? `상대가 ${g.toFixed(1)}m 앞` : g < -.05 ? `내가 ${(-g).toFixed(1)}m 앞` : '나란히';
const kph = v => v == null ? '—' : `${v.toFixed(0)} km/h`;
const SIDE = { inside: '안쪽', outside: '바깥쪽' };

function useSize() {
  const ref = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, size];
}

function refAt(geometry, pct, lat) {
  const n = geometry.grid, f = (((pct % 1) + 1) % 1) * n, i = Math.floor(f) % n, j = (i + 1) % n, w = f - Math.floor(f);
  const r = geometry.ref, q = geometry.normal;
  const nx0 = q[i][0] + (q[j][0] - q[i][0]) * w, ny0 = q[i][1] + (q[j][1] - q[i][1]) * w, l = Math.hypot(nx0, ny0) || 1;
  const nx = nx0 / l, ny = ny0 / l;
  return { x: r[i][0] + (r[j][0] - r[i][0]) * w + nx * lat, y: r[i][1] + (r[j][1] - r[i][1]) * w + ny * lat, hx: ny, hy: -nx };
}
function headingAt(frames, i) {
  const a = frames[Math.max(i - 2, 0)], b = frames[Math.min(i + 2, frames.length - 1)];
  const dx = b.me[0] - a.me[0], dy = b.me[1] - a.me[1];
  return Math.hypot(dx, dy) > .5 ? Math.atan2(dy, dx) : null;
}

function BattleMap({ report, scene, index, zoom }) {
  const [ref, { width, height }] = useSize();
  const frame = scene.frames[index], { geometry, assumptions } = report;
  const W = width || 600, H = height || 360, scale = Math.min(W, H) / (zoom * 2);
  const heading = headingAt(scene.frames, index) ?? 0, rot = -(heading - Math.PI / 2);
  const [cx, cy] = frame.me;
  const P = (x, y) => { const dx = x - cx, dy = y - cy; return [W / 2 + (dx * Math.cos(rot) - dy * Math.sin(rot)) * scale, H * .62 - (dx * Math.sin(rot) + dy * Math.cos(rot)) * scale]; };
  const poly = points => points.map(p => P(p[0], p[1]).map(v => v.toFixed(1)).join(',')).join(' ');
  const near = p => Math.hypot(p[0] - cx, p[1] - cy) < zoom * 3;
  const band = geometry.band_left.filter(near).concat(geometry.band_right.filter(near).reverse());
  const [lo, hi] = frame.rival_lat, narrow = frame.beside != null;
  const a = refAt(geometry, frame.rival_pct, lo), b = refAt(geometry, frame.rival_pct, hi), c = refAt(geometry, frame.rival_pct, (lo + hi) / 2);
  const [ax, ay] = P(a.x, a.y), [bx, by] = P(b.x, b.y);
  const trail = scene.frames.slice(Math.max(0, index - 40), index + 1);
  const rivalTrail = trail.map(q => { const r = refAt(geometry, q.rival_pct, (q.rival_lat[0] + q.rival_lat[1]) / 2); return [r.x, r.y]; });
  const car = (x, y, h) => {
    const L = assumptions.car_length_m, Wd = assumptions.car_width_m, ch = Math.cos(h), sh = Math.sin(h);
    return poly([[L / 2, Wd / 2], [L / 2, -Wd / 2], [-L / 2, -Wd / 2], [-L / 2, Wd / 2]].map(([u, v]) => [x + u * ch - v * sh, y + u * sh + v * ch]));
  };
  const nose = (x, y, h) => { const L = assumptions.car_length_m / 2; const [x1, y1] = P(x, y), [x2, y2] = P(x + L * Math.cos(h), y + L * Math.sin(h)); return { x1, y1, x2, y2 }; };
  // Cars this close run nearly parallel; the reference tangent can be off by the line choice.
  const rivalHeading = Math.abs(frame.gap_m) < 8 ? heading : Math.atan2(c.hy, c.hx);
  return <div className="battle-map" ref={ref}>
    <svg role="img" aria-label="내 차 중심의 배틀 장면" width={W} height={H}>
      <polygon points={poly(band)} className="battle-band" />
      <polyline points={poly(geometry.ref.filter(near))} className="battle-reference-line" />
      <polyline points={poly(trail.map(q => q.me))} className="battle-trail-me" />
      <line x1={ax} y1={ay} x2={bx} y2={by} className={cn('battle-rival-range', narrow && 'is-measured')} strokeWidth={Math.max(6, assumptions.car_length_m * scale * .9)} />
      <polyline points={poly(rivalTrail)} className="battle-trail-rival" />
      <polygon points={car(c.x, c.y, rivalHeading)} className={cn('battle-car-rival', !narrow && 'is-estimated')} />
      <line {...nose(c.x, c.y, rivalHeading)} className="battle-car-nose" />
      <polygon points={car(cx, cy, heading)} className="battle-car-me" />
      <line {...nose(cx, cy, heading)} className="battle-car-nose" />
      <line x1={16} y1={H - 18} x2={16 + 10 * scale} y2={H - 18} className="battle-scale" /><text x={16} y={H - 24} className="battle-scale-label">10 m</text>
    </svg>
    <dl className="battle-readout">
      <div><dt>앞뒤 간격 · 측정</dt><dd>{frame.gap_m > 0 ? `상대가 ${frame.gap_m.toFixed(1)}m 앞` : frame.gap_m < 0 ? `상대가 ${(-frame.gap_m).toFixed(1)}m 뒤` : '나란히'}{frame.gap_clamped ? ' *' : ''}</dd></div>
      <div><dt>좌우</dt><dd className={narrow ? 'is-measured' : 'is-estimated'}>{narrow ? `상대가 ${frame.beside === 'L' ? '왼쪽' : '오른쪽'} · 신호 실측` : '추정 범위'}</dd></div>
      <div><dt>내 속도</dt><dd>{frame.speed_kph.toFixed(0)} km/h · {frame.gear}단</dd></div>
      <div><dt>상대 속도 · 간격 변화로 계산</dt><dd>{frame.rival_speed_kph == null ? '—' : `${frame.rival_speed_kph.toFixed(0)} km/h`}</dd></div>
      <div><dt>제동 / 스로틀</dt><dd>{Math.round(frame.brake * 100)}% / {Math.round(frame.throttle * 100)}%</dd></div>
      {frame.gap_clamped && <p>* 옆 차 신호가 없어 차 길이 간격으로 보정했습니다.</p>}
    </dl>
    <div className="battle-map-legend" aria-hidden="true"><span><i className="me" />내 차 · GPS 실측</span><span><i className="rival" />상대 · 앞뒤 측정, 좌우 추정</span><span><i className="measured" />옆 차 신호로 좁힌 범위</span><span><i className="estimated" />좌우를 모를 때 범위</span><span><i className="band" />내 랩들의 라인 폭</span></div>
  </div>;
}

function GapChart({ scene, index, onIndex, altCorner, alt }) {
  const [ref, { width }] = useSize();
  const frames = scene.frames, W = width || 600, H = 96, t0 = frames[0].t, t1 = frames.at(-1).t;
  const max = Math.max(8, ...frames.map(f => Math.abs(f.gap_m)));
  const X = t => 44 + (t - t0) / Math.max(.1, t1 - t0) * (W - 56), Y = g => 8 + (1 - (g + max) / (2 * max)) * 44, PY = v => 88 - v * 24;
  const line = (value, y) => frames.map(f => `${X(f.t).toFixed(1)},${y(value(f)).toFixed(1)}`).join(' ');
  const spans = [];
  let start = null;
  frames.forEach((f, i) => {
    if (f.beside && start == null) start = f.t;
    if ((!f.beside || i === frames.length - 1) && start != null) { spans.push([start, f.t]); start = null; }
  });
  let altLine = null;
  if (altCorner && alt) {
    const gainM = alt.gain_s * ((altCorner.actual.end_kph ?? frames[index].speed_kph) / 3.6);
    altLine = frames.filter(f => f.t >= altCorner.t_entry).map(f => {
      const w = Math.min(1, (f.t - altCorner.t_entry) / Math.max(.1, altCorner.t_end - altCorner.t_entry));
      return `${X(f.t).toFixed(1)},${Y(Math.max(-max, Math.min(max, f.gap_m - gainM * w))).toFixed(1)}`;
    }).join(' ');
  }
  const pick = event => {
    const box = event.currentTarget.getBoundingClientRect();
    const t = t0 + (event.clientX - box.left - 44) / Math.max(1, box.width - 56) * (t1 - t0);
    const found = frames.findIndex(f => f.t >= t);
    onIndex(found < 0 ? frames.length - 1 : Math.max(0, found));
  };
  return <div className="battle-chart" ref={ref}>
    <svg width={W} height={H} onClick={pick} aria-label="앞뒤 간격과 페달" role="img">
      {spans.map(([a, b]) => <rect key={a} x={X(a)} y={6} width={Math.max(1, X(b) - X(a))} height={50} className="battle-chart-beside" />)}
      <line x1={44} x2={W - 12} y1={Y(0)} y2={Y(0)} className="battle-chart-zero" />
      <polyline points={line(f => f.gap_m, Y)} className="battle-chart-gap" />
      <text x={4} y={Y(max) + 8} className="battle-chart-label">앞 {max.toFixed(0)}m</text><text x={4} y={Y(-max)} className="battle-chart-label">뒤</text>
      <polyline points={line(f => f.throttle, PY)} className="battle-chart-throttle" />
      <polyline points={line(f => f.brake, PY)} className="battle-chart-brake" />
      <text x={4} y={76} className="battle-chart-label">페달</text>
      {altLine && <><rect x={X(altCorner.t_entry)} y={6} width={Math.max(1, X(Math.min(altCorner.t_end, t1)) - X(altCorner.t_entry))} height={50} className="battle-chart-alt-span" />
        <polyline points={altLine} className="battle-chart-alt" /><text x={W - 12} y={14} textAnchor="end" className="battle-chart-alt-label">점선: {altCorner.label}만 이렇게 탔을 때 (이후는 실제 그대로)</text></>}
      <line x1={X(frames[index].t)} x2={X(frames[index].t)} y1={8} y2={H - 4} className="battle-chart-cursor" />
    </svg>
  </div>;
}

function AdviceCard({ corner, active, onShow, onOpenCorner }) {
  const [choice, setChoice] = useState(corner.usual ? 'usual' : 'best');
  const alt = corner[choice] || corner.usual || corner.best;
  const a = corner.actual, b = alt.numbers;
  const brakeShift = a.brake_m != null && b.brake_m != null ? b.brake_m - a.brake_m : null;
  const source = choice === 'usual' ? `평소 혼자 달린 ${corner.usual.laps}랩의 중앙값` : `${corner.best.lap}랩 ${corner.best.solo ? '혼자 주행' : '배틀 중 주행'} · 비슷한 진입 ${corner.best.candidates}번 중 가장 빠름`;
  return <article className={cn('battle-advice', active && 'is-active')} aria-label={`${corner.label} ${corner.lap}랩 제안`}>
    <header><button type="button" className="battle-advice-title" onClick={() => onShow(corner, alt)} aria-pressed={active}><strong>{corner.label} · {corner.lap}랩</strong>{alt.verdict && <Badge variant={alt.verdict === 'ahead' || alt.verdict === 'alongside' ? 'default' : 'secondary'}>{VERDICTS[alt.verdict]}</Badge>}</button></header>
    <p className="battle-advice-situation">진입 때 {gapText(corner.gap_entry_m)}{corner.we_were ? ` · 나란히, 내가 ${SIDE[corner.we_were]}` : ''}</p>
    {corner.usual && corner.best && <ToggleGroup type="single" size="sm" variant="outline" value={choice} onValueChange={value => value && setChoice(value)} aria-label="비교할 주행"><ToggleGroupItem value="usual">평소대로</ToggleGroupItem><ToggleGroupItem value="best">이 레이스 최고</ToggleGroupItem></ToggleGroup>}
    <table><thead><tr><th scope="col" /><th scope="col">실제</th><th scope="col">이렇게</th></tr></thead><tbody>
      <tr><th scope="row">제동 시작</th><td>{brakeAt(a.brake_m)}</td><td>{brakeAt(b.brake_m)}{brakeShift != null && Math.abs(brakeShift) >= 2 && <small> · {Math.abs(brakeShift).toFixed(0)}m {brakeShift < 0 ? '일찍' : '늦게'}</small>}</td></tr>
      <tr><th scope="row">최저 속도</th><td>{kph(a.min_kph)}</td><td>{kph(b.min_kph)}{a.min_kph != null && b.min_kph != null && <small> · {fmt(b.min_kph - a.min_kph, 0, true)}</small>}</td></tr>
      <tr><th scope="row">풀스로틀</th><td>{throttleAt(a.full_throttle_m)}</td><td>{throttleAt(b.full_throttle_m)}</td></tr>
      <tr><th scope="row">탈출 속도</th><td>{kph(a.exit_kph)}</td><td>{kph(b.exit_kph)}{a.exit_kph != null && b.exit_kph != null && <small> · {fmt(b.exit_kph - a.exit_kph, 0, true)}</small>}</td></tr>
      <tr><th scope="row">다음 코너 입구 속도</th><td>{kph(a.end_kph)}</td><td>{kph(b.end_kph)}{a.end_kph != null && b.end_kph != null && <small> · {fmt(b.end_kph - a.end_kph, 0, true)}</small>}</td></tr>
      <tr><th scope="row">다음 코너 입구</th><td>{gapText(corner.gap_next_m)}</td><td>{gapText(alt.gap_next_pred_m)}<small> · {alt.gain_s.toFixed(2)}초 빨리</small></td></tr>
    </tbody></table>
    <p className="battle-advice-source">비교: {source}</p>
    <Button variant="ghost" size="sm" onClick={() => onOpenCorner(corner, choice === 'best' ? corner.best.lap : null)}>코너 비교에서 보기<ArrowRight data-icon="inline-end" /></Button>
  </article>;
}

function SceneView({ report, scene, onOpenCorner }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState('0.5');
  const [zoom, setZoom] = useState(25);
  const [shown, setShown] = useState(null);
  const clock = useRef(null);
  useEffect(() => { setIndex(0); setPlaying(false); setShown(null); }, [scene]);
  useEffect(() => {
    if (!playing) return undefined;
    const frames = scene.frames;
    let frame = 0, last = null;
    clock.current = frames[index]?.t ?? frames[0].t;
    const tick = now => {
      if (last != null) clock.current += (now - last) / 1000 * Number(rate);
      last = now;
      let next = 0;
      while (next < frames.length - 1 && frames[next + 1].t <= clock.current) next++;
      setIndex(next);
      if (next >= frames.length - 1) { setPlaying(false); return; }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // Restarting from the slider position is intended; index is read once when play starts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, rate, scene]);
  const play = () => { if (!playing && index >= scene.frames.length - 1) setIndex(0); setPlaying(value => !value); };
  const jump = t => { setPlaying(false); setIndex(Math.max(0, scene.frames.findIndex(f => f.t >= t - 1))); };
  const show = (corner, alt) => { setShown({ corner, alt }); jump(corner.t_entry); };
  const advised = scene.corners.filter(c => c.usual || c.best).sort((a, b) => (Math.max(b.usual?.gain_s ?? 0, b.best?.gain_s ?? 0)) - (Math.max(a.usual?.gain_s ?? 0, a.best?.gain_s ?? 0)));
  const quiet = scene.corners.filter(c => !c.usual && !c.best);
  const frames = scene.frames;
  return <div className="battle-scene">
    <section className="battle-stage" aria-label="장면 재생">
      <BattleMap report={report} scene={scene} index={index} zoom={zoom} />
      <div className="battle-controls">
        <Button variant={playing ? 'secondary' : 'outline'} size="sm" aria-pressed={playing} onClick={play}>{playing ? <Pause data-icon="inline-start" /> : <Play data-icon="inline-start" />}{playing ? '정지' : '재생'}</Button>
        <NativeSelect size="sm" value={rate} onChange={event => setRate(event.target.value)} aria-label="재생 속도"><NativeSelectOption value="0.25">0.25×</NativeSelectOption><NativeSelectOption value="0.5">0.5×</NativeSelectOption><NativeSelectOption value="1">1×</NativeSelectOption></NativeSelect>
        <input type="range" min={0} max={frames.length - 1} step={1} value={index} onChange={event => { setPlaying(false); setIndex(Number(event.target.value)); }} aria-label="장면 시간" />
        <output className="numeric">{(frames[index].t - frames[0].t).toFixed(1)} / {(frames.at(-1).t - frames[0].t).toFixed(1)}초</output>
        <label className="battle-zoom">확대<input type="range" min={15} max={120} value={zoom} onChange={event => setZoom(Number(event.target.value))} /></label>
      </div>
      <GapChart scene={scene} index={index} onIndex={next => { setPlaying(false); setIndex(next); }} altCorner={shown?.corner} alt={shown?.alt} />
    </section>
    <section className="battle-advice-list" aria-label="이렇게 탔다면">
      <h3>이렇게 탔다면</h3>
      <p className="battle-note">이 레이스에서 <b>내가 실제로 달린 다른 랩</b>과 바꿔 계산합니다. 상대는 실제 기록대로 달렸다고 둡니다. 카드 제목을 누르면 그 코너로 이동하고 아래 그래프에 점선으로 표시합니다.</p>
      {advised.length ? advised.map(corner => <AdviceCard key={`${corner.corner}-${corner.t_entry}`} corner={corner} active={shown?.corner === corner} onShow={show} onOpenCorner={onOpenCorner} />)
        : <p className="battle-note">이 배틀의 코너는 평소 혼자 달릴 때만큼 달렸습니다. 내 다른 랩 중 분명히 더 빨랐던 주행이 없습니다.</p>}
      {quiet.length > 0 && <details className="battle-quiet"><summary>제안이 없는 코너 {quiet.length}곳</summary><ul>{quiet.map(c => <li key={`${c.corner}-${c.t_entry}`}><button type="button" onClick={() => jump(c.t_entry)}>{c.label} · {c.lap}랩</button> {c.why_not === 'TOO_FEW_PASSES' ? '비교할 통과가 부족합니다.' : c.why_not === 'RIVAL_AHEAD' ? '더 빠른 주행은 앞차보다 빠른 속도가 필요해 제외했습니다.' : '평소만큼 달렸습니다.'}</li>)}</ul></details>}
    </section>
  </div>;
}

function BattleScenes({ filename, game, onOpenCorner }) {
  const { report, error, loading, retry } = useBattleReport(filename, game);
  const scenes = useMemo(() => (report?.scenes || []).slice().sort((a, b) => (b.beside_s > 0) - (a.beside_s > 0) || a.t0 - b.t0), [report]);
  const [chosen, setChosen] = useState(null);
  useEffect(() => { setChosen(report?.summary?.top?.scene || scenes[0]?.id || null); }, [report, scenes]);
  if (loading) return <div role="status" className="battle-state">배틀 장면을 계산하는 중…</div>;
  if (error) return <Empty className="battle-state"><EmptyHeader><EmptyTitle>배틀 분석을 불러오지 못했습니다.</EmptyTitle><EmptyDescription>{error}</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" onClick={retry}>다시 시도</Button></EmptyContent></Empty>;
  if (report?.status !== 'READY') return <Empty className="battle-state"><EmptyHeader><EmptyTitle>배틀 장면을 계산하지 않았습니다.</EmptyTitle><EmptyDescription>{BATTLE_REASONS[report?.reason] || report?.reason || '이유를 확인할 수 없습니다.'}</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" onClick={retry}>다시 확인</Button></EmptyContent></Empty>;
  if (!scenes.length) return <Empty className="battle-state"><EmptyHeader><EmptyTitle>배틀 장면이 없습니다.</EmptyTitle><EmptyDescription>같은 클래스 차와 20m 안에서 4초 넘게 달린 구간이 없었습니다.</EmptyDescription></EmptyHeader></Empty>;
  const scene = scenes.find(s => s.id === chosen) || scenes[0];
  const { summary, calibration } = report;
  return <div className="battle-workspace">
    <aside className="battle-scene-list" aria-label="배틀 장면 목록">
      <p className="battle-summary numeric">배틀 {summary.battles}번 · 나란히 {summary.side_by_side} · 추월 {summary.passed} · 추월당함 {summary.lost}</p>
      {!report.enough_laps && <p className="battle-warning">깨끗한 랩이 {report.laps_used}개뿐이라 '이렇게 탔다면'을 계산하지 못한 코너가 많습니다. 5랩 넘게 달린 레이스에서 더 정확합니다.</p>}
      <div className="battle-scene-items">{scenes.map(s => <button key={s.id} type="button" className={cn('battle-scene-item', s.id === scene.id && 'is-active')} aria-pressed={s.id === scene.id} onClick={() => setChosen(s.id)}>
        <strong>{s.lap}랩 · 상대 #{s.rival}{s.beside_s > 0 && <Badge variant="outline">나란히 {s.beside_s.toFixed(1)}초</Badge>}</strong>
        <small>{OUTCOMES[s.outcome] || s.outcome} · {(s.t1 - s.t0).toFixed(0)}초{s.corners.some(c => c.usual || c.best) ? ' · 제안 있음' : ''}</small>
      </button>)}</div>
      <details className="battle-method"><summary>측정한 것과 추정한 것</summary>
        <p><b>측정</b>: 내 차 위치·방향(IBT GPS 초당 60번), 상대와의 앞뒤 간격(초당 10번), 옆 차 신호.</p>
        {calibration?.median_m != null && <p><b>앞뒤 간격 확인</b>: 옆 차 신호가 켜지고 꺼지는 {calibration.edges}번의 간격 중앙값 {calibration.median_m.toFixed(2)}m를 차 길이 {report.assumptions.car_length_m}m와 맞춰 {fmt(calibration.bias_m, 2)}m를 보정했습니다.</p>}
        <p><b>추정</b>: 상대의 좌우 위치. 옆에 있을 때는 신호 방향으로 차 폭~{report.assumptions.side_max_m}m, 그 밖에는 초당 {report.assumptions.lateral_rate_mps}m씩 넓어지는 범위입니다.</p>
        <p><b>쓰지 않은 것</b>: Garage61, 다른 사람의 기록, 상대의 페달 조작.</p>
      </details>
    </aside>
    <SceneView report={report} scene={scene} onOpenCorner={onOpenCorner} />
  </div>;
}

export default function BattleWorkspace({ filename, game, onOpenCorner }) {
  const lmu = game === 'lmu';
  const [view, setView] = useState(lmu ? 'positions' : 'scenes');
  return <section className="wide-workspace battle-tab" aria-labelledby="battle-title">
    <h2 id="battle-title" className="sr-only">배틀 분석</h2>
    <Tabs value={view} onValueChange={setView} className="battle-tabs">
      <TabsList aria-label="배틀 분석 보기" className="battle-view-switch"><TabsTrigger value="scenes">배틀 장면</TabsTrigger><TabsTrigger value="positions">순위 변화</TabsTrigger></TabsList>
      <TabsContent value="scenes" className="battle-tab-content">{lmu ? <Empty className="battle-state"><EmptyHeader><EmptyTitle>LMU 배틀 장면은 준비 중입니다.</EmptyTitle><EmptyDescription>LMU 주변 차량 기록에 좌우 위치를 모으고 있습니다. 순위 변화는 지금 볼 수 있습니다.</EmptyDescription></EmptyHeader><EmptyContent><Button variant="outline" onClick={() => setView('positions')}>순위 변화 보기</Button></EmptyContent></Empty>
        : <BattleScenes filename={filename} game={game} onOpenCorner={onOpenCorner} />}</TabsContent>
      <TabsContent value="positions" className="battle-tab-content"><Suspense fallback={<div role="status">순위 그래프를 준비하는 중…</div>}><RacePositionChart filename={filename} /></Suspense></TabsContent>
    </Tabs>
  </section>;
}
