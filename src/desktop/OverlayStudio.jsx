import React, { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Layers } from 'lucide-react';
import { Target } from 'lucide-react';
import { Timer } from 'lucide-react';
import { Flag } from 'lucide-react';
import { Activity } from 'lucide-react';
import { Settings2 } from 'lucide-react';
import { Route } from 'lucide-react';
import { ArrowUpRight } from 'lucide-react';
import { Check } from 'lucide-react';
import { RotateCcw } from 'lucide-react';
import { Expand } from 'lucide-react';
import { Save } from 'lucide-react';
import { CircleDot } from 'lucide-react';
import { ChevronDown } from 'lucide-react';
import { ShieldCheck } from 'lucide-react';
import { CircleHelp } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { Switch } from '../components/ui/switch';
import { Tabs, TabsList, TabsTrigger } from '../components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';
import { Dialog, DialogTrigger, DialogContent, DialogTitle, DialogDescription, DialogHeader } from '../components/ui/dialog';
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from '../components/ui/table';
import { cn } from '../lib/utils';
import { sessions, modules, byId, games, initialConfig, selectedModules, selectModules, moduleTiming, phaseNames, phaseOrder, moduleRequirements, formationSelection, formationDefaults } from './overlayCatalog';
import Panel from './OverlayPanel';
import { CoachingChoices, FormationChoices, FormationSummary } from './OverlayChoices';
import OverlaySettings from '../components/OverlaySettings';
import { useGame } from '../lib/gameContext';
import { API_BASE } from '../analysis/analysisClient';
import { Alert, AlertDescription } from '../components/ui/alert';
import './overlayStudio.css';
import { GraphControls, GraphExtras } from './OverlayInputGraph';
import OverlayNativePreview from './OverlayNativePreview';

const SESSION_IDS = Object.keys(sessions);
const sessionIcons = { practice: Target, qualify: Timer, race: Flag, formation: Route };

function referenceFor(game, config, availability) {
  if (availability === 'none') return { ready: false, label: '비교 기준 없음', message: '조건에 맞는 비교 기록을 기다리고 있습니다.' };
  if (config.source === 'garage61' && (game === 'lmu' || availability !== 'g61')) return { ready: false, label: 'Garage61 기준 없음', message: '지정한 비교 출처의 기록이 없습니다.' };
  const garage = game === 'iracing' && availability === 'g61' && config.source !== 'personal';
  return { ready: true, kind: garage ? 'garage61' : 'personal', label: garage ? 'Garage61 선택 랩' : '내 빠른 기록',
    lap: garage ? '1:37.607' : games[game].lap, fallback: !garage && config.source === 'auto',
    message: garage ? '같은 차량·레이아웃 · 타이어 조건 일부 미확인' : '같은 차량·레이아웃 · 건조 · 연료 차이 6L / 노면 온도 차이 2°C' };
}

function DrivingBackdrop() {
  const prefix = useId();
  return <svg className="driving-backdrop" viewBox="0 0 900 490" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    <defs><linearGradient id={`${prefix}-sky`} x2="0" y2="1"><stop stopColor="#44525a"/><stop offset="1" stopColor="#a0a7a1"/></linearGradient><linearGradient id={`${prefix}-tarmac`} x2="0" y2="1"><stop stopColor="#4a5052"/><stop offset="1" stopColor="#161b21"/></linearGradient></defs>
    <path fill={`url(#${prefix}-sky)`} d="M0 0H900V220H0z"/><path fill="#344c43" d="M0 168L85 131 160 162 280 140 385 179 497 139 575 164 695 127 900 165V325H0z"/>
    <path fill="#49614b" d="M0 205H900V490H0z"/><path fill={`url(#${prefix}-tarmac)`} d="M358 195Q470 234 820 490H0Q190 236 358 195z"/>
    <path d="M358 195Q228 273 44 490M362 196Q520 282 777 490" stroke="#bcb9a8" strokeWidth="4" fill="none"/>
    <path d="M362 205Q390 275 430 490" stroke="#92958d" strokeWidth="3" strokeDasharray="20 32" fill="none" opacity=".32"/>
    <path d="M0 249L330 196M900 253L392 196" stroke="#bfc5c0" strokeWidth="5" opacity=".65"/>
    <path d="M0 395Q450 299 900 395V490H0z" fill="#11161c"/><path d="M0 417Q450 337 900 417" fill="none" stroke="#31383f" strokeWidth="3"/>
    <path d="M275 490Q280 383 450 377Q620 383 625 490" fill="none" stroke="#090c10" strokeWidth="41"/><path d="M302 490Q300 410 450 402Q600 410 598 490" fill="none" stroke="#383c40" strokeWidth="2"/>
    <rect width="900" height="490" fill="#10151d" opacity=".30"/>
  </svg>;
}

