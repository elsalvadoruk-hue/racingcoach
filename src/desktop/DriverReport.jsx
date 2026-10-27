import React, { useEffect, useRef, useState } from 'react';
import { Activity, ArrowRight, Award, CircleHelp, Clock3, Flag, Gauge, Medal, RefreshCw, Route, ShieldCheck, Target, Trophy, Waves } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogClose } from '../components/ui/dialog';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '../components/ui/empty';
import { Separator } from '../components/ui/separator';
import { Skeleton } from '../components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import './DriverReport.css';

const DASH = '—';
const OBSERVED = new Set(['OBSERVED', 'PARTIAL']);
const CAREER_METRICS = [
  { key: 'distance_m', label: '주행 거리', Icon: Route },
  { key: 'driving_time_s', label: '주행 시간', Icon: Clock3 },
  { key: 'wins', label: '우승', Icon: Trophy },
  { key: 'podiums', label: '포디엄', Icon: Medal },
  { key: 'average_position', label: '평균 순위', Icon: Flag },
];
const SESSION_LABELS = { PRACTICE: '연습', QUALIFY: '예선', RACE: '레이스', UNKNOWN: '종류 미확인' };
const TREND_LABELS = { IMPROVING: '비교 범위에서 페이스 개선', STEADY: '비교 범위에서 비슷한 페이스', DECLINING: '비교 범위에서 페이스 저하' };

function number(value, digits = 0) {
  return Number.isFinite(value) ? value.toLocaleString('ko-KR', { maximumFractionDigits: digits }) : DASH;
}

function duration(value) {
  if (!Number.isFinite(value) || value < 0) return DASH;
  if (value < 60) return `${number(value, 1)}초`;
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return hours ? `${number(hours)}시간 ${minutes}분` : `${minutes}분`;
}

function lapTime(value) {
  if (!Number.isFinite(value) || value <= 0) return DASH;
  const milliseconds = Math.round(value * 1000);
  const minutes = Math.floor(milliseconds / 60000);
  return `${minutes}:${((milliseconds % 60000) / 1000).toFixed(3).padStart(6, '0')}`;
}

function metricValue(key, metric) {
  if (!OBSERVED.has(metric?.status) || !Number.isFinite(metric?.value) || metric.value < 0) return DASH;
  if (key === 'distance_m') return `${number(metric.value / 1000, 1)} km`;
  if (key === 'driving_time_s') return duration(metric.value);
  if (key === 'average_position') return `${number(metric.value, 1)}위`;
  return `${number(metric.value)}회`;
}

function dateLabel(value, full = false) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)) return '날짜 미확인';
  return full ? value.slice(0, 10).replaceAll('-', '.') : value.slice(5, 10).replace('-', '.');
}

function ReportEmpty({ title, children }) {
  return <Empty className="driver-report-empty"><EmptyHeader><EmptyTitle>{title}</EmptyTitle><EmptyDescription>{children}</EmptyDescription></EmptyHeader></Empty>;
}

function GrowthSummary({ pace, game }) {
  const measured = OBSERVED.has(pace?.status) && Number.isFinite(pace?.median_lap_s) && pace.median_lap_s > 0;
  const compared = measured && pace.status === 'OBSERVED' && Number.isFinite(pace.pace_change_pct);
  const change = compared ? pace.pace_change_pct : null;
  const spread = measured && Number.isFinite(pace.mad_pct) && pace.mad_pct >= 0 ? pace.mad_pct : null;
  const metrics = [
    { label: '개인 페이스', Icon: Gauge, value: compared ? `${change < 0 ? '−' : change > 0 ? '+' : ''}${number(Math.abs(change), 2)}%` : DASH,
      note: compared ? '동일 조건의 랩타임 변화' : '비교 조건과 기록 확인 필요',
      reason: compared ? `음수는 랩타임 감소 · ${pace.scope || '조건을 확인한 조합'} · ${pace.reason || ''}` : pace?.reason || '비교 가능한 관측을 기다립니다.' },
    { label: '랩 간 편차 비율', Icon: Waves, value: spread === null ? DASH : `${number(spread, 2)}%`,
      note: spread === null ? '측정 가능한 랩 확인 필요' : pace.status === 'PARTIAL' ? '최근 한 기록의 측정 랩' : '동일 조건의 측정 랩',
      reason: `중앙절대편차 / 랩 중앙값 × 100${pace?.scope ? ` · ${pace.scope}` : ''}` },
    { label: game === 'lmu' ? '측정 가능한 랩 비율' : '유효 랩 비율', Icon: ShieldCheck, value: DASH,
      note: game === 'lmu' ? '전체 랩의 측정 범위 미확인' : '공식 유효 판정 미확인',
      reason: game === 'lmu' ? '전체 랩 수와 측정 가능한 랩 수의 동일 범위를 확인해야 비율을 계산합니다.' : '측정 가능한 랩을 공식 유효 랩으로 간주하지 않습니다.' },
    { label: '시간당 인시던트 점수', Icon: Flag, value: DASH, note: '공식 점수·관측 시간 미확인',
      reason: '인시던트 점수와 완전 관측한 경기 시간을 확인할 수 없습니다.' },
  ];
  return <section className="driver-report-summary" aria-label="최근 성장 요약">{metrics.map(({ label, Icon, value, note, reason }) =>
    <Card key={label} className="driver-report-summary-card" title={reason}>
      <CardHeader><CardTitle asChild><h2><Icon aria-hidden="true" />{label}</h2></CardTitle></CardHeader>
      <CardContent><strong aria-label={`${label} ${value}`}>{value}</strong></CardContent>
      <CardFooter><p>{note}</p></CardFooter>
    </Card>
  )}</section>;
}

