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
export const DRIVER_REPORT_TIMEOUT_MS = 15000;
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const text = value => typeof value === 'string' ? value : '';
const count = value => Number.isFinite(value) && value >= 0 ? value : null;
const records = value => Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)) : [];
const comparablePace = pace => pace?.status === 'OBSERVED' && (!pace.replay_status || pace.replay_status === 'CONFIRMED_NON_REPLAY');

function normalizeReport(payload) {
  const coverage = object(payload.coverage), pace = object(payload.pace);
  const prose = value => Object.fromEntries(['title', 'body', 'scope', 'reason'].map(key => [key, text(object(value)[key])]));
  return { ...payload, state: text(payload.state),
    coverage: { ...coverage, ...Object.fromEntries(['source_count', 'native_session_count', 'source_only_count', 'undated_count',
      'period_session_count', 'observation_count', 'source_observation_count', 'measured_observation_count', 'measured_lap_count',
      'replay_unknown_observation_count', 'driving_unavailable_observation_count', 'outdated_source_count'].map(key => [key, count(coverage[key])])),
      reason: text(coverage.reason), blocking_reason_counts: records(coverage.blocking_reason_counts).map(row => ({ code: text(row.code), count: count(row.count), reason: text(row.reason) })).filter(row => row.reason) },
    career: Object.fromEntries(CAREER_METRICS.map(({ key }) => { const metric = object(object(payload.career)[key]);
      return [key, { value: count(metric.value), status: text(metric.status), reason: text(metric.reason) }]; })),
    overview: prose(payload.overview), challenge: prose(payload.challenge), style: prose(payload.style),
    pace: { ...pace, scope: text(pace.scope), reason: text(pace.reason), status: text(pace.status), trend: text(pace.trend),
      replay_status: text(pace.replay_status), points: records(pace.points).map(point => ({ ...point, date: text(point.date) })) },
    discoveries: records(payload.discoveries).map(fact => ({ id: text(fact.id), date: text(fact.date), title: text(fact.title), body: text(fact.body) })).filter(fact => fact.title || fact.body),
    recent_drives: records(payload.recent_drives).map(drive => ({ drive_id: text(drive.drive_id), recorded_at: text(drive.recorded_at),
      car: text(drive.car), track: text(drive.track), session_type: text(drive.session_type), measured_laps: count(drive.measured_laps),
      identity_status: text(drive.identity_status), identity_reason: text(drive.identity_reason), replay_status: text(drive.replay_status),
      in_selected_period: typeof drive.in_selected_period === 'boolean' ? drive.in_selected_period : null })),
    limitations: Array.isArray(payload.limitations) ? payload.limitations.filter(value => typeof value === 'string') : [],
  };
}

async function readJson(url, controller, method) {
  let timer, abort;
  const deadline = new Promise((resolve, reject) => {
    abort = () => reject(new DOMException('Read cancelled', 'AbortError'));
    controller.signal.addEventListener('abort', abort, { once: true });
    timer = setTimeout(() => {
      const error = new Error('응답이 늦어 조회를 중단했습니다. 다시 시도해 주세요.');
      error.name = 'TimeoutError'; reject(error); controller.abort();
    }, DRIVER_REPORT_TIMEOUT_MS);
  });
  try {
    return await Promise.race([deadline, (async () => {
      const response = await fetch(url, { signal: controller.signal, ...(method ? { method } : {}) });
      let payload;
      try { payload = await response.json(); }
      catch { throw new Error('리포트 응답을 읽지 못했습니다. 다시 조회해 주세요.'); }
      if (!response.ok) throw new Error(typeof payload?.detail === 'string' ? payload.detail : `리포트를 가져오지 못했습니다. (HTTP ${response.status})`);
      return payload;
    })()]);
  } finally { clearTimeout(timer); controller.signal.removeEventListener('abort', abort); }
}