const PreviewStage = memo(function PreviewStage({ game, session, config, scene, availability, gallery = false, previewing = true, onPreview, apiBase, fetcher, appearance }) {
  return <div className={cn('preview-stage', gallery && 'gallery-stage')}>
    <DrivingBackdrop />
    <div className="stage-context"><span>{games[game].track}</span><span>주행 배경 예시</span></div>
    <div className="stage-center">{previewing ? <OverlayNativePreview {...{ apiBase, fetcher, game, session, config, appearance, scene, availability }} /> : <div className="preview-off"><Layers aria-hidden="true"/><h3>미리보기가 꺼져 있습니다</h3><p>주행 대기 상태의 예시입니다.</p><Button variant="outline" size="sm" onClick={()=>onPreview(true)}>미리보기 켜기</Button></div>}</div>
    <div className="stage-caption"><span>{games[game].car}</span><small>실제 오버레이 배치 · 수치는 샘플</small></div>
  </div>;
});

function GalleryFooter({config,session,game}) {
  const inputs = config.modes[session] === 'inputs';
  const selected = selectedModules(config, session);
  if (session === 'formation' && !inputs) {
    const formation = formationSelection(config, game);
    return <><span>표시 형태<b>출발 준비 텍스트</b></span><span>항상 표시<b>공식 출발 순위 · 참가 규모</b></span><span>선택 정보<b>{formation.length}개 · 8초 간격 순환</b></span><span>구성<b>{formation.map(item => item.name).join(' · ') || '출발 순위만 표시'}</b></span></>;
  }
  return <><span>표시 형태<b>{inputs?'입력값 그래프':'코칭 카드'}</b></span>{inputs ? <><span>표시 채널<b>{[['throttle','스로틀'],['brake','브레이크'],['steering','조향']].filter(([key])=>config.graph[key]).map(([,label])=>label).join(' · ') || '표시 안 함'}</b></span><span>표시 범위<b>최근 {config.graph.window}초</b></span></> : <><span>선택 항목<b>{selected.length}개 · 상황별 자동 표시</b></span><span>함께 표시<b>큰 정보 1개 + 보조 최대 2개</b></span><span>구성<b>{selected.map(id => byId[id].name).join(' · ') || '표시 안 함'}</b></span></>}</>;
}

function SceneControls({ session, scene, onScene, onGreen }) {
  const choices = session === 'formation' ? [['before', '출발 준비'], ['after', '출발 직전']] : [['start', '과제 없이 첫 주행'], ['before', '코너 접근'], ['after', '통과 결과'], ['lap', '랩 종료'], ...(session === 'race' ? [['traffic', '근접 상황']] : [])];
  return <div className="scene-controls"><span>장면 체험</span><ToggleGroup type="single" value={scene} onValueChange={value => value && onScene(value)} aria-label="샘플 장면">
    {choices.map(([value, text]) => <ToggleGroupItem key={value} value={value}>{text}</ToggleGroupItem>)}
  </ToggleGroup>{session === 'formation' && <Button variant="outline" size="sm" onClick={onGreen}><Flag data-icon="inline-start" />그린 전환 체험</Button>}</div>;
}

