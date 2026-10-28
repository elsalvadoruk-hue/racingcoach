import React, { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from '../components/ui/empty';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { cn } from '../lib/utils';
import { BATTLE_REASONS, OUTCOMES, RESPONSES, SIM_REASONS, useBattleReport } from './battleReport';
import BattleScenario, { selectedRun, trajectoryAt } from './BattleScenario';
import './battle.css';

const RacePositionChart = lazy(() => import('./RacePositionChart'));

const fmt = (v, d = 1, sign = false) => v == null || !Number.isFinite(v) ? '—' : `${sign && v > 0 ? '+' : ''}${v.toFixed(d)}`;

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

function BattleMap({ report, scene, index, zoom, run }) {
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
  const ghost = trajectoryAt(run?.trajectory, frame.t), baseline = trajectoryAt(run?.baseline, frame.t);
  const ghostHeading = ghost ? headingAt(run.trajectory, ghost.index) ?? heading : heading;
  const ghostRival = ghost ? refAt(geometry, ghost.frame.rival_pct, (ghost.frame.rival_lat[0] + ghost.frame.rival_lat[1]) / 2) : null;
  return <div className="battle-map" ref={ref}>
    <svg role="img" aria-label="내 차 중심의 배틀 장면" width={W} height={H}>
      <polygon points={poly(band)} className="battle-band" />
      <polyline points={poly(geometry.ref.filter(near))} className="battle-reference-line" />
      <polyline points={poly(trail.map(q => q.me))} className="battle-trail-me" />
      {run && <>
        <polyline points={poly(scene.frames.filter(f => f.t >= run.alt.start_t && f.t <= run.alt.end_t).map(f => f.me))} className="battle-comparison-actual" />
        <polyline points={poly(run.baseline.map(q => q.me))} className="battle-trail-baseline" />
        <polyline points={poly(run.trajectory.map(q => q.rival))} className="battle-trail-response" />
        <polyline points={poly(run.trajectory.map(q => q.me))} className="battle-trail-alternative" aria-label="선택한 대응 경로" />
      </>}
      <line x1={ax} y1={ay} x2={bx} y2={by} className={cn('battle-rival-range', narrow && 'is-measured')} strokeWidth={Math.max(6, assumptions.car_length_m * scale * .9)} />
      <polyline points={poly(rivalTrail)} className="battle-trail-rival" />
      <polygon points={car(c.x, c.y, rivalHeading)} className={cn('battle-car-rival', !narrow && 'is-estimated')} />
      <line {...nose(c.x, c.y, rivalHeading)} className="battle-car-nose" />
      <polygon points={car(cx, cy, heading)} className="battle-car-me" aria-label="실제 내 차" />
      <line {...nose(cx, cy, heading)} className="battle-car-nose" />
      {ghost && <g aria-label="같은 시각의 대안 차량" data-time={ghost.frame.t}>
        <polygon points={car(...ghost.frame.me, ghostHeading)} className="battle-car-alternative" aria-label="대안 내 차" />
        <line {...nose(...ghost.frame.me, ghostHeading)} className="battle-car-nose" />
        <polygon points={car(...ghost.frame.rival, Math.atan2(ghostRival.hy, ghostRival.hx))} className="battle-car-response" />
        {baseline && <line x1={P(...baseline.frame.me)[0]} y1={P(...baseline.frame.me)[1]} x2={P(...ghost.frame.me)[0]} y2={P(...ghost.frame.me)[1]} className="battle-comparison-distance" />}
      </g>}
      <line x1={16} y1={H - 18} x2={16 + 10 * scale} y2={H - 18} className="battle-scale" /><text x={16} y={H - 24} className="battle-scale-label">10 m</text>
    </svg>
    <dl className="battle-readout">
      <div><dt>앞뒤 간격 · 측정</dt><dd>{frame.gap_m > 0 ? `상대가 ${frame.gap_m.toFixed(1)}m 앞` : frame.gap_m < 0 ? `상대가 ${(-frame.gap_m).toFixed(1)}m 뒤` : '나란히'}{frame.gap_clamped ? ' *' : ''}</dd></div>
      <div><dt>좌우</dt><dd className={narrow ? 'is-measured' : 'is-estimated'}>{narrow ? `상대가 ${frame.beside === 'L' ? '왼쪽' : '오른쪽'} · 신호 실측` : '추정 범위'}</dd></div>
      <div><dt>내 속도</dt><dd>{frame.speed_kph.toFixed(0)} km/h · {frame.gear}단</dd></div>
      {ghost && <div><dt>대안 속도 · 같은 시각</dt><dd>{ghost.frame.speed_kph.toFixed(0)} km/h</dd></div>}
      {frame.gap_clamped && <p>* 옆 차 신호가 없어 차 길이 간격으로 보정했습니다.</p>}
    </dl>
    <div className="battle-map-legend"><span><i className="me" />실선 · 실제 내 차</span><span><i className="rival" />상대 · 좌우 추정</span>{run && <><span><i className="alternative" />점선 · 선택한 대응</span><span><i className="response" />상대 반응 가정</span><span><i className="baseline" />회색 · 기본 대응 모형</span></>}</div>
    {run && <p className="battle-comparison-caption">{ghost ? `동일 시각 비교 · +${(frame.t - run.trajectory[0].t).toFixed(1)}초` : '대안 계산 구간 밖 · 경로만 표시'} · {run.alt.label}</p>}
  </div>;
}

function GapChart({ scene, index, onIndex, run }) {
  const [ref, { width }] = useSize();
  const frames = scene.frames, W = width || 600, H = 96, t0 = frames[0].t, t1 = frames.at(-1).t;
  const max = Math.max(8, ...frames.map(f => Math.abs(f.gap_m)), ...(run?.trajectory || []).map(f => Math.abs(f.gap_m)));
  const X = t => 44 + (t - t0) / Math.max(.1, t1 - t0) * (W - 56), Y = g => 8 + (1 - (g + max) / (2 * max)) * 44, PY = v => 88 - v * 24;
  const line = (value, y) => frames.map(f => `${X(f.t).toFixed(1)},${y(value(f)).toFixed(1)}`).join(' ');
  const spans = [];
  let start = null;
  frames.forEach((f, i) => {
    if (f.beside && start == null) start = f.t;
    if ((!f.beside || i === frames.length - 1) && start != null) { spans.push([start, f.t]); start = null; }
  });
  const altLine = run?.trajectory.map(f => `${X(f.t).toFixed(1)},${Y(f.gap_m).toFixed(1)}`).join(' ');
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
      {altLine && <><rect x={X(run.alt.start_t)} y={6} width={Math.max(1, X(run.alt.end_t) - X(run.alt.start_t))} height={50} className="battle-chart-alt-span" />
        <polyline points={altLine} className="battle-chart-alt" /><text x={W - 12} y={14} textAnchor="end" className="battle-chart-alt-label">점선: 상대 반응을 가정한 대응</text></>}
      <line x1={X(frames[index].t)} x2={X(frames[index].t)} y1={8} y2={H - 4} className="battle-chart-cursor" />
    </svg>
  </div>;
}

function SceneView({ report, scene, onOpenCorner }) {
  const advised = scene.corners.filter(c => c.alternatives?.length);
  const quiet = scene.corners.filter(c => !c.alternatives?.length);
  const [shown, setShown] = useState(() => advised.length ? { corner: advised[0], alt: advised[0].alternatives[0] } : null);
  const frames = scene.frames;
  const at = t => { const i = frames.findIndex(f => f.t >= t - .001); return i < 0 ? frames.length - 1 : i; };
  const [index, setIndex] = useState(() => shown ? at(shown.alt.start_t) : 0);
  const [playing, setPlaying] = useState(false);
  const [comparisonOnly, setComparisonOnly] = useState(false);
  const [response, setResponse] = useState('track_pace');
  const [rate, setRate] = useState('0.5');
  const [zoom, setZoom] = useState(25);
  const clock = useRef(null);
  const run = selectedRun(shown, response);
  useEffect(() => {
    if (!playing) return undefined;
    let frame = 0, last = null;
    const end = comparisonOnly && shown ? Math.min(frames.length - 1, at(shown.alt.end_t)) : frames.length - 1;
    clock.current = frames[index].t;
    const tick = now => {
      if (last != null) clock.current += (now - last) / 1000 * Number(rate);
      last = now;
      let next = 0;
      while (next < end && frames[next + 1].t <= clock.current + .001) next++;
      setIndex(next);
      if (next >= end) { setPlaying(false); return; }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // Start from the current slider once; restarting on every animation frame would stall playback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, rate, scene, comparisonOnly, shown]);
  const seek = next => { setPlaying(false); setIndex(next); };
  const show = (corner, alt) => { setShown({ corner, alt }); seek(at(alt.start_t)); };
  const compare = (corner, alt) => { setShown({ corner, alt }); setIndex(at(alt.start_t)); setComparisonOnly(true); setPlaying(true); };
  const play = () => {
    if (!playing && index >= frames.length - 1) setIndex(0);
    setComparisonOnly(false); setPlaying(value => !value);
  };
  return <div className="battle-scene">
    <section className="battle-stage" aria-label="장면 재생">
      <BattleMap report={report} scene={scene} index={index} zoom={zoom} run={run} />
      <div className="battle-controls">
        <Button variant={playing ? 'secondary' : 'outline'} size="sm" aria-pressed={playing} onClick={play}>{playing ? <Pause data-icon="inline-start" /> : <Play data-icon="inline-start" />}{playing ? '정지' : '재생'}</Button>
        <NativeSelect size="sm" value={rate} onChange={event => setRate(event.target.value)} aria-label="재생 속도"><NativeSelectOption value="0.25">0.25×</NativeSelectOption><NativeSelectOption value="0.5">0.5×</NativeSelectOption><NativeSelectOption value="1">1×</NativeSelectOption></NativeSelect>
        <input type="range" min={0} max={frames.length - 1} step={1} value={index} onChange={event => seek(Number(event.target.value))} aria-label="장면 시간" />
        <output className="numeric">{(frames[index].t - frames[0].t).toFixed(1)} / {(frames.at(-1).t - frames[0].t).toFixed(1)}초</output>
        <label className="battle-zoom">확대<input type="range" min={15} max={120} value={zoom} onChange={event => setZoom(Number(event.target.value))} /></label>
      </div>
      <GapChart scene={scene} index={index} onIndex={seek} run={run} />
    </section>
    <section className="battle-advice-list" aria-label="이렇게 탔다면">
      <h3>이렇게 탔다면</h3>
      <p className="battle-note">배틀 시작 상태에서 <b>최대 2초의 페달 대응</b>을 비교합니다. 점선 차량은 실제 차량과 같은 시각입니다. 상대 반응을 바꿔 결과를 확인하세요.</p>
      {advised.length > 0 && <NativeSelect size="sm" value={response} onChange={event => { setResponse(event.target.value); setPlaying(false); }} aria-label="상대 반응 가정">
        {Object.entries(RESPONSES).map(([id, label]) => <NativeSelectOption key={id} value={id}>{label}</NativeSelectOption>)}
      </NativeSelect>}
      {advised.map(corner => <BattleScenario key={`${corner.corner}-${corner.t_entry}`} corner={corner} active={shown?.corner === corner} selected={shown?.alt} response={response} onShow={show} onPlay={compare} onOpenCorner={onOpenCorner} />)}
      {!advised.length && <p className="battle-note">이 장면에서 구별되는 대안을 계산하지 못했습니다. 아래 코너별 이유를 확인하세요.</p>}
      {quiet.length > 0 && <details className="battle-quiet"><summary>대안을 계산하지 못한 코너 {quiet.length}곳</summary><ul>{quiet.map(c => <li key={`${c.corner}-${c.t_entry}`}><button type="button" onClick={() => seek(at(c.t_entry))}>{c.label} · {c.lap}랩</button> {SIM_REASONS[c.why_not] || '앞차 제약 안에서 기본 대응과 구별되는 조작이 없습니다.'}</li>)}</ul></details>}
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
      {!report.enough_laps && <p className="battle-warning">완주·비피트 랩이 {report.laps_used}개뿐이라 차량 반응 자료가 적습니다. 코너별 계산 오차를 확인하세요.</p>}
      <div className="battle-scene-items">{scenes.map(s => <button key={s.id} type="button" className={cn('battle-scene-item', s.id === scene.id && 'is-active')} aria-pressed={s.id === scene.id} onClick={() => setChosen(s.id)}>
        <strong>{s.lap}랩 · 상대 #{s.rival}{s.beside_s > 0 && <Badge variant="outline">나란히 {s.beside_s.toFixed(1)}초</Badge>}</strong>
        <small>{OUTCOMES[s.outcome] || s.outcome} · {(s.t1 - s.t0).toFixed(0)}초{s.corners.some(c => c.alternatives?.length) ? ' · 제안 있음' : ''}</small>
      </button>)}</div>
      <details className="battle-method"><summary>측정한 것과 추정한 것</summary>
        <p><b>측정</b>: 내 차 위치·방향(IBT GPS 초당 60번), 상대와의 앞뒤 간격(초당 10번), 옆 차 신호.</p>
        {calibration?.median_m != null && <p><b>앞뒤 간격 확인</b>: 옆 차 신호가 켜지고 꺼지는 {calibration.edges}번의 간격 중앙값 {calibration.median_m.toFixed(2)}m를 차 길이 {report.assumptions.car_length_m}m와 맞춰 {fmt(calibration.bias_m, 2)}m를 보정했습니다.</p>}
        <p><b>추정</b>: 상대의 좌우 위치. 옆에 있을 때는 신호 방향으로 차 폭~{report.assumptions.side_max_m}m, 그 밖에는 초당 {report.assumptions.lateral_rate_mps}m씩 넓어지는 범위입니다.</p>
        <p><b>대안</b>: 같은 출발 상태에서 페달과 간격을 0.1초씩 계산합니다. 다른 랩은 차량 반응 보정에만 사용합니다. 도로 경계·상대 페달·타이어 상태는 알 수 없습니다.</p>
      </details>
    </aside>
    <SceneView key={scene.id} report={report} scene={scene} onOpenCorner={onOpenCorner} />
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