function PaceChart({ points }) {
  const measured = points.filter(point => typeof point?.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(point.date) && Number.isFinite(point.median_lap_s) && point.median_lap_s > 0);
  if (measured.length < 2) return null;
  const values = measured.map(point => point.median_lap_s);
  const low = Math.min(...values), high = Math.max(...values);
  const range = high - low;
  const coordinates = measured.map((point, index) => ({
    ...point,
    x: 12 + index * 456 / (measured.length - 1),
    y: range ? 14 + (high - point.median_lap_s) * 46 / range : 37,
  }));
  return <div className="driver-report-chart">
    <svg viewBox="0 0 480 74" role="img" aria-label={`날짜가 확인된 ${measured.length}개 관측의 랩 중앙값`}>
      <line x1="12" y1="64" x2="468" y2="64" className="driver-report-chart-axis" />
      <polyline points={coordinates.map(point => `${point.x},${point.y}`).join(' ')} className="driver-report-chart-line" />
      {coordinates.map((point, index) => <circle key={`${point.date}-${index}`} cx={point.x} cy={point.y} r="3.5" className="driver-report-chart-dot"><title>{dateLabel(point.date, true)} · {lapTime(point.median_lap_s)} · 측정 {number(point.lap_count)}랩</title></circle>)}
    </svg>
    <div className="driver-report-chart-dates"><span>{dateLabel(measured[0].date)}</span><span>낮을수록 짧은 랩타임</span><span>{dateLabel(measured[measured.length - 1].date)}</span></div>
  </div>;
}

function DriveRow({ drive }) {
  return <div className="driver-report-drive"><time>{dateLabel(drive.recorded_at)}</time><div><strong>{drive.car || '차량 미확인'}</strong><span>{drive.track || '코스 미확인'} · {SESSION_LABELS[drive.session_type] || SESSION_LABELS.UNKNOWN}</span></div><span>{number(drive.measured_laps)}랩</span></div>;
}

