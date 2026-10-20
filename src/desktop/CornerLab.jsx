import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowDownWideNarrow, ArrowLeft, ArrowRight, AudioLines, BookOpen, ChevronDown, Columns3, Flag, FlaskConical, Focus, History, Keyboard, Maximize2, Pause, Play, Route, Settings2, Target } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription, EmptyContent } from '../components/ui/empty';
import { Table, TableHeader, TableBody, TableRow, TableCell, TableHead } from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../components/ui/tooltip';
import { cn } from '../lib/utils';
import CornerMap from '../components/CornerMap';
import LapOverlay, { PanBar } from '../components/LapOverlay';
import { CHANNELS, data, laps, length, comparisonFor, loadOverlay, signed, number, lapTime, distanceDifference, cornerLabel, readNames, activateSession, labRequest, rememberSession, currentSessionResult } from './sessionData';
import { TelemetryChart, TrackMap, ControlLegend } from './Telemetry';
import CornerNamesPage from './CornerNamesPage';
import LapAnalysis from './LapAnalysis';
import SourcePicker from './SourcePicker';
import Insights from './Insights';
import Measurements from './Measurements';
import DrivingSummary from './DrivingSummary';
import SetupWorkbench from './SetupWorkbench';
import UtilityWorkspace, { workspaceLabels } from './UtilityWorkspace';
import { RuntimeControls, useLabControls } from './RuntimePreview';
import { workspaceFromHash } from './labControlState';
import { cornerAnnotations, cornerAtWindow } from './cornerAnnotations';
import { timeline, heldPositionAt, replayPair } from '../lib/ghost';
import { createReplayMotion } from '../lib/replayMotion';
import { CHANNEL_ORDER_KEY, readChannelOrder, moveChannel, timelineHeights, lapCounts, fileLapLabel, isBestLap, sessionTypeLabel, sessionTypeTone } from './labPreferences';
import AnalysisDetails from './AnalysisDetails';
import { desktopDestination, requestedCornerLocation, openVoiceCorner } from './desktopNavigation';
import WorkspaceNotices from './WorkspaceNotices';

const EMPTY_TRACE = [];

function IconButton({ label, icon: Icon, onClick }) {
  return <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" aria-label={label} onClick={onClick}><Icon /></Button></TooltipTrigger><TooltipContent>{label}</TooltipContent></Tooltip>;
}
function Metric({ title, value, sub, tone }) { return <div className="metric"><span>{title}</span><strong className={tone}>{value}</strong><small>{sub}</small></div>; }
function RailButton({ label, icon: Icon, active, onClick }) {
  return <Button variant={active ? 'secondary' : 'ghost'} className="rail-link" aria-current={active ? 'page' : undefined} onClick={onClick}><Icon /><span>{label}</span></Button>;
}


