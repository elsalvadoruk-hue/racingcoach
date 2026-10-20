import React, { useEffect, useMemo, useRef, useState } from 'react';

// Expert view: several laps of the file on one distance axis, MoTeC/Mu style. Pick laps and channels,
// read every lap's value under the cursor, drag across the chart to zoom, or jump to a corner.
// Data: /api/lap-overlay (the analysis' own lap grids); gaps stay gaps.
export const LAP_COLOURS = ['#38bdf8', '#f97316', '#a3e635', '#f472b6', '#facc15', '#c084fc', '#2dd4bf', '#f87171'];
const DEFAULT_CHANNELS = ['delta', 'speed', 'throttle', 'brake', 'steering'];
const LANE_HEIGHT = { delta: 90, speed: 150 };
const LANE_DEFAULT = 70;
const DEFAULT_WIDTH = 1000;
const LEFT = 120;
const RIGHT = 12;
const MAX_LAPS = 8;
const MAX_EXTRA = 4;
const CORNER_BEFORE_M = 150;
const CORNER_AFTER_M = 100;

const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const lapTime = (seconds) => {
  if (!finite(seconds)) return '--';
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(3).padStart(6, '0')}`;
};
const formatValue = (value, unit) => {
  if (!finite(value)) return '--';
  if (unit === 's') return `${value > 0 ? '+' : ''}${value.toFixed(3)}`;
  if (unit === 'g') return value.toFixed(2);
  if (unit === 'gear') return value === 0 ? 'N' : value === -1 ? 'R' : String(Math.round(value));
  if (unit === '' || unit === 'rpm') return String(Math.round(value));
  return value.toFixed(1);
};

export function defaultLaps(laps, preferred = []) {
  const complete = laps.filter((lap) => finite(lap.lap_time_s));
  const valid = complete.filter((lap) => lap.status === 'VALID' || lap.measurement_eligible === true).sort((a, b) => a.lap_time_s - b.lap_time_s);
  const picked = [];
  [...preferred, ...valid.map((lap) => lap.lap_number)].forEach((number) => {
    if (number != null && !picked.includes(number) && complete.some((lap) => lap.lap_number === number) && picked.length < 4) picked.push(number);
  });
  return picked;
}

// Laps of another drive file (same track) or a Garage61 lap, added beside this file's laps.
function AddLaps({ otherFiles, references, loadLaps, onAdd, full }) {
  const [file, setFile] = useState('');
  const [fileLaps, setFileLaps] = useState([]);
  const [lap, setLap] = useState('');
  const [reference, setReference] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setFileLaps([]); setLap(''); setError('');
    if (!file || !loadLaps) return undefined;
    loadLaps(file).then((payload) => { if (live) setFileLaps(payload?.laps || []); })
      .catch((failure) => { if (live) setError(failure?.message || '랩 목록을 불러오지 못했습니다.'); });
    return () => { live = false; };
  }, [file, loadLaps]);
  if (!otherFiles.length && !references.length) return null;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-border p-3" data-testid="lap-overlay-add">
      <span className="text-xs font-semibold text-muted-foreground">다른 랩 더하기 (최대 {MAX_EXTRA}개 · 같은 트랙·레이아웃)</span>
      {otherFiles.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select value={file} onChange={(event) => setFile(event.target.value)} aria-label="다른 주행 파일" className="max-w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm">
            <option value="">다른 주행 파일 고르기</option>
            {otherFiles.map((item) => <option key={item.name} value={item.name} disabled={item.analyzable === false}>{item.label || item.display_name || item.file_name || item.name}</option>)}
          </select>
          <select value={lap} onChange={(event) => setLap(event.target.value)} aria-label="그 파일의 랩" disabled={!fileLaps.length} className="rounded-lg border border-border bg-background px-2 py-1.5 text-sm">
            <option value="">랩</option>
            {fileLaps.map((item) => <option key={item.lap_number} value={item.lap_number} disabled={item.measurement_eligible === false}>랩 {item.lap_number} · {lapTime(item.lap_time_s)}{item.measurement_eligible === true ? ' (유효성 확인 필요)' : item.measurement_eligible === false ? ' (측정 자료 부족)' : item.valid ? '' : ' (무효)'}</option>)}
          </select>
          <button type="button" disabled={!file || !lap || full} onClick={() => { onAdd({ source: 'IBT', filename: file, lap: Number(lap) }); setLap(''); }}
            className="rounded-lg border border-border px-2.5 py-1.5 text-sm disabled:opacity-40">이 랩 더하기</button>
        </div>
      )}
      {references.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select value={reference} onChange={(event) => setReference(event.target.value)} aria-label="Garage61 랩" className="max-w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm">
            <option value="">Garage61 랩 고르기</option>
            {references.map((item) => <option key={item.name} value={item.name}>{item.label || item.name}</option>)}
          </select>
          <button type="button" disabled={!reference || full} onClick={() => { onAdd({ source: 'GARAGE61', filename: reference }); setReference(''); }}
            className="rounded-lg border border-border px-2.5 py-1.5 text-sm disabled:opacity-40">이 랩 더하기</button>
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

// The horizontal scroll bar under the chart: the whole lap is the track, the thumb is the part shown.
// The chart follows the thumb while it is dragged (the overlay keeps a stretch on each side in memory).
const WHEEL_STEP = 0.15;
// One wheel notch (deltaY 100) moves 15 % of the window; touchpads send small deltas and move less.
export const wheelShift = (delta) => Math.max(-0.3, Math.min(0.3, delta / 667));
export function PanBar({ range, onChange }) {
  const trackRef = useRef(null);
  const dragRef = useRef(null);
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const span = range[1] - range[0];
  const clamp = (value, width = span) => (Number.isFinite(value) ? Math.min(Math.max(0, value), Math.max(0, 1 - width)) : rangeRef.current[0]);
  const move = (value) => { if (Math.abs(value - rangeRef.current[0]) > 1e-6) onChange([value, value + span]); };
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return undefined;
    // A native listener: React's wheel handler is passive, and the page must not scroll while panning.
    const onWheel = (event) => {
      const [from, to] = rangeRef.current;
      const width = to - from;
      if (width >= 0.999) return;
      event.preventDefault();
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      const next = Math.min(Math.max(0, from + wheelShift(delta) * width), Math.max(0, 1 - width));
      if (Math.abs(next - from) > 1e-6) onChangeRef.current([next, next + width]);
    };
    track.addEventListener('wheel', onWheel, { passive: false });
    return () => track.removeEventListener('wheel', onWheel);
  }, []);
  const pctAt = (clientX) => {
    const box = trackRef.current?.getBoundingClientRect();
    return box && box.width && Number.isFinite(clientX) ? (clientX - box.left) / box.width : null;
  };
  const dragTo = (clientX) => {
    const drag = dragRef.current;
    const box = trackRef.current?.getBoundingClientRect();
    return drag && box?.width && Number.isFinite(clientX) ? clamp(drag.from + (clientX - drag.x) / box.width) : null;
  };
  return (
    <div className="mt-2 flex items-center gap-2" data-testid="lap-overlay-pan">
      <div ref={trackRef} role="scrollbar" aria-label="그래프 좌우 이동" aria-orientation="horizontal" aria-controls="lap-overlay-chart"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(range[0] * 100)} tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
            event.preventDefault();
            move(clamp(range[0] + (event.key === 'ArrowRight' ? WHEEL_STEP : -WHEEL_STEP) * span));
          }
        }}
        onPointerDown={(event) => {
          const at = pctAt(event.clientX);
          if (at == null) return;
          if (at >= range[0] && at <= range[1]) {
            dragRef.current = { x: event.clientX, from: range[0] };
            event.currentTarget.setPointerCapture?.(event.pointerId);
          } else {
            move(clamp(at - span / 2));                       // jump so the click is in the middle
          }
        }}
        onPointerMove={(event) => { const next = dragTo(event.clientX); if (next != null) move(next); }}
        onPointerUp={(event) => { const next = dragTo(event.clientX); dragRef.current = null; if (next != null) move(next); }}
        className={`relative h-4 flex-1 cursor-pointer rounded-full bg-muted ${span >= 0.999 ? 'opacity-50' : ''}`}>
        <div className="absolute inset-y-0 rounded-full border border-primary/60 bg-primary/40"
          style={{ left: `${range[0] * 100}%`, width: `${Math.max(1.5, span * 100)}%` }} />
      </div>
    </div>
  );
}

const extraKey = (extra) => (extra.source === 'IBT' ? `ibt:${extra.filename}#${extra.lap}` : `g61:${extra.filename}`);