function Evidence({ report, gameName, period, triggerRef, open, onOpenChange }) {
  const coverage = report?.coverage;
  const drives = Array.isArray(report?.recent_drives) ? report.recent_drives : [];
  const discoveries = Array.isArray(report?.discoveries) ? report.discoveries : [];
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="driver-report-scene driver-report-evidence" onCloseAutoFocus={event => { event.preventDefault(); triggerRef.current?.focus(); }}>
    <DialogHeader><DialogTitle>관측 근거와 기록</DialogTitle><DialogDescription>{gameName} · 최근 {period}일 · 앱에 연결된 원장 기준</DialogDescription></DialogHeader>
    <div className="driver-report-evidence-body">
      <section aria-label="관측 범위"><h3>어디까지 확인했나요?</h3><dl className="driver-report-evidence-counts"><div><dt>연결한 원본</dt><dd>{number(coverage?.source_count)}개</dd></div><div><dt>확인된 세션</dt><dd>{number(coverage?.native_session_count)}개</dd></div><div><dt>출처별 관측</dt><dd>{number(coverage?.source_only_count)}개</dd></div><div><dt>날짜 미확인</dt><dd>{number(coverage?.undated_count)}개</dd></div><div><dt>선택 기간 세션</dt><dd>{number(coverage?.period_session_count)}개</dd></div></dl><p>날짜를 확인하지 못한 관측은 최근 {period}일 비교에 넣지 않습니다. 세션을 확인하지 못한 출처를 같은 주행이라고 합치지 않습니다.</p></section>
      <Separator />
      <section aria-label="누적 통계 근거"><h3>전체 누적 통계</h3><p>선택한 게임에서 분석한 기록 전체를 집계합니다. 30일·90일 필터나 현재 선택한 비교 랩에 따라 누적 범위가 바뀌지 않습니다.</p><ul className="driver-report-metric-reasons">{CAREER_METRICS.map(({ key, label }) => <li key={key}><strong>{label} · {metricValue(key, report?.career?.[key])}</strong><span>{report?.career?.[key]?.status === 'PARTIAL' ? '확인된 일부 · ' : ''}{report?.career?.[key]?.reason || '아직 확인된 집계 근거가 없습니다.'}</span></li>)}</ul></section>
      <Separator />
      <section><h3>페이스와 성향을 읽는 기준</h3><p>{report?.pace?.scope || '같은 게임·차량·코스에서 비교할 조건을 확인합니다.'}</p><p>{report?.pace?.reason || '측정 가능한 랩의 중앙값과 편차를 관측합니다.'}</p><p>{report?.style?.reason || '운전 성향을 설명할 근거를 기다립니다.'}</p><p>랩 중앙값과 편차만으로 성장이나 공식 유효 여부를 판정하지 않습니다. 그래프는 날짜를 확인한 실제 관측만 연결합니다.</p></section>
      {discoveries.length ? <><Separator /><section><h3>발견한 관측</h3><ul className="driver-report-evidence-list">{discoveries.map((fact, index) => <li key={fact.id || index}><strong>{fact.title}</strong><time>{dateLabel(fact.date, true)}</time><p>{fact.body}</p></li>)}</ul></section></> : null}
      {drives.length ? <><Separator /><section><h3>최근 연결된 기록</h3><div className="driver-report-drive-list">{drives.map((drive, index) => <DriveRow key={drive.drive_id || index} drive={drive} />)}</div></section></> : null}
      {Array.isArray(report?.limitations) && report.limitations.length ? <><Separator /><section><h3>함께 알아둘 점</h3><ul>{report.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul></section></> : null}
    </div>
    <DialogFooter><DialogClose asChild><Button variant="outline">닫기</Button></DialogClose></DialogFooter>
  </DialogContent></Dialog>;
}

export default function DriverReport({ game = 'iracing', apiBase = '', onNavigate, onChooseSource }) {
  const selectedGame = game === 'lmu' ? 'lmu' : 'iracing';
  const gameName = selectedGame === 'lmu' ? 'LMU' : 'iRacing';
  const [period, setPeriod] = useState('30');
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState({ key: null, data: null, error: '', loading: true });
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceTrigger = useRef(null);
  const base = String(apiBase || '').replace(/\/$/, '');
  const queryKey = `${base}|${selectedGame}|${period}`;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setEvidenceOpen(false);
    setQuery(previous => ({ key: queryKey, data: previous.key === queryKey ? previous.data : null, error: '', loading: true }));
    async function load() {
      try {
        const response = await fetch(`${base}/api/driver-report?game=${selectedGame}&period_days=${period}`, { signal: controller.signal });
        const payload = await response.json();
        if (!response.ok) throw new Error(typeof payload?.detail === 'string' ? payload.detail : `리포트를 가져오지 못했습니다. (HTTP ${response.status})`);
        if (payload?.schema_version !== 1 || payload?.game !== selectedGame || payload?.period_days !== Number(period)) throw new Error('요청한 게임과 기간의 리포트를 확인하지 못했습니다. 다시 조회해 주세요.');
        if (active && !controller.signal.aborted) setQuery({ key: queryKey, data: payload, error: '', loading: false });
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        setQuery(previous => ({ key: queryKey, data: previous.key === queryKey ? previous.data : null, error: error?.message || '리포트를 가져오지 못했습니다. 연결 상태를 확인해 주세요.', loading: false }));
      }
    }
    load();
    return () => { active = false; controller.abort(); };
  }, [base, selectedGame, period, queryKey, reload]);

  const report = query.key === queryKey ? query.data : null;
  const error = query.key === queryKey ? query.error : '';
  const loading = query.key !== queryKey || query.loading;
  const coverage = report?.coverage;
  const pace = report?.pace;
  const measuredPace = OBSERVED.has(pace?.status) && Number.isFinite(pace?.median_lap_s) && pace.median_lap_s > 0;
  const discoveries = Array.isArray(report?.discoveries) ? report.discoveries : [];
  const drives = Array.isArray(report?.recent_drives) ? report.recent_drives : [];
  const openEvidence = event => { evidenceTrigger.current = event.currentTarget; setEvidenceOpen(true); };
  const openAnalysis = () => onNavigate?.('summary');
  const refresh = () => setReload(value => value + 1);

  return <section className="driver-report-scene" aria-label={`${gameName} 드라이버 리포트`} data-state={loading ? 'LOADING' : error ? 'ERROR' : report?.state || 'UNAVAILABLE'} aria-busy={loading}>
    <header className="driver-report-heading">
      <div className="driver-report-title"><span>{gameName} · 나의 관측 기록</span><h1>드라이버 리포트</h1></div>
      <section className="driver-report-career" aria-label="앱에 기록된 전체 누적 통계"><Button variant="ghost" size="sm" className="driver-report-career-scope" aria-label="누적 통계 집계 기준" onClick={openEvidence}><span>앱 기록<br />전체 누적</span><CircleHelp aria-hidden="true" /></Button><dl>{CAREER_METRICS.map(({ key, label, Icon }) => <div key={key}><dt><Icon aria-hidden="true" />{label}</dt><dd title={report?.career?.[key]?.reason} aria-label={`${label} ${metricValue(key, report?.career?.[key])}${report?.career?.[key]?.status === 'PARTIAL' ? ' · 확인된 일부' : ''}`}>{metricValue(key, report?.career?.[key])}</dd></div>)}</dl></section>
      <div className="driver-report-period"><span>관측 기간</span><ToggleGroup type="single" variant="outline" size="sm" aria-label="리포트 기간" value={period} onValueChange={value => { if (value === '30' || value === '90') setPeriod(value); }}><ToggleGroupItem value="30">30일</ToggleGroupItem><ToggleGroupItem value="90">90일</ToggleGroupItem></ToggleGroup></div>
    </header>
    <div className="driver-report-query-line">
      <p role="status" aria-live="polite">{loading ? report ? '기록을 다시 확인하고 있어요.' : `${gameName} 기록을 확인하고 있어요.` : error ? report ? '최근 조회에 실패했어요. 이전에 확인한 기록을 표시합니다.' : '기록을 가져오지 못했어요. 다시 시도해 주세요.' : report ? `최근 ${period}일 · 확인된 ${number(coverage?.period_session_count)}세션${coverage?.undated_count > 0 ? ` · 날짜 미확인 ${number(coverage.undated_count)}개는 기간 비교에서 제외` : ''}` : '확인된 기록이 없습니다.'}</p>
      <Button variant="ghost" size="sm" onClick={refresh} disabled={loading}><RefreshCw data-icon="inline-start" />{error ? '다시 시도' : '새로고침'}</Button>
    </div>
    {error ? <Alert variant="destructive" className="driver-report-error"><AlertTitle>리포트 조회 실패</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    <GrowthSummary pace={pace} game={selectedGame} />
    <div className="driver-report-grid">
      <Card className="driver-report-card driver-report-overview"><CardHeader><div className="driver-report-eyebrow"><Activity aria-hidden="true" /><span>이번 리포트</span><Button variant="ghost" size="sm" onClick={openEvidence}>관측 근거<ArrowRight data-icon="inline-end" /></Button></div></CardHeader><CardContent>{loading && !report ? <div className="driver-report-loading"><Skeleton className="h-8 w-3/4" /><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-2/3" /></div> : <><CardTitle asChild><h2>{report?.overview?.title || (error ? '기록을 다시 불러와 주세요' : '나의 주행 이야기를 시작해요')}</h2></CardTitle><CardDescription>{report?.overview?.body || '주행 파일을 분석하면 확인된 기록부터 연결합니다.'}</CardDescription>{report?.state === 'EMPTY' ? <div className="driver-report-start-actions"><Button size="sm" onClick={onChooseSource} disabled={!onChooseSource}>주행 선택</Button><Button variant="outline" size="sm" onClick={openAnalysis} disabled={!onNavigate}>주행 분석 열기</Button></div> : null}</>}</CardContent><CardFooter><p>{report?.overview?.scope || '분석한 원본에서 확인된 내용을 기다립니다.'}</p></CardFooter></Card>
      <Card className="driver-report-card driver-report-challenge"><CardHeader><div className="driver-report-eyebrow"><Target aria-hidden="true" />다음 목표</div><CardTitle asChild><h2>{report?.challenge?.title || '기록 한 번 살펴보기'}</h2></CardTitle></CardHeader><CardContent><CardDescription>{report?.challenge?.body || '주행 파일을 분석하고 이번에 확인한 내용을 살펴보세요.'}</CardDescription></CardContent><CardFooter><Button onClick={openAnalysis} disabled={!onNavigate}>주행 분석에서 확인<ArrowRight data-icon="inline-end" /></Button></CardFooter></Card>
      <Card className="driver-report-card driver-report-style"><CardHeader><div className="driver-report-eyebrow"><Route aria-hidden="true" />반복되는 조작</div><CardTitle asChild><h2>나의 운전 성향</h2></CardTitle></CardHeader><CardContent><ReportEmpty title="아직은 관찰 중이에요">{report?.style?.reason || '운전 성향을 설명할 근거가 아직 충분하지 않습니다.'}</ReportEmpty></CardContent><CardFooter><p>한 번의 주행으로 성향을 정하지 않아요.</p></CardFooter></Card>
      <Card className="driver-report-card driver-report-pace"><CardHeader><div className="driver-report-eyebrow"><Gauge aria-hidden="true" />기록에서 확인한 수치</div><CardTitle asChild><h2>최근 관측</h2></CardTitle></CardHeader><CardContent>{measuredPace ? <><p className="driver-report-pace-scope" title={pace.scope}>{pace.scope || '조건을 확인한 랩'}</p><dl className="driver-report-pace-values"><div><dt>랩 중앙값</dt><dd>{lapTime(pace.median_lap_s)}</dd></div><div><dt>랩 편차 비율</dt><dd>{Number.isFinite(pace.mad_pct) && pace.mad_pct >= 0 ? `${number(pace.mad_pct, 2)}%` : DASH}</dd></div><div><dt>측정 랩</dt><dd>{number(pace.lap_count)}<small>랩</small></dd></div></dl><PaceChart points={Array.isArray(pace.points) ? pace.points : []} /></> : <ReportEmpty title="비교할 기록을 기다려요">{pace?.reason || '측정 가능한 랩과 비교 조건을 먼저 확인합니다.'}</ReportEmpty>}</CardContent><CardFooter>{measuredPace && TREND_LABELS[pace.trend] ? <Badge variant="secondary">{TREND_LABELS[pace.trend]}</Badge> : <p>{measuredPace ? '성장 방향은 비교 근거가 쌓인 뒤 살펴봐요.' : '확인되지 않은 변화는 채우지 않습니다.'}</p>}</CardFooter></Card>
      <Card className="driver-report-card driver-report-records"><CardHeader><div className="driver-report-eyebrow"><Award aria-hidden="true" />주행의 발자취</div><CardTitle asChild><h2>발견과 기록</h2></CardTitle></CardHeader><CardContent>{discoveries.length || drives.length ? <div className="driver-report-record-summary">{discoveries.length ? <div className="driver-report-discovery"><time>{dateLabel(discoveries[0].date)}</time><strong>{discoveries[0].title}</strong><p>{discoveries[0].body}</p></div> : null}{drives.slice(0, discoveries.length ? 1 : 2).map((drive, index) => <DriveRow key={drive.drive_id || index} drive={drive} />)}</div> : <ReportEmpty title="여기서부터 시작이에요">확인된 관측과 연결한 주행을 이곳에 남깁니다.</ReportEmpty>}</CardContent><CardFooter><Button variant="ghost" size="sm" onClick={openEvidence}>연결된 기록 보기<ArrowRight data-icon="inline-end" /></Button></CardFooter></Card>
    </div>
    <footer className="driver-report-footnote"><span>분석한 기록의 관측 · 게임의 평생 기록과 다를 수 있어요.</span><span>미확인 수치는 {DASH}로 표시합니다.</span></footer>
    <Evidence report={report} gameName={gameName} period={period} triggerRef={evidenceTrigger} open={evidenceOpen} onOpenChange={setEvidenceOpen} />
  </section>;
}