export default function CornerLab({ analysisWorkspace }) {
  const runtime = useLabControls();
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const adopted = useRef(null);
  const analysis = analysisWorkspace.analysis;
  useEffect(() => {
    if (!analysis || analysis === adopted.current) return undefined;
    const controller = new AbortController();
    const snapshot = analysisWorkspace.analysisSnapshot;
    if (!snapshot?.filename) return undefined;
    setLoading(true); setError('');
    const body = { filename: snapshot.filename, target_lap: analysis.comparison?.target_lap,
      reference_lap: analysis.comparison?.reference_lap, reference_mode: snapshot.referenceMode,
      external_reference_files: snapshot.references || [], session_mode: analysisWorkspace.sessionMode,
      pit_rule: analysisWorkspace.pitRule, user_feedback: snapshot.userFeedback || '',
      feedback_phase: snapshot.feedbackPhase || null, feedback_speed: snapshot.feedbackSpeed || null,
      feedback_symptom: snapshot.feedbackSymptom || null, setup_mode: snapshot.setupMode || 'EXPLORE' };
    labRequest('session', body, controller.signal).then(next => {
      if (controller.signal.aborted) return;
      activateSession(currentSessionResult(next, next.initialComparison, body.external_reference_files)); setVersion(v => v + 1);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // Existing workspace owns all automatic analysis and telemetry event decisions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysis]);
  const accept = next => {
    const result = next.initialComparison;
    const current = currentSessionResult(next, result, next.external);
    adopted.current = current.analysisResult;
    analysisWorkspace.adoptDesktopAnalysis(current, result || {});
    activateSession(current); setVersion(v => v + 1);
  };
  return <Workspace key={version} runtime={runtime} analysisWorkspace={analysisWorkspace}
    busy={loading || analysisWorkspace.loading || analysisWorkspace.initialLoading}
    failure={error || analysisWorkspace.error} onSession={accept} />;
}
function Workspace({ onSession, runtime, analysisWorkspace, busy, failure }) {
  const lap = data.defaultTarget == null ? '' : String(data.defaultTarget);
  const reference = data.defaultReference == null ? '' : String(data.defaultReference);
  const [selected, setSelected] = useState(data.corners[0]?.id || null);
  const [sort, setSort] = useState('track');
  const [tab, setTab] = useState('telemetry');
  const [workspace, setWorkspace] = useState(() => workspaceFromHash(window.location.hash));
  const utility = ['setup', 'live', 'history', 'settings', 'guide'].includes(workspace);
  const [channelMode, setChannelMode] = useState('basic');
  const [focus, setFocus] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [practice, setPractice] = useState(null);
  const [notice, setNotice] = useState('실제 IBT 측정값 · GPS 주행선을 비교하세요.');
  const [position, setPosition] = useState(0);
  const [graphPlaying, setGraphPlaying] = useState(false);
  const [graphTime, setGraphTime] = useState(null);
  const graphClock = useRef(null);
  const listRef = useRef(null);
  const timelineRef = useRef(null);
  const [pedalOverlay, setPedalOverlay] = useState(false);
  const [activeNote, setActiveNote] = useState(null);
  const [channelOrder, setChannelOrder] = useState(readChannelOrder);
  const [draggingChannel, setDraggingChannel] = useState(null);
  const channelDrag = useRef(null);
  useEffect(() => { try { localStorage.setItem(CHANNEL_ORDER_KEY, JSON.stringify(channelOrder)); } catch { setNotice('채널 순서를 브라우저에 저장하지 못했습니다.'); } }, [channelOrder]);
  useEffect(() => {
    const move = event => {
      const start = channelDrag.current;
      if (!start || Math.abs(event.clientY - start.y) < 5) return;
      event.preventDefault();
      const row = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-channel-row]');
      if (row) setChannelOrder(order => moveChannel(order, start.key, row.dataset.channelRow));
    };
    const stop = () => { channelDrag.current = null; setDraggingChannel(null); };
    window.addEventListener('pointermove', move, { passive: false }); window.addEventListener('pointerup', stop); window.addEventListener('pointercancel', stop); window.addEventListener('blur', stop);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); window.removeEventListener('pointercancel', stop); window.removeEventListener('blur', stop); };
  }, []);
  const [names, setNames] = useState(readNames);
  const [namesPage, setNamesPage] = useState(window.location.hash === '#corner-names');
  useEffect(() => { const change = () => setNamesPage(window.location.hash === '#corner-names'); window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  useEffect(() => { const change = () => { if (window.location.hash !== '#corner-names') setWorkspace(workspaceFromHash(window.location.hash)); }; window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  const navigateDesktop = useCallback(section => {
    const destination = desktopDestination(section);
    setWorkspace(destination.workspace); setNamesPage(Boolean(destination.names));
    window.history.replaceState(null, '', `#${destination.names ? 'corner-names' : destination.workspace}`);
    window.dispatchEvent(new Event('hashchange'));
    if (destination.anchor) requestAnimationFrame(() => requestAnimationFrame(() => {
      document.getElementById(destination.anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
  }, []);
  function changeWorkspace(value) { navigateDesktop(value); }
  useEffect(() => {
    const navigate = event => navigateDesktop(event.detail);
    window.addEventListener('coach-navigate', navigate); window.addEventListener('coach-reveal-section', navigate);
    return () => { window.removeEventListener('coach-navigate', navigate); window.removeEventListener('coach-reveal-section', navigate); };
  }, [navigateDesktop]);
  const external = data.external || EMPTY_TRACE;
  const comparison = useMemo(() => comparisonFor(), []);
  const [overlaySources, setOverlaySources] = useState({ files: [], references: [] });
  const [lapRange, setLapRange] = useState(null);
  const [chosenLaps, setChosenLaps] = useState(laps.filter(l => l.status === 'VALID').map(l => String(l.lap_number)));
  const [lapTab, setLapTab] = useState('time');
  useEffect(() => {
    if (!data.filename) return;
    let live = true;
    Promise.all([labRequest('files'), labRequest('references', { filename: data.filename })]).then(([catalog, refs]) => {
      const sameTrack = f => f.track_platform_id && data.session.track_platform_id ? String(f.track_platform_id) === String(data.session.track_platform_id) : f.track_name === data.session.track_name && f.track_config === data.session.track_config;
      if (live) setOverlaySources({ files: catalog.files.filter(f => f.name !== data.filename && f.analyzable !== false && sameTrack(f)), references: (refs.files || []).filter(r => r.usable !== false && r.compatible !== false).map(r => ({ ...r, label: `${r.driver_name || 'Garage61'} · ${lapTime(r.lap_time_s)}` })) });
    }).catch(e => { if (live) setNotice(e.message); });
    return () => { live = false; };
  }, []);
  const loadLaps = useMemo(() => filename => labRequest('file-laps', { filename }), []);
  function applyComparison(session, target, base, files, result) {
    rememberSession(session, target, base, files);
    if (session !== data) {
      onSession({ ...session, defaultTarget: Number(target), defaultReference: base ? Number(base) : null, external: files, initialComparison: result });
    } else {
      onSession({ ...session, defaultTarget: Number(target), defaultReference: base ? Number(base) : null, external: files, initialComparison: result });
    }
  }
  const { corners, paths, loss } = comparison;
  const referenceCount = comparison.aggregate?.reference_analysis_weights?.length || external.length;
  const corner = corners.find(item => item.id === selected);
  const ordered = useMemo(() => [...corners].sort((a, b) => sort === 'loss' ? (b.delta_s ?? -Infinity) - (a.delta_s ?? -Infinity) : a.apex_pct - b.apex_pct), [corners, sort]);
  const notes = useMemo(() => cornerAnnotations(corner, comparison.rows, length, comparison.controlEvents), [corner, comparison.rows, comparison.controlEvents]);
  const worst = [...corners].sort((a, b) => (b.delta_s ?? -Infinity) - (a.delta_s ?? -Infinity))[0] || { id: '', delta_s: null, target: {}, reference: {} };
  const cornerRange = useMemo(() => corner ? [Math.max(0, corner.entry_pct - 75 / length), Math.min(1, corner.exit_pct + 75 / length)] : [0, 1], [corner]);
  const [customRange, setCustomRange] = useState(null);
  const history = useRef([]);
  const rangePct = customRange || cornerRange;
  const range = useMemo(() => rangePct.map(value => value * length), [rangePct]);
  const changeWindow = next => { setCustomRange(next); setSelected(cornerAtWindow(corners, next, selected)); setPosition((next[0] + next[1]) / 2 * length); setActiveNote(null); };
  const setChartRange = (next, remember = false) => { if (remember) history.current.push(rangePct); changeWindow(next.map(v => v / length)); };
  const zoomBack = (dragFraction = .25) => {
    const previous = history.current.pop();
    if (previous) return changeWindow(previous);
    const [from, to] = rangePct;
    const width = to - from;
    const expansion = Math.min(1.75, Math.max(.12, dragFraction * 1.7));
    const nextWidth = Math.min(1, width * (1 + expansion));
    const center = (from + to) / 2;
    const nextStart = Math.max(0, Math.min(1 - nextWidth, center - nextWidth / 2));
    return changeWindow([nextStart, nextStart + nextWidth]);
  };
  useEffect(() => { setCustomRange(null); history.current = []; setActiveNote(null); setPosition(length * .5); }, [comparison]);
  useEffect(() => { setPosition(value => Math.max(range[0], Math.min(range[1], value))); }, [range]);
  const targetTimeline = useMemo(() => timeline(paths.target), [paths.target]);
  const referenceTimeline = useMemo(() => timeline(paths.reference), [paths.reference]);
  const scopedReplay = rangePct[0] !== 0 || rangePct[1] !== 1;
  const graphReplay = useMemo(() => replayPair(targetTimeline, referenceTimeline, scopedReplay ? rangePct : null), [targetTimeline, referenceTimeline, rangePct, scopedReplay]);
  const graphTarget = graphReplay?.target || EMPTY_TRACE, graphReference = graphReplay?.reference || EMPTY_TRACE;
  const graphSpan = graphReplay?.span;
  const graphMotion = useMemo(() => createReplayMotion(), []);
  const graphGhost = useMemo(() => Number.isFinite(graphTime) ? {
    target: heldPositionAt(graphTarget, graphTime)?.pct * length,
    reference: heldPositionAt(graphReference, graphTime)?.pct * length,
  } : null, [graphTime, graphTarget, graphReference]);
  useEffect(() => {
    if (!graphPlaying || !graphSpan) return undefined;
    let frame = 0, last = null, committed = 0;
    const tick = now => {
      const next = Math.min(graphSpan[1], (graphClock.current ?? graphSpan[0]) + (last === null ? 0 : (now - last) / 1000));
      last = now; graphClock.current = next;
      graphMotion.publish({ target: heldPositionAt(graphTarget, next)?.pct * length,
        reference: heldPositionAt(graphReference, next)?.pct * length });
      const done = next >= graphSpan[1];
      if (done || now - committed >= 100) {
        committed = now; setGraphTime(next);
        const here = heldPositionAt(graphTarget, next);
        if (here) setPosition(Math.max(range[0], Math.min(range[1], here.pct * length)));
      }
      if (done) setGraphPlaying(false); else frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [graphPlaying, graphSpan, graphTarget, graphReference, graphMotion, range]);
  useEffect(() => { setGraphPlaying(false); setGraphTime(null); graphClock.current = null; graphMotion.publish(null); }, [comparison, selected, customRange, workspace, tab, namesPage, graphMotion]);
  function playGraph() {
    if (graphPlaying) { setGraphTime(graphClock.current); setGraphPlaying(false); return; }
    if (!graphSpan) return;
    if (!Number.isFinite(graphClock.current) || graphClock.current >= graphSpan[1]) {
      graphClock.current = graphSpan[0]; setGraphTime(graphSpan[0]);
      graphMotion.publish({ target: heldPositionAt(graphTarget, graphSpan[0])?.pct * length,
        reference: heldPositionAt(graphReference, graphSpan[0])?.pct * length });
    }
    setGraphPlaying(true);
  }
  function moveCursor(next) { setGraphPlaying(false); setGraphTime(null); graphClock.current = null; graphMotion.publish(null); setPosition(next); }
  const rows = useMemo(() => {
    const visible = comparison.rows.filter(row => row.distance >= range[0] && row.distance <= range[1]);
    if (!scopedReplay) return visible;
    // Use the chart's own baseline (which may be a Garage61 aggregate), not the map's representative lap.
    const afterIndex = comparison.rows.findIndex(row => row.distance >= range[0]);
    const after = comparison.rows[afterIndex], before = comparison.rows[Math.max(0, afterIndex - 1)];
    const offset = after && before && Number.isFinite(after.delta) && Number.isFinite(before.delta)
      ? before.delta + (after.delta - before.delta) * (after.distance === before.distance ? 0 : (range[0] - before.distance) / (after.distance - before.distance)) : null;
    return visible.map(row => ({ ...row, delta: Number.isFinite(offset) && Number.isFinite(row.delta) ? row.delta - offset : null }));
  }, [comparison.rows, range, scopedReplay]);
  const channelCatalog = [...CHANNELS, { key: 'pedals', label: '페달', unit: '%', domain: [0, 100] }];
  const channels = channelOrder.map(key => channelCatalog.find(c => c.key === key)).filter(item => item && (channelMode === 'all' || !['steering', 'gear'].includes(item.key)) && (pedalOverlay ? !['brake', 'throttle'].includes(item.key) : item.key !== 'pedals')).map(item => item.key === 'delta' && scopedReplay ? { ...item, label: '구간 내 차이' } : item);
  const bars = timelineHeights(corners);
  const isOwnBest = isBestLap(data.laps.find(l => String(l.lap_number) === lap && l.status === 'VALID'), data.laps);
  const isReferenceBest = !external.length && isBestLap(data.laps.find(l => String(l.lap_number) === reference && l.status === 'VALID'), data.laps);
  useEffect(() => {
    const item = listRef.current?.querySelector('[aria-pressed="true"]');
    if (item) { const list = listRef.current; if (item.offsetTop < list.scrollTop) list.scrollTop = item.offsetTop; else if (item.offsetTop + item.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = item.offsetTop + item.offsetHeight - list.clientHeight; }
  }, [ordered, selected, workspace]);
  useEffect(() => {
    const host = timelineRef.current;
    const item = host?.querySelector('[aria-pressed="true"]');
    if (host && item) host.scrollLeft = item.offsetLeft + item.offsetWidth / 2 - host.clientWidth / 2;
  }, [selected, workspace, corners]);
  useEffect(() => {
    const host = timelineRef.current;
    if (!host) return undefined;
    const scroll = event => {
      if (host.scrollWidth <= host.clientWidth) return;
      event.preventDefault(); host.scrollLeft += event.deltaY + event.deltaX;
    };
    host.addEventListener('wheel', scroll, { passive: false });
    return () => host.removeEventListener('wheel', scroll);
  }, [workspace]);
  const choose = useCallback(id => { setSelected(id); setCustomRange(null); history.current = []; setActiveNote(null); setPosition((corners.find(c => c.id === id)?.apex_pct ?? .5) * length); }, [corners]);
  const appliedCornerRequest = useRef(null);
  useEffect(() => {
    const request = analysisWorkspace.requestedCorner;
    if (!request || appliedCornerRequest.current === request || busy || (request.lap_number != null && Number(request.lap_number) !== Number(lap))) return;
    const location = requestedCornerLocation(corners, request);
    if (!location) return;
    appliedCornerRequest.current = request;
    choose(location.id); navigateDesktop('corners'); setTab('telemetry'); setPosition(location.pct * length);
    if (location.pct < location.corner.entry_pct || location.pct > location.corner.exit_pct) {
      setCustomRange([Math.max(0, Math.min(location.corner.entry_pct, location.pct) - 75 / length), Math.min(1, Math.max(location.corner.exit_pct, location.pct) + 75 / length)]);
    }
  }, [analysisWorkspace.requestedCorner, busy, corners, choose, lap, navigateDesktop]);
  const showVoiceCorner = note => openVoiceCorner(analysisWorkspace, note);
  function jumpToNote(note) { const at = note.jumpAt ?? note.at; if (!Number.isFinite(at)) return; moveCursor(at * length); setActiveNote(note.id); setTab('telemetry'); if (['steering', 'gear'].includes(note.channel)) setChannelMode('all'); if (at * length < range[0] || at * length > range[1]) { setCustomRange([Math.max(0, Math.min(cornerRange[0], at - 30 / length)), Math.min(1, Math.max(cornerRange[1], at + 30 / length))]); } }
  const step = useCallback(direction => { if (!corners.length) return; const index = corners.findIndex(item => item.id === selected); choose(corners[(index + direction + corners.length) % corners.length].id); }, [corners, selected, choose]);
  useEffect(() => {
    function keydown(event) {
      if (dialog || namesPage || workspace !== 'corners' || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.target.closest('button, input, select, textarea, [role="tab"], [role="button"], [role="radio"], [role="slider"], [role="scrollbar"], [contenteditable]')) return;
      if (corners.length && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) { event.preventDefault(); step(event.key === 'ArrowRight' ? 1 : -1); }
      if (event.key.toLowerCase() === 'f') setFocus(value => !value);
    }
    window.addEventListener('keydown', keydown); return () => window.removeEventListener('keydown', keydown);
  }, [corners, selected, dialog, namesPage, workspace, step]);
  async function fullscreen() { try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); } catch { setNotice('F11로 전체화면으로 전환할 수 있습니다.'); } }
  const openNames = () => { window.location.hash = 'corner-names'; };
  const release = corner ? distanceDifference(corner.target.brake_release_pct, corner.reference.brake_release_pct) : null;
  const throttle = corner ? distanceDifference(corner.target.full_throttle_pct, corner.reference.full_throttle_pct) : null;
  const mapProps = { projected: paths.target, referenceProjected: paths.reference, geographic: paths.geographic, geographicOrigin: paths.origin, referenceLineSource: comparison.reference_line_source, geographicMetersPerUnit: paths.metersPerUnit, corners, selected: corner, selectedRange: scopedReplay ? rangePct : null, activePct: position / length, onSelect: choose, onHover: pct => { if (Number.isFinite(pct)) setPosition(pct * length); }, onFullLap: () => choose(null), referenceLabel: comparison.reference_label || `${reference}랩`, loss, traceRows: comparison.rows };

  return <TooltipProvider delayDuration={150}><div className={cn('corner-lab', focus && workspace === 'corners' && 'focus-mode')}>
    <header className="lab-header"><div className="lab-brand"><div className="brand-symbol"><span /><span /><span /></div><strong role="heading" aria-level="1">iRacing <span>주행 코치</span></strong><span className="header-divider" /><span className="breadcrumb">주행 분석 <span>/</span> <b>{namesPage ? '코너 번호 확인' : workspaceLabels[workspace]}</b></span></div><RuntimeControls runtime={runtime} /><div className="header-actions">{analysisWorkspace.analysis && <AnalysisDetails workspace={analysisWorkspace} onNavigate={changeWorkspace} />}<Badge variant="outline">1.4.1</Badge><IconButton label="전체화면 · F11" icon={Maximize2} onClick={fullscreen} /></div></header>
    {namesPage ? <CornerNamesPage corners={corners} points={paths.target} names={names} onSaved={setNames} initialSelected={selected} onBack={() => changeWorkspace('corners')} /> : <div className="lab-body"><aside className="app-rail" aria-label="작업 보기"><div className="rail-upper">{[[Target, 'summary'], [Route, 'corners'], [Activity, 'laps'], [Columns3, 'expert'], [FlaskConical, 'setup'], [AudioLines, 'live']].map(([Icon, key]) => <RailButton key={key} label={workspaceLabels[key]} icon={Icon} active={workspace === key} onClick={() => changeWorkspace(key)} />)}</div><div className="rail-lower">{[[History, 'history'], [Settings2, 'settings'], [BookOpen, 'guide']].map(([Icon, key]) => <RailButton key={key} label={workspaceLabels[key]} icon={Icon} active={workspace === key} onClick={() => changeWorkspace(key)} />)}<span className="rail-version">LOCAL</span></div></aside>
    <main className={cn('lab-main', workspace !== 'corners' && 'wide-main', utility && 'utility-main', !utility && !data.filename && 'empty-main', workspace === 'live' && 'voice-main', workspace === 'setup' && 'setup-main')} key={utility ? workspace : 'analysis'}>
      {workspace === 'setup' ? <SetupWorkbench analysisWorkspace={analysisWorkspace} onChooseSource={() => setDialog('laps')} onCorner={id => { choose(id); changeWorkspace('corners'); }} /> : utility ? <UtilityWorkspace analysisWorkspace={analysisWorkspace} workspace={workspace} runtime={runtime} onNavigate={navigateDesktop} onOpenCorner={showVoiceCorner} /> : !data.filename ? <Empty className="desktop-empty"><EmptyHeader><EmptyTitle role="heading" aria-level={2}>분석할 주행을 선택하세요.</EmptyTitle><EmptyDescription>{busy ? '로컬 주행 데이터를 불러오고 있습니다.' : failure || '설정에서 IBT 폴더를 확인하고 주행 파일을 선택하세요.'}</EmptyDescription></EmptyHeader><EmptyContent className="max-w-none flex-row flex-wrap justify-center"><Button onClick={() => setDialog('laps')}>주행 선택</Button><Button variant="outline" onClick={() => changeWorkspace('guide')}>처음 사용 가이드 열기</Button><Button variant="outline" onClick={() => changeWorkspace('settings')}>연결 상태 확인</Button></EmptyContent></Empty> : <>
      <section className="session-bar" aria-label="비교 세션"><div className="session-title"><div className="eyebrow">LOCAL TELEMETRY / IBT</div><h1>{data.session.track_name}</h1><div className="session-subtitle">{data.session.car_name} <span>·</span> {data.session.track_config} <Badge variant="outline" className={`session-badge session-${sessionTypeTone(data.session)}`}>{sessionTypeLabel(data.session)}</Badge></div></div><div className="lap-pair"><button className={cn("lap-choice", isOwnBest && "best-lap-choice")} onClick={() => setDialog('laps')} aria-label="비교할 내 랩 변경"><span className="lap-label"><i className="target-swatch" />내 주행 <b>{lap}랩{isOwnBest && " ★ 베스트"}</b><ChevronDown size={14} /></span><strong>{lapTime(comparison.targetTime)}</strong></button><span className="versus">vs</span><button className={cn("lap-choice", isReferenceBest && "best-lap-choice")} onClick={() => setDialog('laps')} aria-label="기준 랩 변경"><span className="lap-label"><i className="reference-swatch" />{external.length ? `G61 선택 ${external.length}랩` : (reference ? `기준 ${reference}랩${isReferenceBest ? ' ★ 베스트' : ''}` : '기준 없음')}<ChevronDown size={14} /></span><strong>{lapTime(comparison.referenceTime)}</strong></button><div className="lap-total"><span>전체 랩 차이</span><strong className={cn(comparison.delta < 0 && 'gain')}>{signed(comparison.delta)}<small> s</small></strong></div></div><Button variant="outline" size="sm" onClick={() => setDialog('laps')}><Settings2 data-icon="inline-start" />비교 변경</Button></section>
      <div className="workspace-navigation"><Tabs value={workspace} onValueChange={changeWorkspace}><TabsList aria-label="작업 화면"><TabsTrigger value="summary">주행 요약</TabsTrigger><TabsTrigger value="corners">코너 비교</TabsTrigger><TabsTrigger value="laps">랩 분석</TabsTrigger><TabsTrigger value="expert">전문가 보기</TabsTrigger></TabsList></Tabs><span>{fileLapLabel(lapCounts(data.laps))} · 자동 구간 {corners.length}개</span><Button variant="outline" size="sm" onClick={openNames}>코너 번호 확인<ArrowRight data-icon="inline-end" /></Button></div>
      {workspace === 'summary' ? <DrivingSummary analysisWorkspace={analysisWorkspace} key={JSON.stringify([lap, reference, external])} comparison={comparison} names={names} context={JSON.stringify([data.filename || '', data.session.track_platform_id, lap, reference, external])} onCompare={() => setDialog('laps')} onCorner={id => { choose(id); changeWorkspace('corners'); }} /> : workspace === 'laps' ? <LapAnalysis target={lap} names={names} range={lapRange} onRange={setLapRange} chosen={chosenLaps} onChosen={setChosenLaps} tab={lapTab} onTab={setLapTab} onCorner={id => { choose(id); changeWorkspace('corners'); }} /> : workspace === 'expert' ? <section className="wide-workspace expert-workspace"><div className="wide-heading"><div><h2>전문가 보기</h2><p>여러 랩을 겹쳐 보고 채널·기준 랩을 바꿔 비교하세요.</p></div><Badge variant="outline">{data.laps.length}랩 · 외부 랩 추가 가능</Badge></div><LapOverlay laps={data.kind === 'LIVE_LOCAL' ? data.laps : laps} corners={corners.map(c => ({ ...c, label: cornerLabel(c, names) }))} preferredLaps={[Number(lap), Number(reference)]} load={loadOverlay} otherFiles={overlaySources.files} references={overlaySources.references} loadLaps={loadLaps} fillHeight readoutGroupSize={3} /></section> : <>
      <div className="workspace-grid"><aside className="corner-sidebar" aria-label="코너 목록"><div className="sidebar-heading"><h2>코너 탐색</h2><Badge variant="secondary">{corners.length}구간</Badge></div><ToggleGroup type="single" size="sm" variant="outline" value={sort} onValueChange={value => value && setSort(value)} aria-label="코너 정렬" className="sort-controls"><ToggleGroupItem value="track">주행순</ToggleGroupItem><ToggleGroupItem value="loss">손실순</ToggleGroupItem></ToggleGroup><Button variant={!corner ? 'secondary' : 'ghost'} size="sm" className="full-lap-button" aria-pressed={!corner} onClick={() => choose(null)}><span className="flex items-center gap-2"><Flag />전체 랩</span><span className="numeric">{signed(comparison.delta)}</span></Button><div className="corner-list" ref={listRef} aria-label="코너별 시간 차이 목록">{ordered.map(item => <button key={item.id} aria-pressed={item.id === selected} className={cn('corner-item', item.id === selected && 'selected')} onClick={() => choose(item.id)}><span className="corner-number">{item.id}</span><span className="corner-name"><b>{cornerLabel(item, names)}</b><small>{Math.round(item.apex_pct * length).toLocaleString()} m</small></span><span className={cn('corner-delta numeric', item.delta_s < 0 && 'gain')}>{signed(item.delta_s)}<i style={{ width: `${Math.min(100, Math.max(8, Math.abs(item.delta_s || 0) / Math.max(.01, worst.delta_s) * 100))}%` }} /></span></button>)}</div><div className="sidebar-bottom"><span>C = 자동 감지 구간</span><span>+ 느림 / − 빠름</span></div></aside>
      <section className="analysis-desk" aria-label="선택 구간 분석"><div className="selection-heading"><div><div className="eyebrow">{corner ? 'CORNER COMPARISON' : 'LAP OVERVIEW'}</div><h2>{corner ? <><span>{corner.id}</span>{cornerLabel(corner, names)}</> : '전체 랩 비교'}{corner?.id === worst.id && worst.delta_s > 0 && <Badge variant="secondary">가장 큰 손실</Badge>}</h2></div><div className="flex gap-1"><IconButton label="이전 코너" icon={ArrowLeft} onClick={() => step(-1)} /><IconButton label="다음 코너" icon={ArrowRight} onClick={() => step(1)} /><IconButton label="그래프 집중 모드 · F" icon={focus ? Columns3 : Focus} onClick={() => setFocus(v => !v)} /></div></div>
      <section className="metrics-strip" aria-label="선택 구간 핵심 수치"><Metric title={corner ? '구간 시간 차이' : '전체 랩 시간 차이'} value={`${signed(corner ? corner.delta_s : comparison.delta)} s`} tone={(corner ? corner.delta_s : comparison.delta) < 0 ? 'gain' : 'loss'} sub={external.length ? `Garage61 적용 ${referenceCount}랩 대비` : `기준 ${reference}랩 대비`} /><Metric title={corner ? '최저 속도' : '가장 큰 시간 차이'} value={corner ? `${number(corner.target.min_speed_kph)} km/h` : worst.id} sub={corner ? `기준 ${number(corner.reference.min_speed_kph)} km/h` : `${signed(worst.delta_s)} s`} /><Metric title={corner ? '제동 해제 차이' : 'GPS 주행 길이'} value={corner ? `${signed(release, 0)} m` : `${(length / 1000).toFixed(3)} km`} sub={corner ? '+ 기준보다 뒤 / − 앞' : 'IBT 기록 기준'} /><Metric title={corner ? '풀스로틀 차이' : '기준보다 빠른 구간'} value={corner ? `${signed(throttle, 0)} m` : `${corners.filter(c => c.delta_s < 0).length}구간`} sub={corner ? '+ 기준보다 뒤 / − 앞' : '측정된 코너 통과 시간'} /></section>
      <Tabs value={tab} onValueChange={setTab} className="analysis-tabs"><div className="trace-toolbar"><TabsList aria-label="비교 보기"><TabsTrigger value="telemetry">조작 비교</TabsTrigger><TabsTrigger value="line">GPS · 고스트</TabsTrigger><TabsTrigger value="metrics">상세 수치</TabsTrigger></TabsList><div className="trace-legend"><span><i className="target-swatch" />{lap}랩</span><span><i className="reference-swatch" />{external.length ? `G61 적용 ${referenceCount}랩` : `${reference}랩`}</span></div>{tab === 'telemetry' && <Button variant={graphPlaying ? 'secondary' : 'outline'} size="sm" data-testid="graph-ghost-play" disabled={!graphSpan} aria-pressed={graphPlaying} title={graphSpan ? (scopedReplay ? '표시 구간 시작에서 동시 출발 · 실선 내 랩 / 점선 기준 랩' : '실측 랩 경과 시간 기준 · 실선 내 랩 / 점선 기준 랩') : '재생할 시간 자료가 없습니다.'} onClick={playGraph}>{graphPlaying ? <Pause data-icon="inline-start" /> : <Play data-icon="inline-start" />}{graphPlaying ? '재생 일시정지' : '그래프 고스트 재생'}</Button>}<Button variant={pedalOverlay ? 'secondary' : 'outline'} size="sm" aria-pressed={pedalOverlay} onClick={() => setPedalOverlay(v => !v)}>페달 겹쳐 보기</Button><Button variant="ghost" size="sm" onClick={() => setChannelMode(v => v === 'all' ? 'basic' : 'all')}><Activity data-icon="inline-start" />{channelMode === 'all' ? '기본 채널' : '조향·기어 추가'}</Button></div>
      <TabsContent value="telemetry" className="telemetry-panel"><div className="chart-context"><span>오른쪽 드래그: 확대 · 왼쪽: 이전 범위 / 추가 축소 · 휠: 구간 이동</span><Button variant="ghost" size="sm" onClick={() => { setCustomRange(null); history.current = []; setGraphPlaying(false); }}>구간 맞춤</Button><span>{Math.round(position).toLocaleString()} m</span></div><div className="chart-note-legend"><span>번호 = 비교 포인트 · ABS: 내 랩 노랑·빨강 선 / 기준 음영</span><ControlLegend controls={comparison.controlEvents} range={range} />{pedalOverlay && <span className="pedal-legend"><i className="brake-key" />브레이크<i className="throttle-key" />스로틀</span>}</div><div className="chart-stack" id="lap-overlay-chart">{channels.map((channel, i) => <TelemetryChart key={channel.key} channel={channel} dragging={draggingChannel === channel.key} onLabelDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.focus(); channelDrag.current = { key: channel.key, y: event.clientY }; setDraggingChannel(channel.key); }} onNudge={direction => { const index = channels.findIndex(c => c.key === channel.key); const other = channels[index + direction]; if (other) setChannelOrder(order => moveChannel(order, channel.key, other.key)); }} rows={rows} position={position} onPosition={moveCursor} ghost={graphGhost} ghostMotion={graphMotion} range={range} apex={corner ? corner.apex_pct * length : null} onRange={setChartRange} onZoomBack={zoomBack} notes={notes} controls={comparison.controlEvents} activeNote={activeNote} onNote={jumpToNote} last={i === channels.length - 1} />)}</div><PanBar range={rangePct} onChange={changeWindow} /></TabsContent>
      <TabsContent value="line" className="line-panel"><CornerMap key={`${lap}-${reference}-${external.join()}`} {...mapProps} compactControls /></TabsContent>
      <TabsContent value="metrics" className="details-panel"><h3>{corner ? `${cornerLabel(corner, names)} · 수치 비교` : '코너별 수치 비교'}</h3><p>IBT 측정값 · — 표시는 측정값 없음</p><Table><TableHeader><TableRow><TableHead>구간</TableHead><TableHead>시간 차이</TableHead><TableHead>최저 속도 · 내 랩</TableHead><TableHead>최저 속도 · 기준</TableHead><TableHead>제동 해제 차이</TableHead></TableRow></TableHeader><TableBody>{(corner ? [corner] : ordered).map(item => <TableRow key={item.id}><TableCell>{item.id}</TableCell><TableCell>{signed(item.delta_s)} s</TableCell><TableCell>{number(item.target.min_speed_kph)} km/h</TableCell><TableCell>{number(item.reference.min_speed_kph)} km/h</TableCell><TableCell>{signed(distanceDifference(item.target.brake_release_pct, item.reference.brake_release_pct), 0)} m</TableCell></TableRow>)}</TableBody></Table>{corner && <Measurements corner={corner} quality={comparison.quality} warnings={comparison.warnings} />}{corner && <div className="detail-notes"><h4>측정 근거</h4>{corner.evidence.map((text, i) => <p key={i}>{text}</p>)}<p>연료·타이어·노면 조건이 다르면 차이를 셋업 효과로 단정하지 않습니다.</p></div>}{!corner && <p>전체 {corners.length}구간입니다. 표와 코너 목록 안에서 스크롤할 수 있습니다.</p>}</TabsContent></Tabs>
      {tab !== 'line' && <div className="cursor-control"><label htmlFor="distance-cursor">비교 위치</label><input id="distance-cursor" type="range" min={range[0]} max={range[1]} step="1" value={position} onChange={event => moveCursor(Number(event.target.value))} /><output htmlFor="distance-cursor">{Math.round(position).toLocaleString()} m</output></div>}</section>
      <aside className="insight-sidebar" aria-label="지도와 다음 연습"><section className="map-panel"><div className="panel-heading"><h2>트랙 위치</h2><Button variant="ghost" size="sm" onClick={() => setTab('line')}>지도·고스트 열기<ArrowRight data-icon="inline-end" /></Button></div><TrackMap points={paths.target} loss={loss} corners={corners} selected={corner} onSelect={choose} position={position} names={names} /></section><Insights notes={notes} activeNote={activeNote} onNote={jumpToNote} corner={corner} corners={corners} names={names} onJump={(id, pct) => { choose(id); setTab('telemetry'); setCustomRange(null); requestAnimationFrame(() => setPosition(pct * length)); }} practice={practice} onPractice={id => { setPractice(practice === id ? null : id); setNotice(`${id} 연습 구간을 선택했습니다. 주행 요약에서 과제를 시작하세요.`); }} /></aside></div>
      <section className="lap-timeline" aria-label="한눈에 보는 전체 코너 손실"><div className="timeline-heading"><strong>한 랩 한눈에</strong><span>자동 구간별 시간 차이 · 초</span><Button variant="ghost" size="sm" onClick={() => { choose(worst.id); setSort('loss'); }}><ArrowDownWideNarrow />가장 큰 손실로 이동</Button></div><div className="timeline-scroll" ref={timelineRef} role="region" aria-label="전체 자동 구간 탐색" tabIndex={0}><div className="timeline-corners" style={{ gridTemplateColumns: `repeat(${corners.length}, minmax(48px, 1fr))` }}>{corners.map((item, index) => <button key={item.id} aria-label={`타임라인 ${item.id} 선택`} aria-pressed={item.id === selected} onClick={() => choose(item.id)} className={cn('timeline-cell', item.delta_s < 0 && 'is-gain', item.id === selected && 'selected')}><span>{item.id}</span><div className="timeline-bar"><i style={{ height: `${bars[index]}px` }} /></div><strong>{signed(item.delta_s, 2)}</strong></button>)}</div></div></section></>}
    </> }</main></div>}
    <footer className="lab-footer"><span><i />로컬 앱 · {busy ? "불러오는 중" : "측정값 기반"}</span><span role="status" className="footer-notice">{failure || notice}</span><WorkspaceNotices workspace={analysisWorkspace} busy={busy} onNavigate={navigateDesktop} /><span><Keyboard size={13} />← → 코너 이동 <kbd>F</kbd> 그래프 집중</span></footer>
    <Dialog open={dialog === 'laps'} onOpenChange={open => !open && setDialog(null)}><DialogContent className="max-w-xl"><DialogHeader><DialogTitle>비교할 주행 선택</DialogTitle><DialogDescription>내 IBT 기록과 내려받은 Garage61 기준을 선택하세요.</DialogDescription></DialogHeader><SourcePicker analysisWorkspace={analysisWorkspace} target={lap} reference={reference} external={external} onApply={applyComparison} /></DialogContent></Dialog>
  </div></TooltipProvider>;
}