function ContentReadiness({ view }) {
  const live = view?.content_readiness, connected = live?.connected === true;
  const count = live?.corner_count, task = view?.practice;
  const practiceSession = ['PRACTICE', 'LIVE'].includes(view?.session?.kind);
  const rows = [
    ['게임 입력', !live ? '확인 전' : !connected ? '연결 대기' : view.driving ? '수신 중' : '연결됨 · 주행 대기'],
    ['코너 구간', !connected || count == null ? '주행 연결 후 확인' : count ? `${count}개 준비됨` : '파일 분석으로 준비'],
    ['비교 기준', !connected ? '주행 연결 후 확인' : view.reference?.state === 'READY' ? '연결됨' : '기준 없이 자동 관측 가능'],
    ['연습 목표 · 선택', !connected ? '연결 후 확인' : !practiceSession ? '연습 세션에서 확인' : task?.phase === 'UNSUPPORTED' ? '목표 측정 기준 확인' : task ? '목표 연결됨' : '연결된 목표 없음 · 자동 관측 가능'],
  ];
  return <Alert className="content-readiness"><AlertDescription>
    <div className="readiness-heading"><div><b>과제 없이 바로 사용하기</b><p>자동 주행 코칭을 체크하고 저장한 뒤 달리세요. 아래는 실제 연결 상태입니다.</p></div><DialogTrigger asChild><Button variant="outline" size="sm"><CircleHelp data-icon="inline-start"/>내용 표시 방법</Button></DialogTrigger></div>
    <dl className="readiness-grid" aria-label="실제 자료 준비 상태">{rows.map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>
  </AlertDescription></Alert>;
}

export default function OverlayStudio({ apiBase = API_BASE, fetcher = fetch }) {
  const { selectedGame: game } = useGame();
  const [view, setView] = useState(null), [configs, setConfigs] = useState({});
  const [scope, setScope] = useState('game'), [session, setSession] = useState('practice');
  const [layout, setLayout] = useState('studio'), [commonOpen, setCommonOpen] = useState(false);
  const commonId = useId();
  const [scene, setScene] = useState('start'), [availability, setAvailability] = useState('personal');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [expanded, setExpanded] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const dirty = useRef(false), generation = useRef(0), saving = useRef(false), currentTarget = useRef(null);
  const [previewing, setPreviewing] = useState(true);
  const target = `${game}:${scope}`;
  const config = useMemo(() => ({ ...initialConfig(), ...(configs[target] || {}), source: game === 'lmu' ? 'personal' : (configs[target]?.source || 'auto') }), [configs, target, game]);
  const appearance = useMemo(() => ({width:view?.settings?.width,height:view?.settings?.height,
    content_opacity:view?.settings?.content_opacity,background_opacity:view?.settings?.background_opacity}),
  [view?.settings?.width,view?.settings?.height,view?.settings?.content_opacity,view?.settings?.background_opacity]);
  const selected = selectedModules(config, session), info = sessions[session];
  const display = selectModules(config, session, scene, availability);
  const ref = referenceFor(game, config, availability), Icon = sessionIcons[session], inputs = config.modes[session] === 'inputs';
  const request = useCallback(async (path = '', method = 'GET', body, signal) => {
    const response = await fetcher(`${apiBase}/api/overlay/studio${path}?game=${game}`, { method, signal,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
    const data = await response.json();
    if (!response.ok) throw new Error(response.status === 404 ? '새 오버레이 화면을 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.' : typeof data.detail === 'string' ? data.detail : '오버레이 구성을 저장하지 못했습니다.');
    if (!data.studio?.selected) throw new Error('오버레이 복수 선택을 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.');
    if (data.diagnostics_version !== 1) throw new Error('새 코너 진단을 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.');
    if (data.automatic_version !== 1) throw new Error('과제 없는 자동 주행 코칭을 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.');
    if (!Array.isArray(data.studio.formation_items)) throw new Error('포메이션 텍스트를 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.');
    return data;
  }, [apiBase, fetcher, game]);
  useEffect(() => {
    let cancelled = false, inFlight = false, controller;
    dirty.current = false; currentTarget.current = null; generation.current += 1;
    setView(null); setError(''); setConfigs({}); setNotice('');
    const poll = async () => {
      if (saving.current || inFlight || document.visibilityState === 'hidden') return;
      inFlight = true;
      controller = new AbortController();
      const ticket = generation.current;
      try {
        const data = await request('', 'GET', undefined, controller.signal);
        if (cancelled || saving.current || ticket !== generation.current) return;
        const nextTarget = JSON.stringify(data.settings_target);
        if (currentTarget.current !== nextTarget) {
          dirty.current = false; currentTarget.current = nextTarget;
          setScope(data.settings_target?.car_key ? 'car' : 'game');
          setSession(data.studio?.manual_session || 'practice');
        }
        setView(data);
        if (!dirty.current) setConfigs(current => {
          const next = {[`${game}:car`]: data.studio, [`${game}:game`]: data.game_default};
          return JSON.stringify(current) === JSON.stringify(next) ? current : next;
        });
        setError('');
      } catch (failure) { if (!cancelled && !controller.signal.aborted && ticket === generation.current) setError(failure.message); }
      finally { inFlight = false; }
    };
    poll(); const timer = setInterval(poll, 1500);
    document.addEventListener('visibilitychange', poll);
    return () => { cancelled = true; clearInterval(timer); controller?.abort(); document.removeEventListener('visibilitychange', poll); };
  }, [game, request]);
  const chooseScene = next => setScene(next);
  const previewProps = {game,session,config,scene,availability,previewing,onPreview:setPreviewing,apiBase,fetcher,appearance};
  const patch = values => { dirty.current = true; generation.current += 1; setConfigs(current => ({...current, [target]: {...config,...values}})); setNotice(''); };
  const changeSession = next => { setSession(next); chooseScene(next === 'formation' ? 'before' : 'start'); setNotice(''); };
  const toggleModule = id => {
    const next = selected.includes(id) ? selected.filter(key => key !== id) : [...selected, id];
    patch({selected: {...config.selected, [session]: next}});
  };
  const save = async () => {
    if (!view || saving.current) return;
    const epoch = generation.current, savedGame = game;
    const content = {...Object.fromEntries(['formation_items','slots','selected','modes','graph','source','auto','quiet','size','detail','timing'].map(key => [key, config[key]])), manual_session: session};
    saving.current = true; setBusy(true); setNotice('');
    try {
      const data = await request('', 'PUT', {config: content, scope, target: {game, car_key: view.settings_target?.car_key || null}});
      if (epoch !== generation.current || savedGame !== game) return;
      setView(data); dirty.current = false;
      setConfigs({[`${game}:car`]: data.studio, [`${game}:game`]: data.game_default});
      setError(''); setNotice(scope === 'game' && data.settings_target?.car_key ? '게임 기본값을 저장했습니다. 현재 차량에 별도 구성이 있으면 차량 구성이 우선합니다.' : '구성을 저장했습니다. 주행 오버레이와 창 미리보기에 적용됩니다.');
      await request('/session', 'PUT', {session});
    } catch (failure) { if (epoch === generation.current) setError(failure.message); }
    finally { saving.current = false; setBusy(false); }
  };
  const preset = () => { patch(session === 'formation' ? {formation_items: [...formationDefaults]} : { slots: { ...config.slots, [session]: [...info.slots] }, selected: {...config.selected, [session]: info.slots.filter(Boolean)} }); setScene(session === 'formation' ? 'before' : 'start'); };
  const green = () => { changeSession('race'); setNotice('그린 전환 예시: 포메이션 텍스트를 종료하고 레이스 구성으로 바뀌었습니다.'); };
  return <Dialog open={helpOpen} onOpenChange={setHelpOpen}><div className="overlay-studio">
    <main className="studio-main">
      <div className="page-heading"><div><p className="eyebrow">DRIVING COACH / OVERLAY</p><h1>오버레이</h1><p>세션별로 필요한 코칭을 고르고, 주행 화면에서 확인하세요.</p></div>
        <ToggleGroup type="single" value={layout} onValueChange={value => value && setLayout(value)} aria-label="화면 보기" variant="outline"><ToggleGroupItem value="studio"><Settings2 aria-hidden="true"/>설정 화면</ToggleGroupItem><ToggleGroupItem value="compare"><Layers aria-hidden="true"/>네 세션 비교</ToggleGroupItem></ToggleGroup></div>
      <div className="context-bar"><div className="game-picker"><Badge variant="outline">{game === 'lmu' ? 'LMU' : 'iRacing'}</Badge><span className="context-car">{view?.settings_target?.car_name || '차량 연결 대기 · 게임 기본값 편집'}</span></div>
        <Field orientation="horizontal" className="scope-field"><FieldLabel htmlFor="save-scope">저장 대상</FieldLabel><NativeSelect id="save-scope" value={scope} disabled={busy} onChange={e => {setScope(e.target.value); setNotice('');}}><NativeSelectOption value="car" disabled={!view?.settings_target?.car_key}>현재 차량</NativeSelectOption><NativeSelectOption value="game">게임 기본값</NativeSelectOption></NativeSelect></Field>
        <div className="context-actions"><Button variant="outline" size="sm" disabled={busy || !view} onClick={preset}><RotateCcw data-icon="inline-start"/>현재 세션 추천 구성</Button><Button size="sm" disabled={busy || !view} onClick={save}><Save data-icon="inline-start"/>{busy ? '저장 중…' : '저장·적용'}</Button></div></div>
      <div className="common-settings" data-open={commonOpen}><Button variant="ghost" className="common-toolbar" aria-expanded={commonOpen} aria-controls={commonId} onClick={() => setCommonOpen(open => !open)}><Settings2 data-icon="inline-start"/><span className="common-heading"><b>창·표시 설정</b><small>자동 표시 · 위치 · 크기 · 불투명도 · 고정</small></span><span className="common-disclosure">{commonOpen ? '설정 접기' : '설정 열기'}<ChevronDown aria-hidden="true"/></span></Button><div id={commonId} className="common-body" hidden={!commonOpen}><OverlaySettings apiBase={apiBase} fetcher={fetcher} visible={commonOpen} compact /></div></div>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {notice && <p role="status" className="save-notice"><Check aria-hidden="true"/>{notice}</p>}
      <ContentReadiness view={error ? null : view}/>
      {layout === 'studio' ? <>
        <div className="session-bar"><Tabs value={session} onValueChange={changeSession}><TabsList aria-label="세션별 구성">{SESSION_IDS.map(id => { const SIcon = sessionIcons[id]; return <TabsTrigger key={id} value={id} disabled={busy || !view}><SIcon aria-hidden="true"/>{sessions[id].name}</TabsTrigger>; })}</TabsList></Tabs><Field orientation="horizontal" className="auto-field"><FieldLabel htmlFor="auto-session">세션 자동 전환</FieldLabel><Switch disabled={busy || !view} id="auto-session" checked={config.auto} onCheckedChange={value => patch({ auto: value })}/></Field></div>
        <div className="session-intro"><Icon aria-hidden="true"/><div><h2>{info.title}</h2><p>{info.description}</p></div><Badge variant="secondary">추천 · {info.preset}</Badge></div>
        <div className="mode-picker"><div><h3>{info.name}에 표시할 화면</h3><p>세션마다 선택하고, 두 방식의 세부 설정을 그대로 유지하세요.</p></div><ToggleGroup disabled={busy || !view} type="single" value={config.modes[session]} onValueChange={value=>value&&patch({modes:{...config.modes,[session]:value}})} aria-label="오버레이 표시 형태"><ToggleGroupItem value="coach"><Layers aria-hidden="true"/><span><b>코칭 카드</b><small>목표·통과 결과·반복 패턴</small></span>{!inputs&&<Check className="mode-check" aria-hidden="true"/>}</ToggleGroupItem><ToggleGroupItem value="inputs"><Activity aria-hidden="true"/><span><b>입력값 그래프</b><small>스로틀·브레이크·조향 비교</small></span>{inputs&&<Check className="mode-check" aria-hidden="true"/>}</ToggleGroupItem></ToggleGroup></div>
        <fieldset className="studio-grid" disabled={busy || !view}>
          {session === 'formation' && !inputs ? <FormationChoices config={config} game={game} onChange={patch}/> : inputs ? <Panel title="입력값 선택" description="보고 싶은 입력만 켜두세요." className="library-panel graph-controls-panel" label="입력값 그래프 설정" footer={<span>코칭 카드 구성은 그대로 보관됩니다.</span>}><GraphControls config={config} onChange={patch}/></Panel> : <CoachingChoices selected={selected} session={session} onToggle={toggleModule}/>}
          <section className="center-column" aria-label="오버레이 미리보기">
            <div className="preview-heading"><span><i/>오버레이 미리보기 <small>샘플 장면</small></span><Button variant="ghost" size="sm" onClick={() => setExpanded(true)}><Expand data-icon="inline-start"/>크게 보기</Button></div>
            <PreviewStage {...previewProps} />
            <SceneControls {...{ session, scene, onScene: chooseScene, onGreen: green }}/>
            <div className="stage-explanation"><ArrowUpRight aria-hidden="true"/><p>{scene === 'traffic' && config.quiet ? '근접 상황에서는 상세 코칭을 접는 예시입니다. 오른쪽에서 설정을 바꿔 비교하세요.' : scene === 'after' && config.timing === 'lap' ? '결과 표시를 ‘랩 종료’로 설정했습니다. 랩 종료 장면에서 결과를 확인하세요.' : '실제 오버레이의 배치·글꼴·비율입니다. 창·표시 설정의 크기와 불투명도도 반영하며, 좁은 공간에서는 같은 비율로 축소합니다.'}</p></div>
            {session !== 'formation' && <Panel title={<span className="reference-title"><ShieldCheck aria-hidden="true"/>비교 기준</span>} className="reference-panel" label="비교 기준 설정">
              <FieldGroup className="reference-fields"><Field><FieldLabel htmlFor="reference-source">비교할 기록</FieldLabel><NativeSelect id="reference-source" value={config.source} onChange={e => patch({ source: e.target.value })}>{game === 'iracing' && <NativeSelectOption value="auto">자동 · Garage61 없으면 내 기록</NativeSelectOption>}<NativeSelectOption value="personal">비슷한 조건의 내 빠른 기록</NativeSelectOption>{game === 'iracing' && <NativeSelectOption value="garage61">Garage61 기준</NativeSelectOption>}</NativeSelect></Field>
                <Field><FieldLabel htmlFor="reference-fixture">미리보기 샘플 상황</FieldLabel><NativeSelect id="reference-fixture" value={availability} onChange={e => setAvailability(e.target.value)}><NativeSelectOption value="personal">개인 기록 있음 · 샘플</NativeSelectOption>{game === 'iracing' && <NativeSelectOption value="g61">Garage61·개인 기록 있음 · 샘플</NativeSelectOption>}<NativeSelectOption value="none">조건에 맞는 비교 기록 없음</NativeSelectOption></NativeSelect></Field></FieldGroup>
              <p className="text-sm text-muted-foreground">실제 연결: {view?.reference?.message || '주행 대기'}{view?.reference?.condition_note ? ` · ${view.reference.condition_note}` : ''}</p><div className="reference-status" data-ready={ref.ready}><CircleDot aria-hidden="true"/><div><b>{ref.label}{ref.ready && ` · ${ref.lap}`}</b><p>{ref.message}</p>{ref.ready && <small>{game === 'lmu' ? 'LMU 개인 기록 예시 · 공식 랩 유효성 미확인' : ref.fallback ? 'Garage61 기준이 없어 개인 기록으로 연결한 예시입니다.' : '선택한 출처의 기준을 사용하는 예시입니다.'}</small>}</div><Badge variant="outline">{ref.ready ? '샘플 연결' : '비교 대기'}</Badge></div>
            </Panel>}
          </section>
          <div className="settings-column">
            {session === 'formation' && !inputs ? <FormationSummary config={config} game={game}/> : inputs ? <Panel title="그래프 보조 정보" description="그래프에 함께 표시할 항목입니다." className="slots-panel" label="그래프 보조 정보 설정"><GraphExtras config={config} onChange={patch}/></Panel> : <Panel title="상황별 자동 표시" description={`${selected.length}개 선택 · 측정된 정보 중 최대 3개를 표시합니다.`} className="slots-panel" label="상황별 표시 안내">
              <div className="phase-plan">{Object.entries(phaseOrder).filter(([phase]) => session === 'formation' ? phase === 'formation' : phase !== 'formation').map(([phase, ids]) => <div key={phase} data-active={display.phase === phase}><b>{phaseNames[phase]}{display.phase === phase && <Badge variant="secondary">현재 장면</Badge>}</b><p>{ids.filter(id => selected.includes(id)).map(id => byId[id].name).join(' · ') || '선택한 항목 없음'}</p></div>)}</div>
              <p className="selection-hint">항목을 여러 개 선택해도 화면을 채우지 않습니다. 상황과 측정 자료에 맞춰 큰 정보 1개와 보조 최대 2개로 바뀝니다.</p>
            </Panel>}
            <Panel title="표시 방식" className="appearance-panel" label="표시 방식 설정">
              <FieldGroup>
                {!inputs && <><Field><FieldLabel htmlFor="detail-level">정보 상세도</FieldLabel><NativeSelect id="detail-level" value={config.detail} onChange={e => patch({ detail: e.target.value })}><NativeSelectOption value="simple">간단히 · 제목과 숫자</NativeSelectOption><NativeSelectOption value="standard">기본 · 설명과 측정값</NativeSelectOption><NativeSelectOption value="detailed">자세히 · 관측 범위 포함</NativeSelectOption></NativeSelect></Field>
                {session !== 'formation' && <Field><FieldLabel htmlFor="result-timing">결과로 자동 전환</FieldLabel><NativeSelect id="result-timing" value={config.timing} onChange={e => patch({ timing: e.target.value })}><NativeSelectOption value="pass">코너 묶음 후 · 직선에서 표시</NativeSelectOption><NativeSelectOption value="lap">랩 종료 후 · 직선에서 표시</NativeSelectOption></NativeSelect><p className="coaching-schedule">연속 코너의 결과를 모아 다음 직선에서 핵심 내용을 6초간 고정합니다. 새 내용은 최소 8초 간격으로 표시합니다. 다음 제동이 가까우면 기다리고, 제동·회전·근접 상황에서는 상세 내용을 접습니다. 예측 자료가 없는 첫 주행은 실제 입력이 2초간 안정된 뒤 표시합니다.</p></Field>}</>}
                <Field><FieldLabel htmlFor="font-size">오버레이 글자 크기</FieldLabel><NativeSelect id="font-size" value={config.size} onChange={e => patch({ size: e.target.value })}><NativeSelectOption value="small">작게</NativeSelectOption><NativeSelectOption value="normal">기본</NativeSelectOption><NativeSelectOption value="large">크게</NativeSelectOption></NativeSelect></Field>
                {session !== 'formation' && <Field orientation="horizontal"><FieldLabel htmlFor="quiet-traffic">레이스 근접 시 간단히</FieldLabel><Switch id="quiet-traffic" checked={config.quiet} onCheckedChange={value => patch({ quiet: value })}/></Field>}
              </FieldGroup>
            </Panel>
            <div className="module-footnote"><b>{inputs ? '실선은 내 입력 · 옅은 면은 기준' : '기본은 과제 없이 자동 관측'}</b><p>{inputs ? '맞는 비교 기록이 없으면 내 입력만 표시합니다. 크기·위치·불투명도는 위의 창·표시 설정에서 조절하세요.' : '자동 주행 코칭은 과제·비교 기록 없이 표시됩니다. 코너 비교는 파일 분석으로 구간을 준비하고, 연습 목표·성공률은 원하는 경우에만 과제를 연결하세요.'}</p></div>
          </div>
        </fieldset>
      </> : <section className="compare-view" aria-label="네 세션 비교">
        <div className="compare-intro"><div><h2>한 가지 도구, 네 가지 목적.</h2><p>현재 선택한 게임·저장 대상의 구성을 한눈에 비교하세요.</p></div><Badge variant="outline">세션을 누르면 편집 화면으로 이동</Badge></div>
        <div className="compare-grid">{SESSION_IDS.map(id => { const SIcon = sessionIcons[id]; return <Card key={id} className="compare-card"><CardHeader><CardTitle asChild><h3><span><SIcon aria-hidden="true"/>{sessions[id].name}<small>{sessions[id].en}</small></span><Button size="sm" variant="ghost" onClick={() => { changeSession(id); setLayout('studio'); }}>{sessions[id].name} 편집<ArrowUpRight data-icon="inline-end"/></Button></h3></CardTitle><CardDescription><p>{sessions[id].description}</p></CardDescription></CardHeader><CardContent><PreviewStage game={game} session={id} config={config} appearance={appearance} apiBase={apiBase} fetcher={fetcher} availability={availability} gallery /></CardContent><CardFooter><GalleryFooter config={config} session={id} game={game}/></CardFooter></Card>; })}</div>
      </section>}
      <footer className="studio-footer"><span><CircleDot aria-hidden="true"/>미리보기는 실제 오버레이와 같은 코드로 그립니다. 수치는 샘플입니다.</span><span>저장·적용하면 실제 주행 오버레이 구성이 바뀝니다.</span></footer>
    </main>
    <Dialog open={expanded} onOpenChange={setExpanded}><DialogContent className="overlay-studio expanded-dialog"><DialogHeader><DialogTitle>{info.name} 오버레이 · 크게 보기</DialogTitle><DialogDescription>선택한 구성으로 장면을 바꿔보세요. 배경과 수치는 가상 예시입니다.</DialogDescription></DialogHeader><PreviewStage {...previewProps}/><SceneControls {...{ session, scene, onScene: chooseScene, onGreen: green }}/></DialogContent></Dialog>
    <DialogContent className="overlay-studio guidance-dialog"><DialogHeader><DialogTitle>오버레이 내용 표시 방법</DialogTitle><DialogDescription>자동 관측으로 바로 시작하고, 필요한 항목만 추가로 준비하세요.</DialogDescription></DialogHeader>
      <ol className="guidance-steps"><li>표시 형태에서 코칭 카드 → 자동 주행 코칭을 체크하고 저장·적용하세요. 현재 세션 추천 구성으로도 시작할 수 있습니다.</li><li>트랙에서 정상 주행하면 약 1초 뒤 무입력·동시 입력 관측이 나옵니다. 제동 해제 후 가속을 연결하면 직전 연결 시간도 표시합니다.</li><li>코너별 비교를 더 보고 싶으면 같은 차량·트랙의 주행 파일을 한 번 분석하세요. 조건에 맞는 기록이 준비되면 비교 결과도 표시합니다.</li><li>연습 목표·성공률은 선택 기능입니다. 주행 요약 → 다음 주행에서 할 일 → 이 과제로 연습 시작에서 연결하세요.</li></ol>
      <p>피트·리플레이·저속·옐로·근접 구간은 자동 관측에서 제외합니다. 코너 반복 기록은 이번 주행에서 쌓이며 새 주행·자료 끊김·비교 기준 변경 때 다시 관측합니다. 한 번에 선택 항목 중 최대 3개를 상황에 맞춰 표시합니다.</p>
      <Table><TableHeader><TableRow><TableHead>표시 항목</TableHead><TableHead>필요한 자료와 표시 조건</TableHead></TableRow></TableHeader><TableBody>{modules.map(item => <TableRow key={item.id}><TableCell><b>{item.name}</b><p>{moduleTiming(item.id, session)}</p></TableCell><TableCell>{moduleRequirements[item.id]}</TableCell></TableRow>)}</TableBody></Table>
    </DialogContent>
  </div></Dialog>;
}