function useHistoryRefresh(base, game, enabled, onComplete) {
  const scope = `${base}|${game}`;
  const [state, setState] = useState({ key: scope, job: null, busy: false, error: '' });
  const current = useRef(null), completion = useRef(onComplete);
  completion.current = onComplete;
  useEffect(() => {
    const run = { active: true, busy: false, watching: false, controller: null, timer: null, job: null };
    current.current = run;
    setState({ key: scope, job: null, busy: false, error: '' });
    run.execute = async method => {
      if (!run.active || run.busy) return;
      run.busy = true; clearTimeout(run.timer);
      run.controller = new AbortController();
      if (method === 'POST') run.watching = true;
      setState(previous => ({ ...previous, key: scope, busy: true, error: '' }));
      try {
        const payload = await readJson(`${base}/api/driver-history/refresh?game=${game}`, run.controller, method);
        if (!run.active || run.controller.signal.aborted) return;
        if (payload?.game !== game || !['IDLE', 'RUNNING', 'COMPLETE', 'FAILED'].includes(payload?.status)) throw new Error('기존 기록의 확인 상태를 읽지 못했습니다. 다시 확인해 주세요.');
        const job = { status: payload.status, message: text(payload.message),
          ...Object.fromEntries(['total', 'processed', 'updated', 'skipped', 'failed'].map(key => [key, count(payload[key])])) };
        run.job = job;
        setState({ key: scope, job, busy: false, error: '' });
        if (job.status === 'RUNNING') {
          run.watching = true; run.timer = setTimeout(() => run.execute(), 1500);
        } else if (run.watching && ['COMPLETE', 'FAILED'].includes(job.status)) {
          run.watching = false; completion.current?.();
        }
      } catch (error) {
        if (run.active && (error?.name !== 'AbortError')) setState(previous => ({ ...previous, key: scope, busy: false,
          error: error?.name === 'TypeError' ? '기록 확인 상태를 가져오지 못했습니다. 연결 상태를 확인해 주세요.' : error?.message || '기록 확인 상태를 가져오지 못했습니다.' }));
      } finally { run.busy = false; }
    };
    // Reading an existing job never starts a new source scan.
    if (enabled) void run.execute();
    return () => { run.active = false; clearTimeout(run.timer); run.controller?.abort(); };
  }, [base, game, scope, enabled]);
  const visible = state.key === scope ? state : { job: null, busy: false, error: '' };
  return { ...visible, start: () => current.current?.execute(current.current?.job?.status === 'RUNNING' ? undefined : 'POST') };
}

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
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value) || !Number.isFinite(calendarDay(value.slice(0, 10)))) return '날짜 미확인';
  return full ? value.slice(0, 10).replaceAll('-', '.') : value.slice(5, 10).replace('-', '.');
}

function calendarDay(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value ? timestamp : NaN;
}

function ReportEmpty({ title, children }) {
  return <Empty className="driver-report-empty"><EmptyHeader><EmptyTitle>{title}</EmptyTitle><EmptyDescription>{children}</EmptyDescription></EmptyHeader></Empty>;
}