export default function LapOverlay({ laps = [], corners = [], preferredLaps = [], preferredExtras = [], preferredBaseKey = null, load, otherFiles = [], references = [], loadLaps, fillHeight = false, readoutGroupSize = 0 }) {
  const complete = useMemo(() => laps.filter((lap) => finite(lap.lap_time_s)), [laps]);
  const [chosen, setChosen] = useState(() => defaultLaps(laps, preferredLaps));
  const [extras, setExtras] = useState(() => preferredExtras.slice(0, MAX_EXTRA));
  const [baseKey, setBaseKey] = useState(preferredBaseKey);
  const [channels, setChannels] = useState(DEFAULT_CHANNELS);
  const [range, setRange] = useState([0, 1]);
  // The stretch asked from the server: the window shown plus one window on each side, so panning (wheel over
  // the chart, dragging the scroll bar) only moves the view; leaving it fetches a new stretch a moment later.
  const [fetchRange, setFetchRange] = useState([0, 1]);
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const [corner, setCorner] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(null);
  const [drag, setDrag] = useState(null);
  const [history, setHistory] = useState([]);
  const svgRef = useRef(null);
  const [chartSize, setChartSize] = useState(null);
  const lapKey = laps.map((lap) => lap.lap_number).join(',');

  useEffect(() => { setChosen(defaultLaps(laps, preferredLaps)); setExtras(preferredExtras.slice(0, MAX_EXTRA)); setBaseKey(preferredBaseKey); setRange([0, 1]); setFetchRange([0, 1]); setHistory([]); setCorner(''); }, [lapKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let live = true;
    if (!load || (!chosen.length && !extras.length)) { setData(null); return undefined; }
    setLoading(true); setError('');
    const shownWidth = Math.max(1e-6, rangeRef.current[1] - rangeRef.current[0]);
    const points = Math.min(4000, Math.max(300, Math.round(1500 * ((fetchRange[1] - fetchRange[0]) / shownWidth))));
    load({ laps: chosen, extra: extras.map(({ source, filename, lap }) => ({ source, filename, lap })), base_key: baseKey,
      start_pct: fetchRange[0], end_pct: fetchRange[1], points })
      .then((payload) => { if (live) { setData(payload); setCursor(null); } })
      .catch((failure) => { if (live) setError(failure?.message || '랩 자료를 불러오지 못했습니다.'); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [load, chosen, extras, baseKey, fetchRange]);

  useEffect(() => {
    const width = range[1] - range[0];
    const wanted = width >= 0.999 ? [0, 1] : [Math.max(0, range[0] - width), Math.min(1, range[1] + width)];
    const covered = fetchRange[0] <= range[0] + 1e-9 && fetchRange[1] >= range[1] - 1e-9;
    // A different zoom needs new detail; the same zoom moved within the stretch needs nothing.
    const sameDetail = Math.abs((fetchRange[1] - fetchRange[0]) - (wanted[1] - wanted[0])) < width * 0.5 || (width >= 0.999 && fetchRange[1] - fetchRange[0] >= 0.999);
    if (covered && sameDetail) return undefined;
    const timer = setTimeout(() => setFetchRange(wanted), 120);
    return () => clearTimeout(timer);
  }, [range, fetchRange]);

  const toggleLap = (number) => setChosen((current) => (current.includes(number)
    ? current.filter((item) => item !== number)
    : current.length >= MAX_LAPS ? current : [...current, number]));
  const toggleChannel = (key) => setChannels((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]));
  // Every lap drawn has a key: this file's laps their number, others 'ibt:file#lap' / 'g61:file' (as the server).
  const allKeys = [...chosen.map(String), ...extras.map(extraKey)];
  const colourOf = (key) => LAP_COLOURS[Math.max(0, allKeys.indexOf(String(key))) % LAP_COLOURS.length];
  const tagOf = (key) => {
    const index = extras.findIndex((extra) => extraKey(extra) === key);
    return index >= 0 ? `+${index + 1}` : String(key);
  };
  const addExtra = (extra) => setExtras((list) => (list.length >= MAX_EXTRA || list.some((item) => extraKey(item) === extraKey(extra)) ? list : [...list, extra]));
  const removeExtra = (key) => { setExtras((list) => list.filter((item) => extraKey(item) !== key)); if (baseKey === key) setBaseKey(null); };

  const pickCorner = (id) => {
    setCorner(id);
    const found = corners.find((item) => item.id === id);
    const length = data?.track_length_m;
    if (!found || !(length > 0)) { setRange([0, 1]); return; }
    const start = Math.max(0, found.entry_pct - CORNER_BEFORE_M / length);
    const end = Math.min(1, found.exit_pct + CORNER_AFTER_M / length);
    setRange(end > start ? [start, end] : [0, 1]);   // a corner across the line falls back to the whole lap
    setHistory([]);
  };
  // MoTeC-style drag: left to right zooms into the dragged part, right to left steps back out
  // (to the previous zoom, or twice as wide around the middle when there is none).
  const zoomIn = (a, b) => { setHistory((list) => [...list, range]); setRange([a, b]); setCorner(''); };
  const zoomOut = () => {
    if (history.length) { setRange(history[history.length - 1]); setHistory((list) => list.slice(0, -1)); return; }
    const middle = (range[0] + range[1]) / 2;
    const half = Math.min(0.5, range[1] - range[0]);
    const start = Math.max(0, middle - half);
    const end = Math.min(1, middle + half);
    setRange(end - start >= 0.999 ? [0, 1] : [start, end]);
    if (end - start >= 0.999) setCorner('');
  };

  const shown = (data?.channels || []).filter((channel) => channels.includes(channel.key));
  const distances = data?.distance_pct || [];
  // The points of the fetched stretch that fall in the window shown (one more on each side for the edges).
  const lowerBound = (value) => { let lo = 0; let hi = distances.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (distances[mid] < value) lo = mid + 1; else hi = mid; } return lo; };
  const i0 = Math.max(0, lowerBound(range[0]) - 1);
  const i1 = Math.min(distances.length - 1, lowerBound(range[1]));
  const lapNumbers = (data?.laps || []).map((lap) => lap.key ?? String(lap.lap)).filter((key) => allKeys.includes(key));
  const baseOf = data?.base_key ?? (data?.base_lap != null ? String(data.base_lap) : null);
  const lanes = [];
  const minimumPlotHeight = shown.length * 56 + 26;
  const naturalHeight = shown.reduce((sum, channel) => sum + (LANE_HEIGHT[channel.key] || LANE_DEFAULT) + 14, 26);
  const H = fillHeight && chartSize ? Math.max(minimumPlotHeight, chartSize.height) : Math.max(60, naturalHeight);
  const W = fillHeight && chartSize ? chartSize.width : DEFAULT_WIDTH;
  const laneWeight = shown.reduce((sum, channel) => sum + (LANE_HEIGHT[channel.key] || LANE_DEFAULT), 0);
  const extraHeight = Math.max(0, H - 26 - shown.length * (42 + 14));
  let top = 8;
  shown.forEach((channel) => {
    const weight = LANE_HEIGHT[channel.key] || LANE_DEFAULT;
    const height = fillHeight ? 42 + extraHeight * weight / Math.max(1, laneWeight) : weight;
    const values = lapNumbers.flatMap((key) => (data.series[key]?.[channel.key] || []).slice(i0, i1 + 1)).filter(finite);
    let lo = values.length ? Math.min(...values) : 0;
    let hi = values.length ? Math.max(...values) : 1;
    if (channel.key === 'throttle' || channel.key === 'brake') { lo = 0; hi = 100; }
    if (channel.key === 'delta') { const span = Math.max(Math.abs(lo), Math.abs(hi), 0.05); lo = -span; hi = span; }
    if (channel.key === 'steering') { const span = Math.max(Math.abs(lo), Math.abs(hi), 1); lo = -span; hi = span; }
    if (hi - lo < 1e-6) { hi = lo + 1; }
    lanes.push({ channel, top, height, lo, hi });
    top += height + 14;
  });
  const span = Math.max(1e-6, range[1] - range[0]);
  const x = (pct) => LEFT + ((pct - range[0]) / span) * (W - LEFT - RIGHT);
  const indexAt = (clientX) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box || !distances.length || !box.width) return null;
    const px = ((clientX - box.left) / box.width) * W;
    const pct = range[0] + ((px - LEFT) / (W - LEFT - RIGHT)) * span;
    const hi = Math.min(i1, Math.max(i0, lowerBound(pct)));
    const lo = Math.max(i0, hi - 1);
    return Math.abs(distances[lo] - pct) < Math.abs(distances[hi] - pct) ? lo : hi;
  };
  const lineFor = (values, lane) => {
    let d = ''; let open = false;
    for (let i = i0; i <= i1; i += 1) {
      const value = values[i];
      if (!finite(value)) { open = false; continue; }
      const y = lane.top + lane.height - ((value - lane.lo) / (lane.hi - lane.lo)) * lane.height;
      const px = x(distances[i]).toFixed(1), py = y.toFixed(1);
      // Gear is a discrete state. A diagonal ramp would invent intermediate
      // gears that neither game reported.
      d += open && lane.channel.key === 'gear' ? `H${px}V${py}` : `${open ? 'L' : 'M'}${px} ${py}`;
      open = true;
    }
    return d;
  };
  // The mouse wheel over the chart moves the window left and right once zoomed in (the whole lap leaves the
  // page to scroll). A native listener, because React's wheel handler is passive.
  const chartReady = Boolean(data && lanes.length);
  useEffect(() => {
    const svg = svgRef.current;
    if (!fillHeight || !svg) return undefined;
    const measure = () => {
      const width = svg.clientWidth, height = svg.clientHeight;
      if (width <= LEFT + RIGHT || height <= 0) return;
      setChartSize(previous => previous?.width === width && previous?.height === height ? previous : { width, height });
    };
    // Match SVG coordinates to the visible pixels so a shallow graph does not
    // flatten its axis labels or stretch them horizontally on wide monitors.
    measure();
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measure);
      observer.observe(svg);
      return () => observer.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [fillHeight, chartReady]);
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const onWheel = (event) => {
      const [from, to] = rangeRef.current;
      const width = to - from;
      if (width >= 0.999) return;
      event.preventDefault();
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      const next = Math.min(Math.max(0, from + wheelShift(delta) * width), Math.max(0, 1 - width));
      if (Math.abs(next - from) > 1e-6) { setRange([next, next + width]); setCorner(''); }
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [chartReady]);
  const length = data?.track_length_m;
  // corner names under the chart, skipping one that would overlap the previous (whole-lap view of a long track)
  const cornerMarks = [];
  [...corners].filter((item) => item.apex_pct >= range[0] && item.apex_pct <= range[1])
    .sort((a, b) => a.apex_pct - b.apex_pct)
    .forEach((item) => {
      const short = item.label.replace('자동 구간 ', 'C');
      const last = cornerMarks[cornerMarks.length - 1];
      if (!last || x(item.apex_pct) - x(last.apex_pct) > (3 * (short.length + last.short.length)) + 6) cornerMarks.push({ ...item, short });
    });
  const metres = (pct) => (length > 0 ? `${Math.round(pct * length)} m` : `${(pct * 100).toFixed(1)}%`);
  const zoomed = range[0] > 0 || range[1] < 1;

  return (
    <div className="flex flex-col gap-4" data-testid="lap-overlay">
      <p className="text-sm text-muted-foreground">
        이 파일의 여러 랩을 같은 거리축에 겹쳐 봅니다. 같은 트랙의 다른 주행 파일이나 Garage61 랩도 더할 수 있습니다. 그래프 위에서 마우스를 움직이면 그 지점의 모든 랩 값이 오른쪽 표에 나옵니다. 오른쪽으로 끌면 그 구간이 확대되고, 왼쪽으로 끌면 한 단계 축소됩니다. 확대한 뒤에는 그래프 위에서 마우스 휠을 굴리거나 아래 막대를 끌어 좌우로 옮깁니다. 시간 차이는 기준 랩(★)보다 그 지점까지 몇 초 늦었는지(+)입니다.
      </p>
      <div className="overlay-lap-picker flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-semibold text-muted-foreground">랩 (최대 {MAX_LAPS}개 · ★ 기준 랩)</span>
          <div className="overlay-selection-summary flex flex-wrap items-center gap-2 text-xs" aria-label="선택한 비교 랩">
            <span className="text-muted-foreground">선택 {chosen.length}/{MAX_LAPS}{extras.length > 0 ? ` · 외부 ${extras.length}개` : ''}</span>
            {allKeys.map(key => <span key={key} className="font-mono" style={{ color: colourOf(key) }}>{tagOf(key)}{key === baseOf ? '★' : ''}</span>)}
            <span className="text-muted-foreground">전체 {complete.length}랩 · 목록에서 스크롤</span>
          </div>
        </div>
        <div className="overlay-lap-list flex flex-wrap gap-2" role="group" aria-label="겹쳐 볼 랩 목록" tabIndex={0}>
          {complete.map((lap) => {
            const on = chosen.includes(lap.lap_number);
            const isBase = on && baseOf === String(lap.lap_number);
            return (
              <span key={lap.lap_number} className={`inline-flex items-center overflow-hidden rounded-lg border text-sm ${on ? 'border-foreground/40' : 'border-border opacity-70'}`}>
                <button type="button" aria-pressed={on} onClick={() => toggleLap(lap.lap_number)} className="inline-flex items-center gap-2 px-2.5 py-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: on ? colourOf(lap.lap_number) : 'transparent', border: '1px solid currentColor' }} />
                  <span className="font-mono">랩 {lap.lap_number}</span>
                  <span className="font-mono text-xs text-muted-foreground">{lapTime(lap.lap_time_s)}</span>
                  {lap.status !== 'VALID' && <span className="text-xs text-warning">{lap.measurement_eligible ? '유효성 확인 필요' : '무효'}</span>}
                </button>
                {on && (
                  <button type="button" aria-label={`랩 ${lap.lap_number}을 기준으로`} title="시간 차이의 기준 랩" onClick={() => setBaseKey(String(lap.lap_number))}
                    className={`border-l border-border px-2 py-1.5 ${isBase ? 'text-warning' : 'text-muted-foreground hover:text-foreground'}`}>★</button>
                )}
              </span>
            );
          })}
          {extras.map((extra) => {
            const key = extraKey(extra);
            const info = (data?.laps || []).find((lap) => lap.key === key);
            const skipped = (data?.skipped || []).find((item) => item.key === key);
            return (
              <span key={key} className="inline-flex items-center overflow-hidden rounded-lg border border-foreground/40 text-sm" data-testid="overlay-extra">
                <span className="inline-flex items-center gap-2 px-2.5 py-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: colourOf(key), border: '1px solid currentColor' }} />
                  <span className="font-mono text-xs text-muted-foreground">{tagOf(key)}</span>
                  <span>{info?.label || (extra.source === 'IBT' ? `${extra.filename} · 랩 ${extra.lap}` : extra.filename)}</span>
                  <span className="font-mono text-xs text-muted-foreground">{lapTime(info?.lap_time_s)}</span>
                  {info?.car_name && <span className="text-xs text-muted-foreground">{info.car_name}</span>}
                  {skipped && <span className="text-xs text-destructive">그리지 못함: {skipped.reason}</span>}
                </span>
                <button type="button" aria-label={`${tagOf(key)}을 기준으로`} title="시간 차이의 기준 랩" onClick={() => setBaseKey(key)}
                  className={`border-l border-border px-2 py-1.5 ${baseOf === key ? 'text-warning' : 'text-muted-foreground hover:text-foreground'}`}>★</button>
                <button type="button" aria-label={`${tagOf(key)} 빼기`} onClick={() => removeExtra(key)} className="border-l border-border px-2 py-1.5 text-muted-foreground hover:text-foreground">×</button>
              </span>
            );
          })}
        </div>
      </div>
      <AddLaps otherFiles={otherFiles} references={references} loadLaps={loadLaps} onAdd={addExtra} full={extras.length >= MAX_EXTRA} />
      <div className="overlay-chart-controls flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <span className="text-xs font-semibold text-muted-foreground">채널</span>
        <div className="flex flex-wrap gap-2">
          {(data?.channels || []).map((channel) => (
            <button key={channel.key} type="button" aria-pressed={channels.includes(channel.key)} onClick={() => toggleChannel(channel.key)}
              className={`rounded-lg border px-2.5 py-1.5 text-sm ${channels.includes(channel.key) ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground'}`}>
              {channel.label.split(' (')[0]}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs font-semibold text-muted-foreground">구간</span>
        <select value={corner} onChange={(event) => pickCorner(event.target.value)} className="rounded-lg border border-border bg-background px-2 py-1.5 text-sm" aria-label="코너로 이동">
          <option value="">전체 랩</option>
          {[...corners].sort((a, b) => a.apex_pct - b.apex_pct).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
        {zoomed && <button type="button" onClick={() => { setRange([0, 1]); setHistory([]); setCorner(''); }} className="rounded-lg border border-border px-2.5 py-1.5 text-sm">전체 랩으로</button>}
        {zoomed && <span className="font-mono text-xs text-muted-foreground">{metres(range[0])} ~ {metres(range[1])}</span>}
        {loading && <span className="text-xs text-muted-foreground">불러오는 중…</span>}
      </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {data?.provider === 'LMU' && Object.entries(data.channel_support || {}).filter(([, info]) => info.status !== 'AVAILABLE').map(([key, info]) => <p className="text-sm text-muted-foreground" key={key}>{({ throttle_ratio: '스로틀', brake_ratio: '브레이크', steering_ratio: '조향 입력', gear: '기어' })[key] || key}: {info.reason || '일부 구간의 관측값을 확인할 수 없습니다.'}</p>)}
      {!chosen.length && !extras.length && <p className="text-sm text-muted-foreground">겹쳐 볼 랩을 하나 이상 고르세요.</p>}
      {data && lanes.length > 0 && (
        <div className="grid gap-3 2xl:grid-cols-[minmax(0,1fr)_260px]">
          <div className="min-w-0">
          <div className="overlay-plot-scroll" data-testid="overlay-plot-scroll">
          <svg id="lap-overlay-chart" ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio={fillHeight ? 'none' : 'xMidYMid meet'} style={fillHeight ? { minHeight: minimumPlotHeight } : undefined} className="w-full select-none rounded-lg border border-border bg-muted/20" role="img" aria-label="여러 랩 겹쳐 보기 그래프"
            onMouseMove={(event) => { const i = indexAt(event.clientX); setCursor(i); if (drag) setDrag({ ...drag, to: i }); }}
            onMouseLeave={() => { setCursor(null); setDrag(null); }}
            onMouseDown={(event) => { const i = indexAt(event.clientX); if (i != null) setDrag({ from: i, to: i }); }}
            onMouseUp={() => {
              if (drag && drag.to - drag.from > 3) zoomIn(distances[drag.from], distances[drag.to]);
              else if (drag && drag.from - drag.to > 3 && (range[0] > 0 || range[1] < 1)) zoomOut();
              setDrag(null);
            }}>
            <defs><clipPath id="lap-overlay-plot"><rect x={LEFT} y={0} width={W - LEFT - RIGHT} height={H} /></clipPath></defs>
            {lanes.map((lane) => (
              <g key={lane.channel.key}>
                <rect x={LEFT} y={lane.top} width={W - LEFT - RIGHT} height={lane.height} fill="currentColor" className="text-muted/30" />
                <text x="6" y={lane.top + 12} fontSize="11" fill="currentColor" className="text-foreground">{lane.channel.label.split(' (')[0]}</text>
                <text x="6" y={lane.top + 26} fontSize="10" fill="currentColor" className="text-muted-foreground">{formatValue(lane.hi, lane.channel.unit)} {lane.channel.unit}</text>
                <text x="6" y={lane.top + lane.height - 2} fontSize="10" fill="currentColor" className="text-muted-foreground">{formatValue(lane.lo, lane.channel.unit)}</text>
                {['delta', 'steering'].includes(lane.channel.key) && (
                  <line data-testid={lane.channel.key === 'steering' ? 'overlay-steering-zero-guide' : undefined} x1={LEFT} x2={W - RIGHT} y1={lane.top + lane.height / 2} y2={lane.top + lane.height / 2} stroke="currentColor" className="text-border" strokeDasharray="4 4" />
                )}
                {lapNumbers.map((key) => {
                  const values = data.series[key]?.[lane.channel.key];
                  if (!values) return null;
                  return <path key={key} data-overlay-channel={lane.channel.key} data-testid={`overlay-trace-${lane.channel.key}-${key}`} d={lineFor(values, lane)} fill="none" stroke={colourOf(key)} strokeWidth={key === baseOf ? 2.2 : 1.4} clipPath="url(#lap-overlay-plot)"
                    strokeDasharray={key.includes(':') ? '6 3' : undefined} />;
                })}
              </g>
            ))}
            {cornerMarks.map((item) => (
              <text key={item.id} x={x(item.apex_pct)} y={H - 4} fontSize="10" textAnchor="middle" fill="currentColor" className="text-muted-foreground">{item.short}</text>
            ))}
            {drag && drag.from !== drag.to && (
              <rect x={Math.min(x(distances[drag.from]), x(distances[drag.to]))} y={0} width={Math.abs(x(distances[drag.to]) - x(distances[drag.from]))} height={H} fill="currentColor" className="text-primary/15" />
            )}
            {cursor != null && <line x1={x(distances[cursor])} x2={x(distances[cursor])} y1={0} y2={H} stroke="currentColor" className="text-foreground/60" />}
          </svg>
          </div>
          <PanBar range={range} onChange={(next) => { setRange(next); setCorner(''); }} />
          </div>
          <div className="rounded-lg border border-border p-3 text-sm" data-testid="lap-overlay-readout">
            <p className="mb-2 font-mono text-xs text-muted-foreground">{cursor != null ? `커서 ${metres(distances[cursor])}` : '그래프 위에 마우스를 올리세요'}</p>
            {cursor != null && (
              <div className="overlay-readout-groups">{Array.from({ length: Math.ceil(lapNumbers.length / (readoutGroupSize || lapNumbers.length)) }, (_, group) => {
                const groupLaps = lapNumbers.slice(group * (readoutGroupSize || lapNumbers.length), (group + 1) * (readoutGroupSize || lapNumbers.length));
                return <table key={group} className="w-full font-mono text-xs">
                <thead>
                  <tr className="text-muted-foreground"><th className="text-left font-normal">채널</th>{groupLaps.map((key) => <th key={key} className="text-right font-semibold" style={{ color: colourOf(key) }}>{tagOf(key)}{key === baseOf ? '★' : ''}</th>)}</tr>
                </thead>
                <tbody>
                  {lanes.map((lane) => (
                    <tr key={lane.channel.key}>
                      <td className="pr-2 text-muted-foreground">{lane.channel.label.split(' (')[0]}</td>
                      {groupLaps.map((key) => <td key={key} className="text-right text-foreground">{formatValue(data.series[key]?.[lane.channel.key]?.[cursor], lane.channel.unit)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>; })}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