function GrowthSummary({ pace, game }) {
  const measured = OBSERVED.has(pace?.status) && Number.isFinite(pace?.median_lap_s) && pace.median_lap_s > 0;
  const compared = measured && comparablePace(pace) && Number.isFinite(pace.pace_change_pct);
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
  const measured = points.map(point => ({ ...point, day: calendarDay(point.date) }))
    .filter(point => Number.isFinite(point.day) && Number.isFinite(point.median_lap_s) && point.median_lap_s > 0).sort((a, b) => a.day - b.day);
  if (measured.length < 2 || measured[0].day === measured[measured.length - 1].day) return null;
  const values = measured.map(point => point.median_lap_s);
  const low = Math.min(...values), high = Math.max(...values);
  const range = high - low;
  const firstDay = measured[0].day, elapsedDays = measured[measured.length - 1].day - firstDay;
  const coordinates = measured.map(point => ({
    ...point,
    x: 12 + (point.day - firstDay) * 456 / elapsedDays,
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

function DriveRow({ drive, detailed = false }) {
  const details = [drive.identity_status === 'SOURCE_ONLY' ? '독립 세션 미확인 · 누적 통계와 성장 비교에서 제외' : '',
    ['UNKNOWN', 'MIXED'].includes(drive.replay_status) ? '리플레이 여부 미확인 · 관측한 측정값만 표시' : '',
    drive.replay_status === 'CONFIRMED_REPLAY' ? '리플레이 관측 · 주행 누적에서 제외' : '',
    drive.in_selected_period === false ? '선택 기간 밖 또는 날짜 미확인' : ''].filter(Boolean);
  return <div className="driver-report-drive"><time>{dateLabel(drive.recorded_at)}</time><div><strong>{drive.car || '차량 미확인'}</strong><span>{drive.track || '코스 미확인'} · {typeof SESSION_LABELS[drive.session_type] === 'string' ? SESSION_LABELS[drive.session_type] : SESSION_LABELS.UNKNOWN}</span>{detailed && details.length ? <span className="driver-report-drive-details">{details.join(' · ')}</span> : null}{detailed && drive.identity_reason ? <span className="driver-report-drive-details">{drive.identity_reason}</span> : null}</div><span title="관측에서 측정 가능한 랩 수입니다. 공식 유효 판정과 다를 수 있습니다.">측정 {number(drive.measured_laps)}랩</span></div>;
}

function Evidence({ report, gameName, period, triggerRef, open, onOpenChange }) {
  const coverage = report?.coverage;
  const drives = Array.isArray(report?.recent_drives) ? report.recent_drives : [];
  const discoveries = Array.isArray(report?.discoveries) ? report.discoveries : [];
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="driver-report-scene driver-report-evidence" onCloseAutoFocus={event => { event.preventDefault(); triggerRef.current?.focus(); }}>
    <DialogHeader><DialogTitle>관측 근거와 기록</DialogTitle><DialogDescription>{gameName} · 기간 비교는 최근 {period}일 · 누적과 연결 기록은 전체 범위</DialogDescription></DialogHeader>
    <div className="driver-report-evidence-body">
      <section aria-label="관측 범위"><h3>어디까지 확인했나요?</h3><dl className="driver-report-evidence-counts"><div><dt>연결한 원본</dt><dd>{number(coverage?.source_count)}개</dd></div><div><dt>확인된 세션</dt><dd>{number(coverage?.native_session_count)}개</dd></div><div><dt>출처별 관측</dt><dd>{number(coverage?.source_only_count)}개</dd></div><div><dt>날짜 미확인</dt><dd>{number(coverage?.undated_count)}개</dd></div><div><dt>선택 기간 세션</dt><dd>{number(coverage?.period_session_count)}개</dd></div></dl><p>날짜를 확인하지 못한 관측은 최근 {period}일 비교에 넣지 않습니다. 세션을 확인하지 못한 출처는 별도 관측으로 보관하고, 독립된 주행 누적이나 성장 비교에 넣지 않습니다.</p>{coverage?.reason ? <p>{coverage.reason}</p> : null}</section>
      {Number.isFinite(coverage?.measured_lap_count) ? <><Separator /><section aria-label="측정과 누적 범위"><h3>측정한 기록과 누적의 차이</h3><dl className="driver-report-evidence-counts driver-report-measurement-counts"><div><dt>연결한 관측</dt><dd>{number(coverage.observation_count)}개</dd></div><div><dt>측정 가능 관측</dt><dd>{number(coverage.measured_observation_count)}개</dd></div><div><dt>측정 가능한 랩</dt><dd>{number(coverage.measured_lap_count)}랩</dd></div><div><dt>리플레이 미확인</dt><dd>{number(coverage.replay_unknown_observation_count)}개</dd></div><div><dt>운전량 미확인</dt><dd>{number(coverage.driving_unavailable_observation_count)}개</dd></div></dl><p>측정 가능한 랩은 공식 유효 랩 수가 아닙니다. 리플레이 여부가 미확인인 관측은 확인된 구간의 측정값으로만 읽으며, 확정된 실제 주행이나 성장으로 해석하지 않습니다.</p></section></> : null}
      {coverage?.blocking_reason_counts?.length ? <><Separator /><section aria-label="미포함 이유"><h3>누적과 비교에 넣지 못한 이유</h3><ul>{coverage.blocking_reason_counts.map((item, index) => <li key={item.code || index}>{item.reason}{Number.isFinite(item.count) ? ` · ${number(item.count)}개 관측` : ''}</li>)}</ul><p>한 관측에 여러 이유가 있을 수 있어 이유별 개수는 더하지 않습니다.</p></section></> : null}
      <Separator />
      <section aria-label="누적 통계 근거"><h3>전체 누적 통계</h3><p>선택한 게임에서 분석한 기록 전체를 집계합니다. 30일·90일 필터나 현재 선택한 비교 랩에 따라 누적 범위가 바뀌지 않습니다.</p><ul className="driver-report-metric-reasons">{CAREER_METRICS.map(({ key, label }) => <li key={key}><strong>{label} · {metricValue(key, report?.career?.[key])}</strong><span>{report?.career?.[key]?.status === 'PARTIAL' ? '확인된 일부 · ' : ''}{report?.career?.[key]?.reason || '아직 확인된 집계 근거가 없습니다.'}</span></li>)}</ul></section>
      <Separator />
      <section><h3>페이스와 성향을 읽는 기준</h3><p>{report?.pace?.scope || '같은 게임·차량·코스에서 비교할 조건을 확인합니다.'}</p><p>{report?.pace?.reason || '측정 가능한 랩의 중앙값과 편차를 관측합니다.'}</p><p>{report?.style?.reason || '운전 성향을 설명할 근거를 기다립니다.'}</p><p>랩 중앙값과 편차만으로 성장이나 공식 유효 여부를 판정하지 않습니다. 그래프는 날짜를 확인한 실제 관측만 연결합니다.</p></section>
      {discoveries.length ? <><Separator /><section><h3>발견한 관측</h3><ul className="driver-report-evidence-list">{discoveries.map((fact, index) => <li key={fact.id || index}><strong>{fact.title}</strong><time>{dateLabel(fact.date, true)}</time><p>{fact.body}</p></li>)}</ul></section></> : null}
      {drives.length ? <><Separator /><section><h3>전체 연결 기록 중 최근 항목</h3><p>연결한 전체 기록에서 최근 항목을 보여줍니다. 선택한 {period}일 밖의 기록도 포함될 수 있습니다.</p><div className="driver-report-drive-list">{drives.map((drive, index) => <DriveRow key={drive.drive_id || index} drive={drive} detailed />)}</div></section></> : null}
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
        const payload = await readJson(`${base}/api/driver-report?game=${selectedGame}&period_days=${period}`, controller);
        if (payload?.schema_version !== 1 || payload?.game !== selectedGame || payload?.period_days !== Number(period)) throw new Error('요청한 게임과 기간의 리포트를 확인하지 못했습니다. 다시 조회해 주세요.');
        if (active && !controller.signal.aborted) setQuery({ key: queryKey, data: normalizeReport(payload), error: '', loading: false });
      } catch (error) {
        if (!active || (controller.signal.aborted && error?.name !== 'TimeoutError')) return;
        setQuery(previous => ({ key: queryKey, data: previous.key === queryKey ? previous.data : null,
          error: error?.name === 'TypeError' ? '분석 서버와 연결하지 못했습니다. 연결 상태를 확인해 주세요.' : error?.message || '리포트를 가져오지 못했습니다. 연결 상태를 확인해 주세요.', loading: false }));
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
  const historyRefresh = useHistoryRefresh(base, selectedGame, coverage?.outdated_source_count > 0, refresh);
  const historyJob = historyRefresh.job;
  const historyRunning = historyJob?.status === 'RUNNING';
  const showHistoryRefresh = coverage?.outdated_source_count > 0 || (historyJob && historyJob.status !== 'IDLE') || historyRefresh.error;

  return <section className="driver-report-scene" aria-label={`${gameName} 드라이버 리포트`} data-state={loading ? 'LOADING' : error ? 'ERROR' : report?.state || 'UNAVAILABLE'} aria-busy={loading}>
    <header className="driver-report-heading">
      <div className="driver-report-title"><span>{gameName} · 나의 관측 기록</span><h1>드라이버 리포트</h1></div>
      <section className="driver-report-career" aria-label="앱에 기록된 전체 누적 통계"><Button variant="ghost" size="sm" className="driver-report-career-scope" aria-label="누적 통계 집계 기준" onClick={openEvidence}><span>앱 기록<br />전체 누적</span><CircleHelp aria-hidden="true" /></Button><dl>{CAREER_METRICS.map(({ key, label, Icon }) => <div key={key}><dt><Icon aria-hidden="true" />{label}</dt><dd title={report?.career?.[key]?.reason} aria-label={`${label} ${metricValue(key, report?.career?.[key])}${report?.career?.[key]?.status === 'PARTIAL' ? ' · 확인된 일부' : ''}`}>{metricValue(key, report?.career?.[key])}</dd></div>)}</dl></section>
      <div className="driver-report-period"><span>관측 기간</span><ToggleGroup type="single" variant="outline" size="sm" aria-label="리포트 기간" value={period} onValueChange={value => { if (value === '30' || value === '90') setPeriod(value); }}><ToggleGroupItem value="30">30일</ToggleGroupItem><ToggleGroupItem value="90">90일</ToggleGroupItem></ToggleGroup></div>
    </header>
    <div className="driver-report-query-area"><div className="driver-report-query-line">
      <p role="status" aria-live="polite">{loading ? report ? '기록을 다시 확인하고 있어요.' : `${gameName} 기록을 확인하고 있어요.` : error ? report ? '최근 조회에 실패했어요. 이전에 확인한 기록을 표시합니다.' : '기록을 가져오지 못했어요. 다시 시도해 주세요.' : report ? `최근 ${period}일 · 확인된 ${number(coverage?.period_session_count)}세션${coverage?.undated_count > 0 ? ` · 날짜 미확인 ${number(coverage.undated_count)}개는 기간 비교에서 제외` : ''}` : '확인된 기록이 없습니다.'}</p>
      <Button variant="ghost" size="sm" onClick={refresh} disabled={loading}><RefreshCw data-icon="inline-start" />{error ? '다시 시도' : '새로고침'}</Button>
    </div>{showHistoryRefresh ? <Alert className="driver-report-history-refresh" role={historyRefresh.error || historyJob?.status === 'FAILED' ? 'alert' : 'status'} variant={historyRefresh.error || historyJob?.status === 'FAILED' ? 'destructive' : 'default'}>
      <div><AlertTitle>{historyRunning ? '기존 기록을 다시 확인하고 있어요' : '기존 기록 확인'}</AlertTitle><AlertDescription>
        <p>{historyRefresh.error || historyJob?.message || `${number(coverage?.outdated_source_count)}개 원본의 측정 범위를 다시 확인할 수 있어요. 저장된 관측을 유지하며 확인합니다.`}</p>
        {historyJob && historyJob.status !== 'IDLE' ? <p>처리 {number(historyJob.processed)}/{number(historyJob.total)}개 · 갱신 {number(historyJob.updated)}개 · 건너뜀 {number(historyJob.skipped)}개 · 실패 {number(historyJob.failed)}개</p> : null}
        {historyJob?.failed > 0 || historyJob?.skipped > 0 ? <p>확인하지 못한 원본은 기존 관측을 유지합니다. 해당 주행을 다시 선택해 분석하면 확인할 수 있습니다.</p> : null}
        {historyRunning && historyRefresh.error ? <p>화면 조회가 중단되어도 시작한 기록 확인은 계속될 수 있습니다.</p> : null}
      </AlertDescription></div>
      <Button variant="outline" size="sm" onClick={historyRefresh.start} disabled={historyRefresh.busy || (historyRunning && !historyRefresh.error)}>{historyRefresh.busy ? '상태 확인 중…' : historyRunning ? '진행 상태 다시 확인' : '기존 기록 다시 확인'}</Button>
    </Alert> : null}</div>
    {error ? <Alert variant="destructive" className="driver-report-error"><AlertTitle>리포트 조회 실패</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
    <GrowthSummary pace={pace} game={selectedGame} />
    <div className="driver-report-grid">
      <Card className="driver-report-card driver-report-overview"><CardHeader><div className="driver-report-eyebrow"><Activity aria-hidden="true" /><span>이번 리포트</span><Button variant="ghost" size="sm" onClick={openEvidence}>관측 근거<ArrowRight data-icon="inline-end" /></Button></div></CardHeader><CardContent>{loading && !report ? <div className="driver-report-loading"><Skeleton className="h-8 w-3/4" /><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-2/3" /></div> : <><CardTitle asChild><h2>{report?.overview?.title || (error ? '기록을 다시 불러와 주세요' : '나의 주행 이야기를 시작해요')}</h2></CardTitle><CardDescription>{report?.overview?.body || '주행 파일을 분석하면 확인된 기록부터 연결합니다.'}</CardDescription>{report?.state === 'EMPTY' ? <div className="driver-report-start-actions"><Button size="sm" onClick={onChooseSource} disabled={!onChooseSource}>주행 선택</Button><Button variant="outline" size="sm" onClick={openAnalysis} disabled={!onNavigate}>주행 분석 열기</Button></div> : null}</>}</CardContent><CardFooter><p>{report?.overview?.scope || '분석한 원본에서 확인된 내용을 기다립니다.'}</p></CardFooter></Card>
      <Card className="driver-report-card driver-report-challenge"><CardHeader><div className="driver-report-eyebrow"><Target aria-hidden="true" />다음 목표</div><CardTitle asChild><h2>{report?.challenge?.title || '기록 한 번 살펴보기'}</h2></CardTitle></CardHeader><CardContent><CardDescription>{report?.challenge?.body || '주행 파일을 분석하고 이번에 확인한 내용을 살펴보세요.'}</CardDescription></CardContent><CardFooter><Button onClick={openAnalysis} disabled={!onNavigate}>주행 분석에서 확인<ArrowRight data-icon="inline-end" /></Button></CardFooter></Card>
      <Card className="driver-report-card driver-report-style"><CardHeader><div className="driver-report-eyebrow"><Route aria-hidden="true" />반복되는 조작</div><CardTitle asChild><h2>나의 운전 성향</h2></CardTitle></CardHeader><CardContent><ReportEmpty title="아직은 관찰 중이에요">{report?.style?.reason || '운전 성향을 설명할 근거가 아직 충분하지 않습니다.'}</ReportEmpty></CardContent><CardFooter><p>한 번의 주행으로 성향을 정하지 않아요.</p></CardFooter></Card>
      <Card className="driver-report-card driver-report-pace"><CardHeader><div className="driver-report-eyebrow"><Gauge aria-hidden="true" />기록에서 확인한 수치</div><CardTitle asChild><h2>최근 관측</h2></CardTitle></CardHeader><CardContent>{measuredPace ? <><p className="driver-report-pace-scope" title={pace.scope}>{pace.scope || '조건을 확인한 랩'}</p><dl className="driver-report-pace-values"><div><dt>랩 중앙값</dt><dd>{lapTime(pace.median_lap_s)}</dd></div><div><dt>랩 편차 비율</dt><dd>{Number.isFinite(pace.mad_pct) && pace.mad_pct >= 0 ? `${number(pace.mad_pct, 2)}%` : DASH}</dd></div><div><dt>측정 랩</dt><dd>{number(pace.lap_count)}<small>랩</small></dd></div></dl><PaceChart points={Array.isArray(pace.points) ? pace.points : []} /></> : <ReportEmpty title="비교할 기록을 기다려요">{pace?.reason || '측정 가능한 랩과 비교 조건을 먼저 확인합니다.'}</ReportEmpty>}</CardContent><CardFooter>{measuredPace && comparablePace(pace) && typeof TREND_LABELS[pace.trend] === 'string' ? <Badge variant="secondary">{TREND_LABELS[pace.trend]}</Badge> : <p>{measuredPace ? '성장 방향은 비교 근거가 쌓인 뒤 살펴봐요.' : '확인되지 않은 변화는 채우지 않습니다.'}</p>}</CardFooter></Card>
      <Card className="driver-report-card driver-report-records"><CardHeader><div className="driver-report-eyebrow"><Award aria-hidden="true" />주행의 발자취</div><CardTitle asChild><h2>발견과 기록</h2></CardTitle></CardHeader><CardContent>{discoveries.length || drives.length ? <div className="driver-report-record-summary">{discoveries.length ? <div className="driver-report-discovery"><time>{dateLabel(discoveries[0].date)}</time><strong>{discoveries[0].title}</strong><p>{discoveries[0].body}</p></div> : null}{drives.slice(0, discoveries.length ? 1 : 2).map((drive, index) => <DriveRow key={drive.drive_id || index} drive={drive} />)}</div> : <ReportEmpty title="여기서부터 시작이에요">확인된 관측과 연결한 주행을 이곳에 남깁니다.</ReportEmpty>}</CardContent><CardFooter><Button variant="ghost" size="sm" onClick={openEvidence}>연결된 기록 보기<ArrowRight data-icon="inline-end" /></Button></CardFooter></Card>
    </div>
    <footer className="driver-report-footnote"><span>분석한 기록의 관측 · 게임의 평생 기록과 다를 수 있어요.</span><span>미확인 수치는 {DASH}로 표시합니다.</span></footer>
    <Evidence report={report} gameName={gameName} period={period} triggerRef={evidenceTrigger} open={evidenceOpen} onOpenChange={setEvidenceOpen} />
  </section>;
}
