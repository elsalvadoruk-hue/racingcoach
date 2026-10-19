import { Button } from './components/ui/button';
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from './components/ui/table';
import { Input } from './components/ui/input';
import { Textarea } from './components/ui/textarea';
import { NativeSelect } from './components/ui/native-select';
import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from './components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from './components/ui/dialog';
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from './components/ui/empty';
import { ToggleGroup, ToggleGroupItem } from './components/ui/toggle-group';
import { FieldSet, FieldLegend } from './components/ui/field';
import { Badge } from './components/ui/badge';
import { Skeleton } from './components/ui/skeleton';
import { Alert, AlertTitle, AlertDescription } from './components/ui/alert';
import {
  Activity,
  AlertTriangle,
  LineChart,
  CheckCircle2,
  ChevronDown,
  Database,
  FlaskConical,
  Gauge,
  Info,
  Layers3,
  Mic,
  RefreshCw,
  Route,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  Target,
  Thermometer,
  TimerReset,
  TrendingDown,
  TrendingUp,
  UploadCloud,
  XCircle,
  Zap,
} from 'lucide-react';
import CornerComparison from './components/CornerComparison';
import { GoalSelector, TrialPanel, TrialHistory, useTrialWorkflow } from './components/TrialWorkflow';
import { HelpHint, HelpLayer, QuickStart, UserManual, useHoverHelpSetting } from './components/HelpGuide';
import WorkspaceShell, { WorkflowSteps } from './components/WorkspaceShell';
import { COMPARISON_COLORS } from './chartTheme';
import AppSettings from './components/AppSettings';
import SessionLogExport from './components/SessionLogExport';
import LiveCoach, { VoiceReview } from './components/LiveCoach';
import CornerNames from './components/CornerNames';
import LapTimeUse from './components/LapTimeUse';
import DamperHistogram from './components/DamperHistogram';
import LapRangeFilter, { lapsInRange } from './components/LapRangeFilter';
import CornerConsistency from './components/CornerConsistency';
import GGDiagram from './components/GGDiagram';
import PracticeCoach from './components/PracticeCoach';
import CornerTrace from './components/CornerTrace';
import LapOverlay from './components/LapOverlay';
import LapDistribution from './components/LapDistribution';

const API_BASE =
  (typeof process !== 'undefined' && process.env?.REACT_APP_API_BASE_URL) ||
  'http://localhost:24158';

const APP_TABS = [
  { key: 'summary', label: '주행 요약', icon: Gauge },
  { key: 'compare', label: '코너 비교', icon: Route },
  { key: 'setup', label: '셋업 실험', icon: FlaskConical },
  { key: 'live', label: '음성 코치', icon: Mic },
  { key: 'history', label: '기록', icon: Database },
  { key: 'settings', label: '설정', icon: SlidersHorizontal },
  { key: 'guide', label: '사용 가이드', icon: Info },
];

const SESSION_MODES = [
  { value: 'QUALIFYING', label: '퀄리파잉 · 2랩', icon: Zap },
  { value: 'BASELINE_3LAP', label: '3-Lap 베이스', icon: RefreshCw },
  { value: 'ENDURANCE', label: '인듀런스', icon: Activity },
];

const SESSION_MODE_META = {
  QUALIFYING: {
    label: '퀄리파잉',
    badge: 'border-accent/35 bg-accent/10 text-foreground',
    badgeSelected: 'border-accent/60 bg-accent/25 text-foreground',
    button: 'coach-primary-action text-foreground shadow-lg shadow-violet-950/30',
  },
  BASELINE_3LAP: {
    label: '3-Lap 베이스',
    badge: 'border-primary/35 bg-primary/10 text-primary',
    badgeSelected: 'border-primary/60 bg-primary/25 text-foreground',
    button: 'coach-primary-action text-foreground shadow-lg shadow-cyan-950/30',
  },
  ENDURANCE: {
    label: '인듀런스',
    badge: 'border-warning/35 bg-warning/10 text-warning',
    badgeSelected: 'border-warning/60 bg-warning/25 text-foreground',
    button: 'bg-warning text-foreground shadow-lg shadow-amber-950/30',
  },
};

function storedModesForFile(file) {
  const modes = file?.memory?.session_modes;
  return Array.isArray(modes) ? modes.filter(Boolean) : [];
}

function assignedModeForFile(file) {
  return file?.memory?.assigned_mode || storedModesForFile(file)[0] || null;
}

// The mode a file opens in: what the driver stored for it, else what its session suggests
// (qualifying, a race of an hour or more as endurance, everything else as the 3-lap base).
function initialModeForFile(file, fallback = 'BASELINE_3LAP') {
  const suggested = SESSION_MODE_META[file?.suggested_session_mode] ? file.suggested_session_mode : null;
  return assignedModeForFile(file) || suggested || fallback;
}

// Says why this mode was chosen, or what the session would suggest when the stored choice differs.
function SessionModeHint({ file, stored, current }) {
  const suggested = SESSION_MODE_META[file?.suggested_session_mode] ? file.suggested_session_mode : null;
  if (!suggested || !file?.session_type) return null;
  const kind = /qual/i.test(file.session_type) ? '퀄리파잉' : /race/i.test(file.session_type) ? '레이스' : '연습';
  const minutes = kind === '레이스' && file.session_duration_s > 0 && file.session_duration_s < 86400 ? Math.round(file.session_duration_s / 60) : null;
  const length = minutes == null ? '' : minutes >= 60 ? ` ${Math.floor(minutes / 60)}시간${minutes % 60 ? ` ${minutes % 60}분` : ''}` : ` ${minutes}분`;
  const session = `${kind}${length}`;
  if (!stored && current === suggested) return <p role="status">자동 선택 · {session} 세션이라 {sessionModeLabel(suggested)}로 열었습니다.</p>;
  if (current !== suggested) return <p role="status">이 파일은 {session} 세션입니다. 세션 기준 추천은 {sessionModeLabel(suggested)}입니다.</p>;
  return null;
}

function sessionModeLabel(mode) {
  return SESSION_MODE_META[mode]?.label || mode || '미지정';
}

function lapBadgeClass(file, selected = false) {
  const modes = storedModesForFile(file);
  if (modes.length > 1 || file?.memory?.has_mode_conflict) {
    return selected
      ? 'border-destructive/60 bg-destructive/25 text-foreground'
      : 'border-destructive/35 bg-destructive/10 text-destructive';
  }
  const mode = assignedModeForFile(file);
  if (!mode) {
    return selected
      ? 'border-border/50 bg-accent/70 text-foreground'
      : 'border-border bg-accent text-foreground';
  }
  return selected
    ? SESSION_MODE_META[mode]?.badgeSelected
    : SESSION_MODE_META[mode]?.badge;
}

function normalizeContextValue(value) {
  return String(value || '').trim().toLocaleLowerCase();
}

function comparisonContext(file) {
  return {
    car: normalizeContextValue(file?.car_path || file?.car_name),
    track: normalizeContextValue(file?.track_path || file?.track_name),
    config: normalizeContextValue(file?.track_config),
  };
}

function sameVehicleAndTrack(left, right) {
  if (!left || !right) return false;
  const a = comparisonContext(left);
  const b = comparisonContext(right);
  return Boolean(a.car && a.track && a.car === b.car && a.track === b.track && a.config === b.config);
}

function analysisContextKey(context) {
  return JSON.stringify({ ...context, references: [...(context.references || [])].sort() });
}

function comparisonContextLabel(file) {
  if (!file) return '기준 파일을 선택하십시오.';
  const track = [file.track_name, file.track_config].filter(Boolean).join(' · ');
  return `${file.car_name || 'Unknown Car'} @ ${track || 'Unknown Track'}`;
}

const QUALITY_THEME = {
  VALID: {
    icon: CheckCircle2,
    border: 'border-success/40',
    background: 'bg-success/30',
    text: 'text-success',
    label: '판정 가능',
  },
  PARTIAL: {
    icon: AlertTriangle,
    border: 'border-warning/40',
    background: 'bg-warning/30',
    text: 'text-warning',
    label: '부분 데이터',
  },
  MISSING: {
    icon: XCircle,
    border: 'border-destructive/40',
    background: 'bg-destructive/30',
    text: 'text-destructive',
    label: '분석 제한',
  },
};

const CLASSIFICATION_LABELS = {
  INSUFFICIENT_DATA: '반복성 근거 부족',
  DRIVER_LIMITED: '드라이빙 영향 우세',
  SETUP_LIMITED: '셋업 영향 우세',
};

const ISSUE_LABELS = {
  EXIT: '탈출',
  MID_CORNER: '코너 중간',
  ENTRY: '진입',
  BRAKING: '제동',
  BALANCE_SUSPECT: '밸런스 의심',
  DRIVER_VARIANCE: '입력 편차',
  GAIN: '개선 구간',
  CONSISTENT: '일관됨',
  NO_REFERENCE: '기준 없음',
  NO_DELTA: '델타 없음',
};
const SPEED_LABELS = { LOW: '저속', MEDIUM: '중속', HIGH: '고속' };

function cx(...classes) {
  return classes.filter(Boolean).join(' ');
}

// Setup items in Korean; the iRacing garage keeps the English names, so those are shown small beside them.
const SETTING_NAMES = {
  rear_wing_angle_deg: '리어 윙 각도', rear_arb_blades: '리어 ARB 블레이드', front_arb_blades: '프런트 ARB 블레이드',
  front_ride_height: '앞 차고', rear_ride_height: '뒤 차고', diff_preload_nm: '디프 프리로드',
  brake_bias_pct: '브레이크 바이어스', front_toe_mm: '앞 토',
};
function settingName(item) {
  return SETTING_NAMES[item?.setting] || item?.label || item?.setting || '';
}

function formatNumber(value, digits = 1, suffix = '') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  return `${Number(value).toFixed(digits)}${suffix}`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const amount = bytes / (1024 ** index);
  return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatSigned(value, digits = 3, suffix = ' s') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  const numeric = Number(value);
  return `${numeric > 0 ? '+' : ''}${numeric.toFixed(digits)}${suffix}`;
}

function formatLapSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return '--';
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - minutes * 60;
  return `${minutes}:${remainder.toFixed(3).padStart(6, '0')}`;
}

function statusColor(status) {
  if (status === 'VALID') return 'text-success bg-success/10 border-success/30';
  if (status === 'INVALID') return 'text-destructive bg-destructive/10 border-destructive/30';
  if (status === 'OUT_LAP') return 'text-blue-300 bg-blue-500/10 border-blue-500/30';
  return 'text-warning bg-warning/10 border-warning/30';
}

async function requestJson(path, options = {}) {
  const controller = new AbortController();
  const { signal, timeoutMs = 180000, ...fetchOptions } = options;
  const cancel = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
  const response = await fetch(`${API_BASE}${path}`, { ...fetchOptions, signal: controller.signal });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.detail || `HTTP ${response.status}`);
  }
  return payload;
  } catch (error) {
    if (timedOut) throw new Error('응답 대기 시간이 초과되었습니다. 분석 서버 연결을 확인하고 다시 시도해 주세요.');
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', cancel);
  }
}

function useCanvasRenderer(draw, dependencies) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const container = canvas.parentElement;
    if (!container) return undefined;

    const render = () => {
      const rect = container.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(rect.width * ratio));
      canvas.height = Math.max(1, Math.round(rect.height * ratio));
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const context = canvas.getContext('2d');
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw(context, rect.width, rect.height, canvas);
    };

    render();
    const observer = new ResizeObserver(render);
    observer.observe(container);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  return canvasRef;
}

function TraceChart({ trace = [], targetLap, referenceLap }) {
  const [mode, setMode] = useState('speed');

  const draw = useCallback(
    (ctx, width, height) => {
      ctx.clearRect(0, 0, width, height);
      if (!trace.length) return;

      const padding = { left: 46, right: 18, top: 20, bottom: 28 };
      const plotWidth = width - padding.left - padding.right;
      const plotHeight = height - padding.top - padding.bottom;
      const x = (pct) => padding.left + pct * plotWidth;

      const values =
        mode === 'speed'
          ? trace.flatMap((point) =>
              [point.target_speed_kph, point.reference_speed_kph].filter((value) => value !== null)
            )
          : trace.map((point) => point.delta_s).filter((value) => value !== null);
      if (!values.length) return;
      let minValue = Math.min(...values);
      let maxValue = Math.max(...values);
      if (mode === 'speed') minValue = Math.max(0, minValue - 12);
      else {
        const span = Math.max(Math.abs(minValue), Math.abs(maxValue), 0.05);
        minValue = -span;
        maxValue = span;
      }
      const valueSpan = Math.max(maxValue - minValue, 1e-6);
      const y = (value) => padding.top + (1 - (value - minValue) / valueSpan) * plotHeight;

      ctx.strokeStyle = 'rgba(71, 85, 105, 0.55)';
      ctx.lineWidth = 1;
      ctx.font = '13px ui-monospace, SFMono-Regular, Menlo, monospace';
      ctx.fillStyle = COMPARISON_COLORS.muted;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (let row = 0; row <= 4; row += 1) {
        const value = minValue + ((4 - row) / 4) * valueSpan;
        const lineY = padding.top + (row / 4) * plotHeight;
        ctx.beginPath();
        ctx.moveTo(padding.left, lineY);
        ctx.lineTo(width - padding.right, lineY);
        ctx.stroke();
        ctx.fillText(mode === 'speed' ? value.toFixed(0) : value.toFixed(2), padding.left - 7, lineY);
      }

      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      [0, 0.25, 0.5, 0.75, 1].forEach((pct) => {
        const lineX = x(pct);
        ctx.beginPath();
        ctx.moveTo(lineX, padding.top);
        ctx.lineTo(lineX, height - padding.bottom);
        ctx.stroke();
        ctx.fillText(`${Math.round(pct * 100)}%`, lineX, height - padding.bottom + 7);
      });

      const drawLine = (key, strokeStyle, widthValue = 2.2, dashed = false) => {
        ctx.strokeStyle = strokeStyle;
        ctx.lineWidth = widthValue;
        ctx.setLineDash(dashed ? [5, 3] : []);
        ctx.beginPath();
        let started = false;
        trace.forEach((point) => {
          const value = point[key];
          if (value === null || value === undefined) return;
          if (!started) {
            ctx.moveTo(x(point.distance_pct), y(value));
            started = true;
          } else {
            ctx.lineTo(x(point.distance_pct), y(value));
          }
        });
        ctx.stroke();
        ctx.setLineDash([]);
      };

      if (mode === 'speed') {
        const hasReferenceBand = trace.some(
          (point) => point.reference_speed_p25_kph !== null && point.reference_speed_p25_kph !== undefined
            && point.reference_speed_p75_kph !== null && point.reference_speed_p75_kph !== undefined
        );
        if (hasReferenceBand) {
          ctx.fillStyle = `${COMPARISON_COLORS.reference}1f`;
          ctx.beginPath();
          let started = false;
          trace.forEach((point) => {
            if (point.reference_speed_p75_kph === null || point.reference_speed_p75_kph === undefined) return;
            const px = x(point.distance_pct);
            const py = y(point.reference_speed_p75_kph);
            if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
          });
          [...trace].reverse().forEach((point) => {
            if (point.reference_speed_p25_kph === null || point.reference_speed_p25_kph === undefined) return;
            ctx.lineTo(x(point.distance_pct), y(point.reference_speed_p25_kph));
          });
          ctx.closePath();
          ctx.fill();
        }
        drawLine('reference_speed_kph', COMPARISON_COLORS.reference, 2, true);
        drawLine('target_speed_kph', COMPARISON_COLORS.target, 2.4);

        ctx.fillStyle = 'rgba(248, 113, 113, 0.18)';
        ctx.beginPath();
        trace.forEach((point, index) => {
          const barHeight = Math.max(0, point.brake || 0) * plotHeight * 0.2;
          const lineX = x(point.distance_pct);
          if (index === 0) ctx.moveTo(lineX, height - padding.bottom);
          ctx.lineTo(lineX, height - padding.bottom - barHeight);
        });
        ctx.lineTo(width - padding.right, height - padding.bottom);
        ctx.closePath();
        ctx.fill();
      } else {
        if (minValue < 0 && maxValue > 0) {
          ctx.strokeStyle = 'rgba(226, 232, 240, 0.55)';
          ctx.setLineDash([5, 5]);
          ctx.beginPath();
          ctx.moveTo(padding.left, y(0));
          ctx.lineTo(width - padding.right, y(0));
          ctx.stroke();
          ctx.setLineDash([]);
        }
        trace.forEach((point, index) => {
          if (index === 0 || point.delta_s === null) return;
          const previous = trace[index - 1];
          if (previous.delta_s === null) return;
          ctx.strokeStyle = point.delta_s > previous.delta_s ? '#fb7185' : '#34d399';
          ctx.lineWidth = 2.4;
          ctx.beginPath();
          ctx.moveTo(x(previous.distance_pct), y(previous.delta_s));
          ctx.lineTo(x(point.distance_pct), y(point.delta_s));
          ctx.stroke();
        });
      }
    },
    [mode, trace]
  );

  const canvasRef = useCanvasRenderer(draw, [draw]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-sm">
          {mode === 'speed' ? (
            <>
              <span className="flex items-center gap-1.5" style={{ color: COMPARISON_COLORS.target }}>
                <span className="h-0.5 w-5" style={{ background: COMPARISON_COLORS.target }} /> 대상 랩 {targetLap ?? '--'}
              </span>
              <span className="flex items-center gap-1.5" style={{ color: COMPARISON_COLORS.reference }}>
                <span className="w-5 border-t-2 border-dashed" style={{ borderColor: COMPARISON_COLORS.reference }} /> 기준 랩 {referenceLap ?? '--'}
              </span>
            </>
          ) : (
            <span className="text-muted-foreground">상승=시간 손실, 하강=시간 이득</span>
          )}
        </div>
        <div className="flex rounded-xl border border-border bg-background p-1">
          <Button variant="default"
            type="button"
            onClick={() => setMode('speed')}
            className={cx(
              ' px-3 py-1.5 text-sm  transition',
              mode === 'speed' ? 'coach-primary-action ' : ' '
            )}
          >
            속도
          </Button>
          <Button variant="default"
            type="button"
            onClick={() => setMode('delta')}
            className={cx(
              ' px-3 py-1.5 text-sm  transition',
              mode === 'delta' ? 'coach-primary-action ' : ' '
            )}
          >
            누적 델타
          </Button>
        </div>
      </div>
      <div className="h-[270px] rounded-2xl border border-border bg-background/80 p-2">
        <canvas ref={canvasRef} className="h-full w-full" />
      </div>
    </div>
  );
}

// The "jump to" list under the sidebar for long screens: panel ids in screen order (see WorkspaceShell).
const SECTION_NAV = {
  summary: [
    { id: 'practice-coach', label: '오늘의 한 가지' },
    { id: 'run-summary', label: '이번 주행 요약' },
    { id: 'closed-loop-coach', label: '다음 주행에서 할 일' },
    { id: 'voice-review-panel', label: '대화로 남긴 코너' },
  ],
  compare: [
    { id: 'corner-inputs', label: '코너별 조작 비교' },
    { id: 'corner-names', label: '코너 번호 확인' },
    { id: 'comparison', label: '거리 기준 랩 비교' },
    { id: 'priorities', label: '랩타임 단축 우선순위' },
    { id: 'external-reference', label: 'Garage61 기준 주행' },
    { id: 'lap-time-use', label: '랩별 시간 쓰임새' },
    { id: 'lap-detail', label: '랩 선택 및 품질' },
    { id: 'corner-table', label: '코너 자동 분할 및 위상별 비교' },
    { id: 'corner-consistency', label: '코너별 일관성' },
    { id: 'lap-overlay', label: '전문가 보기 · 여러 랩 겹쳐 보기' },
  ],
  setup: [
    { id: 'briefing', label: '드라이버 피드백과 검증' },
    { id: 'engineer-briefing', label: '엔지니어 브리핑' },
    { id: 'setup-decision', label: '셋업 결정' },
    { id: 'ab-manager', label: '셋업 A/B 비교' },
    { id: 'decision', label: '원인 분류' },
    { id: 'ai-planner', label: 'AI 셋업 실험 계획' },
    { id: 'tire-snapshot', label: '타이어 종료 스냅샷' },
    { id: 'ride-aero', label: '차고·서스펜션·에어로' },
    { id: 'damper-velocity', label: '댐퍼 속도 분포' },
    { id: 'gg-diagram', label: 'G-G 그림 (그립 사용)' },
  ],
};

// What the stops of the open file (or earlier races of its series) showed about tyre changes (pit_service.py).
function pitServiceHint(service) {
  const name = (rule) => (rule === 'SEQUENTIAL' ? '순차 교체' : '동시 교체');
  if (service?.rule) return `이 파일의 피트 기록: ${name(service.rule)} (정차 ${service.stops?.length || 0}회)`;
  if (service?.remembered) return `이 시리즈의 지난 경기 기록: ${name(service.remembered)}`;
  return null;
}

function Panel({
  title,
  icon: Icon,
  children,
  className = '',
  action = null,
  collapsible = false,
  defaultOpen = true,
  id = undefined,
  lazy = false,
}) {
  const [open, setOpen] = useState(defaultOpen);
  // lazy: build the content the first time the panel opens (for content that fetches data)
  const [opened, setOpened] = useState(defaultOpen);
  useEffect(() => { if (open) setOpened(true); }, [open]);
  useEffect(() => {
    if (!id) return undefined;
    // A folded panel also opens for the section it wraps ("voice-review-panel" holds "voice-review").
    const reveal = (event) => { if (event.detail === id || `${event.detail}-panel` === id) setOpen(true); };
    window.addEventListener('coach-reveal-section', reveal);
    return () => window.removeEventListener('coach-reveal-section', reveal);
  }, [id]);

  return (
    <Card asChild><section
      id={id}
      className={cx(
        'coach-panel scroll-mt-24',
        className
      )}
    >
      <CardHeader className={cx('coach-panel-heading flex flex-row flex-wrap items-center justify-between gap-3', !open && 'mb-0')}>
        <CardTitle asChild><h2 data-help={title} className="flex items-center gap-2.5">
          {Icon && <Icon className="h-5 w-5 text-muted-foreground" />}
          {title}
        </h2></CardTitle>
        <div className="flex items-center gap-2">
          <HelpHint topic={title} />
          {action}
          {collapsible && (
            <Button variant="outline"
              type="button"
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
              className="flex min-h-[42px] items-center gap-2  border   px-3 py-2 text-sm   transition  "
            >
              {open ? '접기' : '펼치기'}
              <ChevronDown className={cx('h-5 w-5 transition-transform', open && 'rotate-180')} />
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="coach-panel-content" hidden={!open}>{!lazy || opened ? children : null}</CardContent>
    </section></Card>
  );
}

function MetricCard({ label, value, note, icon: Icon, tone = 'cyan' }) {
  const readableLabel = ({ 'Median delta': '중앙 랩타임 변화', 'Best delta': '최고 랩타임 변화', 'Stdev delta': '랩타임 흔들림 변화', 'Pace slope delta': '장기 페이스 변화', 'Steering work': '조향 수정량', Median: '중앙 랩타임', PACE: '랩 페이스', CONSISTENCY: '주행 일관성', DRIVEABILITY: '운전 편의', 'Front median': '앞 차고 중앙값', 'Rear median': '뒤 차고 중앙값', 'Rear wing': '리어 윙', 'Hot pressure slope': '온간 압력 추세', 'Surface temp slope': '표면 온도 추세' })[label] || label;
  return (
    <Card className="coach-stat" data-tone={tone} data-placeholder={value == null || ['--', '—', '비교 기준 없음', '측정 불가', '수집 중', '연결 필요', '설정 필요', '연결 확인 필요', '주행 파일 대기'].includes(value)}>
      <CardHeader className="coach-stat-label">
        <span data-help={label}>{readableLabel}<HelpHint topic={label} /></span>
        {Icon && <Icon className="h-4 w-4 opacity-70" />}
      </CardHeader>
      <CardContent className="p-0"><div className="coach-stat-value font-mono">{value ?? '--'}</div>
      {note && <CardDescription className="coach-stat-note">{note}</CardDescription>}</CardContent>
    </Card>
  );
}

function QualityBanner({ quality }) {
  if (!quality) return null;
  const theme = QUALITY_THEME[quality.state] || QUALITY_THEME.MISSING;
  const Icon = theme.icon;
  return (
    <div
      className={cx(
        'rounded-2xl border p-4 md:p-5',
        theme.border,
        theme.background
      )}
    >
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex items-start gap-3">
          <Icon className={cx('mt-0.5 h-5 w-5 shrink-0', theme.text)} />
          <div>
            <div className={cx('font-semibold', theme.text)}>
              데이터 품질: {theme.label} · {Math.round((quality.score || 0) * 100)}점
            </div>
            <div className="mt-2 flex flex-col gap-1 text-base leading-relaxed text-foreground">
              {(quality.messages || []).map((message, index) => (
                <p key={`${message}-${index}`}>{message}</p>
              ))}
            </div>
          </div>
        </div>
        <div className="grid shrink-0 grid-cols-3 gap-2 text-center text-sm">
          <div className="rounded-xl border border-border/70 bg-background/60 px-3 py-2">
            <div className="text-muted-foreground">유효 랩</div>
            <div className="mt-1 font-mono text-lg font-semibold text-foreground">{quality.valid_laps}</div>
          </div>
          <div className="rounded-xl border border-border/70 bg-background/60 px-3 py-2">
            <div className="text-muted-foreground">채널</div>
            <div className="mt-1 font-mono text-lg font-semibold text-foreground">{quality.channel_count}</div>
          </div>
          <div className="rounded-xl border border-border/70 bg-background/60 px-3 py-2">
            <div className="text-muted-foreground">확정 판정</div>
            <div
              className={cx(
                'mt-1 text-base font-semibold',
                quality.setup_decision_ready ? 'text-success' : 'text-warning'
              )}
            >
              {quality.setup_decision_ready ? 'READY' : 'HOLD'}
            </div>
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {Object.entries(quality.feature_support || {}).map(([feature, supported]) => (
          <span
            key={feature}
            className={cx(
              'rounded-lg border px-2 py-1 text-xs font-semibold uppercase tracking-wider',
              supported
                ? 'border-success/20 bg-success/5 text-success'
                : 'border-destructive/20 bg-destructive/5 text-destructive'
            )}
          >
            {feature}: {supported ? 'OK' : 'MISSING'}
          </span>
        ))}
      </div>
    </div>
  );
}

function lapReproducibilityLabel(status) {
  if (status === 'STRONG') return { label: '재현성 강함', cls: 'border-success/25 bg-success/10 text-success' };
  if (status === 'SUPPORTED') return { label: '재현 가능', cls: 'border-primary/25 bg-primary/10 text-primary' };
  if (status === 'ISOLATED') return { label: '단발 / 고립', cls: 'border-warning/25 bg-warning/10 text-warning' };
  return { label: '--', cls: 'border-border bg-muted text-muted-foreground' };
}

function lapSelectionReasonLabel(reason) {
  const labels = {
    FASTEST_REPRODUCIBLE: '가장 빠른 재현 가능 VALID 랩',
    FASTEST_VALID_FALLBACK: '재현 클러스터 없음 · 가장 빠른 VALID 랩 fallback',
    FASTEST_SUPPORTING_PEER: '대상과 직접 재현되는 가장 빠른 peer',
    SECOND_FASTEST_REPRODUCIBLE: '다음으로 빠른 재현 가능 랩',
    FASTEST_ALTERNATIVE_FALLBACK: '다른 VALID 랩 중 가장 빠른 랩 fallback',
    USER_SELECTED: '사용자 수동 선택',
    NO_ALTERNATIVE_LAP: '비교할 다른 VALID 랩 없음',
    NO_VALID_LAP: 'VALID 랩 없음',
  };
  return labels[reason] || reason || '--';
}

function LapSelectionSummary({ selection, onResetAuto, loading }) {
  if (!selection) return null;
  const targetAuto = selection.target_mode === 'AUTO';
  const referenceAuto = selection.reference_mode === 'AUTO';
  return (
    <div className="mb-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">FASTEST REPRODUCIBLE LAP POLICY</div>
          <p className="mt-2 text-sm leading-relaxed text-foreground">
            자동 대상은 단순히 마지막 랩이 아니라, 비슷한 페이스와 거리 정규화 트레이스가 반복된 VALID 랩 중 가장 빠른 랩입니다.
            단발성 파일 베스트는 반복 클러스터가 있으면 기본 대상에서 제외됩니다.
          </p>
        </div>
        <Button variant="outline"
          type="button"
          onClick={onResetAuto}
          disabled={loading || (targetAuto && referenceAuto)}
          className="shrink-0  border   px-4 py-2 text-sm   transition  disabled:opacity-30"
        >
          자동 선택 복원
        </Button>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-border bg-background/70 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">대상 랩</div>
          <div className="mt-1 font-mono text-base font-semibold text-primary">Lap {selection.target_lap ?? '--'}</div>
          <div className="mt-1 text-xs text-muted-foreground">{targetAuto ? 'AUTO' : 'MANUAL'} · {lapSelectionReasonLabel(selection.target_reason)}</div>
          <div className="mt-1 text-xs text-muted-foreground">peer {selection.target_support_count ?? 0}개 {selection.target_peer_laps?.length ? `· ${selection.target_peer_laps.join(', ')}` : ''}</div>
        </div>
        <div className="rounded-xl border border-border bg-background/70 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">기준 랩</div>
          <div className="mt-1 font-mono text-base font-semibold text-foreground">Lap {selection.reference_lap ?? '--'}</div>
          <div className="mt-1 text-xs text-muted-foreground">{referenceAuto ? 'AUTO' : 'MANUAL'} · {lapSelectionReasonLabel(selection.reference_reason)}</div>
          <div className="mt-1 text-xs text-muted-foreground">peer {selection.reference_support_count ?? 0}개 {selection.reference_peer_laps?.length ? `· ${selection.reference_peer_laps.join(', ')}` : ''}</div>
        </div>
        <div className="rounded-xl border border-border bg-background/70 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">재현 판정</div>
          <div className="mt-1 font-mono text-base font-semibold text-foreground">{selection.repeatable_lap_count ?? 0}/{selection.valid_lap_count ?? 0} laps</div>
          <div className="mt-1 text-xs text-muted-foreground">페이스 ±{formatNumber(selection.pace_tolerance_s, 3, ' s')} · trace RMS ≤ {formatNumber(selection.trace_tolerance_s, 3, ' s')}</div>
        </div>
        <div className="rounded-xl border border-border bg-background/70 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">비교 용도</div>
          <div className="mt-1 text-sm font-semibold text-foreground">대상 = 현재 성능 대표</div>
          <div className="mt-1 text-xs leading-relaxed text-muted-foreground">기준 = 대상을 지지하는 재현 peer. Garage61 외부 기준과는 별도로 비교합니다.</div>
        </div>
      </div>
    </div>
  );
}

function LapTable({ laps = [], targetLap, referenceLap, onTargetChange, onReferenceChange }) {
  const validLaps = laps.filter((lap) => lap.status === 'VALID');
  return (
    <div className="overflow-x-auto">
      <Table className="w-full min-w-[860px] border-collapse text-left text-base">
        <TableHeader>
          <TableRow className="border-b border-border text-sm uppercase tracking-wider text-muted-foreground">
            <TableHead className="px-3 py-3">랩</TableHead>
            <TableHead className="px-3 py-3">상태</TableHead>
            <TableHead className="px-3 py-3">랩타임</TableHead>
            <TableHead className="px-3 py-3">파일 베스트 대비</TableHead>
            <TableHead className="px-3 py-3">재현성</TableHead>
            <TableHead className="px-3 py-3">연료 사용</TableHead>
            <TableHead className="px-3 py-3 text-center">대상</TableHead>
            <TableHead className="px-3 py-3 text-center">기준</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {laps.map((lap) => {
            const repro = lapReproducibilityLabel(lap.reproducibility_status);
            return (
              <TableRow key={lap.lap_number} className="border-b border-border/60 last:border-0">
                <TableCell className="px-3 py-3">
                  <div className="font-mono font-semibold text-foreground">{lap.lap_number}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {lap.is_file_best && <span className="rounded border border-warning/25 bg-warning/10 px-1.5 py-0.5 text-[10px] font-semibold text-warning">FILE BEST</span>}
                    {(lap.selection_roles || []).includes('TARGET') && <span className="rounded border border-primary/25 bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold text-primary">TARGET</span>}
                    {(lap.selection_roles || []).includes('REFERENCE') && <span className="rounded border border-border bg-accent px-1.5 py-0.5 text-[10px] font-semibold text-foreground">REFERENCE</span>}
                  </div>
                </TableCell>
                <TableCell className="px-3 py-3">
                  <span className={cx('rounded-lg border px-2 py-1 text-xs font-semibold', statusColor(lap.status))}>
                    {lap.status}
                  </span>
                </TableCell>
                <TableCell className="px-3 py-3 font-mono font-bold text-foreground">{lap.lap_time || '--'}</TableCell>
                <TableCell
                  className={cx(
                    'px-3 py-3 font-mono',
                    lap.delta_to_file_best_s > 0 ? 'text-destructive' : 'text-success'
                  )}
                >
                  {lap.status === 'VALID' ? formatSigned(lap.delta_to_file_best_s) : '--'}
                </TableCell>
                <TableCell className="px-3 py-3">
                  {lap.status === 'VALID' ? (
                    <div>
                      <span className={cx('rounded-lg border px-2 py-1 text-xs font-semibold', repro.cls)}>{repro.label}</span>
                      <div className="mt-1 text-xs text-muted-foreground">peer {lap.reproducibility_support ?? 0}개{lap.reproducibility_trace_rms_s !== null && lap.reproducibility_trace_rms_s !== undefined ? ` · RMS ${Number(lap.reproducibility_trace_rms_s).toFixed(3)}s` : ''}</div>
                    </div>
                  ) : '--'}
                </TableCell>
                <TableCell className="px-3 py-3 font-mono text-foreground">
                  {formatNumber(lap.fuel_used_l, 2, ' L')}
                </TableCell>
                <TableCell className="px-3 py-3 text-center">
                  <Input
                    type="radio"
                    name="target-lap"
                    disabled={lap.status !== 'VALID'}
                    checked={Number(targetLap) === lap.lap_number}
                    onChange={() => onTargetChange(String(lap.lap_number))}
                    className="h-4 w-4 accent-cyan-500 disabled:opacity-20"
                  />
                </TableCell>
                <TableCell className="px-3 py-3 text-center">
                  <Input
                    type="radio"
                    name="reference-lap"
                    disabled={lap.status !== 'VALID' || validLaps.length < 2}
                    checked={Number(referenceLap) === lap.lap_number}
                    onChange={() => onReferenceChange(String(lap.lap_number))}
                    className="h-4 w-4 accent-slate-400 disabled:opacity-20"
                  />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function ComparisonSummary({ comparison, lapSelection }) {
  if (!comparison) {
    return <EmptyState text="거리 기준 랩 비교를 생성할 유효 랩이 없습니다." />;
  }
  const totalDelta = comparison.total_delta_s;
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <MetricCard
          label={`대상 랩 ${comparison.target_lap}`}
          value={comparison.target_time || '--'}
          icon={TimerReset}
          tone="cyan"
        />
        <MetricCard
          label={`기준 랩 ${comparison.reference_lap ?? '--'}`}
          value={comparison.reference_time || '--'}
          icon={Route}
          tone="orange"
        />
        <MetricCard
          label="총 델타"
          value={formatSigned(totalDelta)}
          note={totalDelta < 0 ? '대상 랩이 더 빠름' : '대상 랩이 더 느림'}
          icon={totalDelta < 0 ? TrendingDown : TrendingUp}
          tone={totalDelta < 0 ? 'emerald' : 'rose'}
        />
        <MetricCard
          label="롤링 베스트"
          value={comparison.rolling_best_time || '--'}
          note={comparison.rolling_best_laps ? `랩 ${comparison.rolling_best_laps[0]}–${comparison.rolling_best_laps[1]} · 트랙 ${formatNumber((comparison.rolling_best_start_pct ?? 0) * 100, 0, '%')}에서 시작 · 베스트보다 ${formatNumber(comparison.rolling_best_gain_s, 3, ' s')} 빠름` : '연속한 유효 랩 2개가 필요합니다'}
          icon={TimerReset}
          tone="emerald"
        />
        <MetricCard
          label="재현 가능 최적"
          value={comparison.reproducible_optimal_time || '--'}
          note={`회수 가능 ${formatNumber(comparison.recoverable_time_s, 3, ' s')}`}
          icon={Target}
          tone="amber"
        />
        <MetricCard
          label="세션 메타 베스트"
          value={comparison.session_best_time || '--'}
          note={
            comparison.session_best_trace_available
              ? '트레이스 포함'
              : '트레이스 미포함: 구간 역산 제외'
          }
          icon={Gauge}
          tone="slate"
        />
      </div>
      {lapSelection && (
        <div className="mt-3 rounded-xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm leading-relaxed text-foreground">
          <span className="font-semibold text-primary">{lapSelection.target_mode === 'AUTO' ? 'AUTO TARGET' : 'MANUAL TARGET'}</span>
          {' '}Lap {lapSelection.target_lap ?? '--'} · {lapSelectionReasonLabel(lapSelection.target_reason)}
          <span className="mx-2 text-muted-foreground">|</span>
          <span className="font-semibold text-foreground">{lapSelection.reference_mode === 'AUTO' ? 'AUTO REFERENCE' : 'MANUAL REFERENCE'}</span>
          {' '}Lap {lapSelection.reference_lap ?? '--'} · {lapSelectionReasonLabel(lapSelection.reference_reason)}
        </div>
      )}
    </div>
  );
}

function PriorityCards({ priorities = [] }) {
  if (!priorities.length) return <EmptyState text="우선순위가 생성되지 않았습니다." />;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {priorities.map((priority) => (
        <article
          key={`${priority.rank}-${priority.corner}`}
          className="rounded-2xl border border-border bg-background/60 p-5"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                Priority {priority.rank}
              </div>
              <h3 className="mt-2 text-lg font-semibold text-foreground">{priority.corner}</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {priority.distance_m ? `${priority.distance_m} m 지점` : '전체 랩'} ·{' '}
                {ISSUE_LABELS[priority.issue_type] || priority.issue_type}
              </p>
            </div>
            {priority.loss_s !== null && priority.loss_s !== undefined && (
              <span className="rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 font-mono text-lg font-semibold text-destructive">
                +{Number(priority.loss_s).toFixed(3)}
              </span>
            )}
          </div>
          <p className="mt-4 text-base font-semibold leading-relaxed text-foreground">
            {priority.action}
          </p>
          <div className="mt-4 flex flex-col gap-2">
            {(priority.evidence || []).map((evidence) => (
              <div key={evidence} className="flex items-start gap-2 text-sm text-muted-foreground">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                <span>{evidence}</span>
              </div>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

function CornerTable({ corners = [], trackLength, loadWindow, referenceLabel }) {
  const [graphFor, setGraphFor] = useState(null);
  const ordered = useMemo(
    () => [...corners].sort((a, b) => a.apex_pct - b.apex_pct),
    [corners]
  );
  if (!ordered.length) return <EmptyState text="코너 자동 분할 결과가 없습니다." />;
  // One value per line, never broken inside: "164 m · 1.45 g" and "+0.059 s" stay whole.
  const Value = ({ children, className = '' }) => <div className={cx('whitespace-nowrap font-mono tabular-nums', className)}>{children}</div>;
  const Sub = ({ children }) => <div className="mt-1 whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground">{children}</div>;
  const braking = (metrics) => {
    if (metrics?.braking_distance_m == null) return null;
    const decel = metrics.avg_decel_g != null && metrics.avg_decel_g >= 0.1 ? `${metrics.avg_decel_g.toFixed(2)} g` : '가벼운 제동';
    return `${Math.round(metrics.braking_distance_m)} m · ${decel}`;
  };
  const Band = ({ band }) => (band ? (
    <>
      <Value className={cx('font-semibold', band.delta_s > 0 ? 'text-destructive' : 'text-success')}>{formatSigned(band.delta_s)}</Value>
      <Sub>{Math.round(band.from_kph)}→{Math.round(band.to_kph)} km/h</Sub>
    </>
  ) : <span className="text-muted-foreground">--</span>);
  return (
    <div className="overflow-x-auto">
      <p className="mb-4 max-w-4xl text-sm leading-relaxed text-muted-foreground">
        <b className="text-foreground">제동</b>은 브레이크를 밟기 시작해 놓을 때까지(트레일 브레이킹 포함)의 거리와 평균 감속입니다.
        {' '}<b className="text-foreground">감속·가속</b>은 두 랩이 모두 지난 같은 속도 범위를 지나는 데 걸린 시간 차이입니다. 감속은 코너 진입 제동, 가속은 코너를 나와 풀스로틀로 달린 구간이고, +는 기준보다 느린 것입니다.
        {' '}각 칸의 아래 줄 회색 글씨는 기준 랩 값입니다.
        {loadWindow && <>{' '}<b className="text-primary">그래프 보기</b>나 줄을 누르면 그 코너를 이번 랩과 기준으로 겹친 그래프와, 다르게 한 곳의 설명이 펼쳐집니다.</>}
      </p>
      <Table className="w-full min-w-[1320px] border-collapse text-left text-sm">
        <TableHeader>
          <TableRow className="border-b border-border text-xs tracking-wider text-muted-foreground">
            {loadWindow && <TableHead className="w-[1%] px-3 py-3">그래프</TableHead>}
            <TableHead className="px-3 py-3">구간</TableHead>
            <TableHead className="px-3 py-3 text-right">델타</TableHead>
            <TableHead className="px-3 py-3">유형</TableHead>
            <TableHead className="px-3 py-3 text-right">최저 속도</TableHead>
            <TableHead className="px-3 py-3 text-right">풀스로틀 지점</TableHead>
            <TableHead className="px-3 py-3 text-right">ABS</TableHead>
            <TableHead className="px-3 py-3 text-right">제동</TableHead>
            <TableHead className="px-3 py-3 text-right">감속 시간</TableHead>
            <TableHead className="px-3 py-3 text-right">가속 시간</TableHead>
            <TableHead className="min-w-[280px] px-3 py-3">코칭</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ordered.map((corner) => {
            const target = corner.target || {};
            const reference = corner.reference || {};
            const fullThrottleDelta =
              target.full_throttle_pct != null && reference.full_throttle_pct != null
                ? (target.full_throttle_pct - reference.full_throttle_pct) * 100
                : null;
            return (
              <React.Fragment key={corner.id}>
              <TableRow
                className={cx('border-b border-border/60 align-top last:border-0 hover:bg-accent/30', loadWindow && 'cursor-pointer', graphFor === corner.id && 'bg-primary/5')}
                onClick={loadWindow ? (event) => { if (!event.target.closest('button')) setGraphFor(graphFor === corner.id ? null : corner.id); } : undefined}
              >
                {loadWindow && (
                  <TableCell className="px-3 py-4">
                    <button type="button" aria-expanded={graphFor === corner.id} aria-label={`${corner.label} 그래프 ${graphFor === corner.id ? '닫기' : '보기'}`}
                      onClick={() => setGraphFor(graphFor === corner.id ? null : corner.id)}
                      className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border px-3 py-2 text-sm font-semibold transition',
                        graphFor === corner.id ? 'border-primary bg-primary text-primary-foreground' : 'border-primary/50 bg-primary/10 text-primary hover:bg-primary/20')}>
                      <LineChart className="h-4 w-4" aria-hidden="true" />
                      {graphFor === corner.id ? '닫기' : '보기'}
                      <ChevronDown className={cx('h-4 w-4 transition-transform', graphFor === corner.id && 'rotate-180')} aria-hidden="true" />
                    </button>
                  </TableCell>
                )}
                <TableCell className="px-3 py-4">
                  <div className="whitespace-nowrap text-base font-semibold text-foreground">{corner.label}</div>
                  <Sub>{(corner.apex_pct * 100).toFixed(1)}% · {corner.distance_m} m · {corner.speed_class}</Sub>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className={cx('text-base font-semibold', corner.delta_s > 0 ? 'text-destructive' : 'text-success')}>{formatSigned(corner.delta_s)}</Value>
                </TableCell>
                <TableCell className="px-3 py-4">
                  <span className="whitespace-nowrap rounded-lg border border-border bg-background/70 px-2 py-1 text-xs font-semibold text-foreground">
                    {ISSUE_LABELS[corner.issue_type] || corner.issue_type}
                  </span>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className="text-foreground">{formatNumber(target.min_speed_kph, 1, ' km/h')}</Value>
                  <Sub>{formatNumber(reference.min_speed_kph, 1, ' km/h')}</Sub>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className="text-foreground">{target.full_throttle_pct != null ? `${(target.full_throttle_pct * 100).toFixed(1)}%` : '--'}</Value>
                  <Sub>차이 {fullThrottleDelta === null ? '--' : `${fullThrottleDelta > 0 ? '+' : ''}${fullThrottleDelta.toFixed(2)}%`}</Sub>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className="text-foreground">{formatNumber(target.abs_duty_pct, 1, '%')}</Value>
                </TableCell>
                <TableCell className="px-3 py-4 text-right" data-testid={`braking-${corner.id}`}>
                  <Value className="text-foreground">{braking(target) || '--'}</Value>
                  <Sub>{braking(reference) || '--'}</Sub>
                  {target.brake_entry_speed_kph != null && <Sub>{Math.round(target.brake_entry_speed_kph)}→{Math.round(target.brake_end_speed_kph)} km/h</Sub>}
                </TableCell>
                <TableCell className="px-3 py-4 text-right" data-testid={`bands-${corner.id}`}>
                  <Band band={corner.braking_band} />
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Band band={corner.drive_band} />
                </TableCell>
                <TableCell className="px-3 py-4 text-sm leading-relaxed text-foreground">
                  {corner.driver_action}
                </TableCell>
              </TableRow>
              {graphFor === corner.id && (
                <TableRow className="border-b border-border/60">
                  <TableCell colSpan={11} className="px-3 py-4">
                    <div className="sticky left-0 max-w-[900px]">
                      <CornerTrace corner={corner} trackLength={trackLength} load={loadWindow} referenceLabel={referenceLabel} />
                    </div>
                  </TableCell>
                </TableRow>
              )}
              </React.Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function TirePanel({ tires }) {
  if (!tires || !tires.wheels) return <EmptyState text="타이어 채널이 없습니다." />;
  const trend = tires.lap_trend || [];
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm leading-relaxed text-muted-foreground">
        대상 랩이 결승선을 지나기 직전 1초의 값입니다. iRacing 주행 파일에서 <b className="text-foreground">표면 온도와 열간 압력은 주행 중에 계속 기록</b>되고,
        카커스(내부) 온도와 마모는 피트에 들어갔을 때만 바뀌므로 여기서는 쓰지 않습니다. 표면 온도는 코너마다 크게 오르내리므로(한 랩에 30~40°C 차이),
        같은 지점(결승선 직전 직선)끼리 비교하는 값으로 보세요.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Object.entries(tires.wheels).map(([wheel, data]) => (
          <div key={wheel} className="rounded-2xl border border-border bg-background/60 p-4">
            <div className="flex items-center justify-between">
              <span className="font-mono text-lg font-semibold text-foreground">{wheel}</span>
              <span className="text-xs text-muted-foreground">표면 온도 (안·가운데·바깥)</span>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              {(data.surface_lmr_c || []).map((value, index) => (
                <div key={`${wheel}-${index}`} className="rounded-lg bg-muted px-2 py-2">
                  <div className="text-xs text-muted-foreground">{['L', 'M', 'R'][index]}</div>
                  <div className="mt-1 font-mono text-base font-semibold text-warning">
                    {formatNumber(value, 1, '°')}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-col gap-2 text-sm">
              <div className="flex justify-between text-muted-foreground">
                <span>열간 압력</span>
                <span className="font-mono font-bold text-foreground">
                  {formatNumber(data.hot_pressure_kpa, 1, ' kPa')}
                </span>
              </div>
              <div className="flex justify-between text-muted-foreground">
                <span>압력 상승</span>
                <span className="font-mono font-bold text-foreground">
                  {formatNumber(data.pressure_gain_kpa, 1, ' kPa')}
                </span>
              </div>
              <div className="flex justify-between text-muted-foreground">
                <span>냉간 압력(셋업)</span>
                <span className="font-mono text-foreground">{formatNumber(data.cold_pressure_kpa, 1, ' kPa')}</span>
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>카커스 온도·마모</span>
                <span>{data.carcass_status === 'DYNAMIC' && data.wear_status === 'DYNAMIC' ? '주행 중 기록됨' : '피트에서만 갱신 (사용 안 함)'}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <MetricMini
          label="랩당 표면 온도 변화"
          value={formatNumber(tires.surface_temp_slope_c_per_lap, 2, ' °C/lap')}
        />
        <MetricMini
          label="랩당 열간 압력 변화"
          value={formatNumber(tires.hot_pressure_slope_kpa_per_lap, 2, ' kPa/lap')}
        />
      </div>
      {trend.length > 0 && (
        <div className="overflow-x-auto rounded-2xl border border-border bg-background/50">
          <Table className="min-w-full text-left text-sm">
            <TableHeader className="border-b border-border bg-muted/70 text-xs uppercase tracking-wider text-muted-foreground">
              <TableRow>
                <TableHead className="px-4 py-3">랩</TableHead>
                <TableHead className="px-4 py-3">랩타임</TableHead>
                <TableHead className="px-4 py-3">평균 표면 온도</TableHead>
                <TableHead className="px-4 py-3">평균 노면 온도</TableHead>
                <TableHead className="px-4 py-3">평균 열간 압력</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody className="divide-y divide-slate-800">
              {trend.map((row) => (
                <TableRow key={row.lap_number}>
                  <TableCell className="px-4 py-3 font-mono font-semibold text-foreground">{row.lap_number}</TableCell>
                  <TableCell className="px-4 py-3 font-mono text-primary">
                    {row.lap_time_s ? `${Math.floor(row.lap_time_s / 60)}:${(row.lap_time_s % 60).toFixed(3).padStart(6, '0')}` : '--'}
                  </TableCell>
                  <TableCell className="px-4 py-3 font-mono text-warning">
                    {formatNumber(row.mean_surface_temp_c, 1, ' °C')}
                  </TableCell>
                  <TableCell className="px-4 py-3 font-mono text-muted-foreground" data-testid={`track-temp-${row.lap_number}`}>
                    {formatNumber(row.mean_track_temp_c, 1, ' °C')}
                  </TableCell>
                  <TableCell className="px-4 py-3 font-mono text-foreground">
                    {formatNumber(row.mean_hot_pressure_kpa, 1, ' kPa')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="rounded-xl border border-border bg-background/50 p-4 text-base leading-relaxed text-foreground">
        <Info className="mr-2 inline h-4 w-4 text-primary" />
        {tires.reason}
      </div>
    </div>
  );
}

function RideAeroPanel({ rideHeight, aero }) {
  const splitter = rideHeight?.channels?.CFSRrideHeight;
  const measurable = rideHeight?.bottoming_detected !== null && rideHeight?.bottoming_detected !== undefined && rideHeight?.bottoming_available !== false;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div
        className={cx(
          'rounded-2xl border p-5',
          rideHeight?.bottoming_detected
            ? 'border-destructive/40 bg-destructive/20'
            : measurable ? 'border-success/30 bg-success/10' : 'border-border bg-background/40'
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Bottoming</div>
            <div
              className={cx(
                'mt-2 text-xl font-semibold',
                rideHeight?.bottoming_detected ? 'text-destructive' : measurable ? 'text-success' : 'text-muted-foreground'
              )}
            >
              {!measurable ? '측정 불가' : rideHeight?.bottoming_detected ? '검출됨' : '미검출'}
            </div>
          </div>
          {rideHeight?.bottoming_detected ? (
            <ShieldAlert className="h-8 w-8 text-destructive" />
          ) : measurable ? (
            <CheckCircle2 className="h-8 w-8 text-success" />
          ) : <Info className="h-8 w-8 text-muted-foreground" />}
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 text-sm">
          <MetricMini label="Splitter minimum" value={formatNumber(splitter?.minimum_mm, 1, ' mm')} />
          <MetricMini label="Splitter p0.1" value={formatNumber(splitter?.p0_1_mm, 1, ' mm')} />
          <MetricMini label="Events" value={String(rideHeight?.bottoming_events ?? '--')} />
          <MetricMini label="Median" value={formatNumber(splitter?.median_mm, 1, ' mm')} />
        </div>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">{rideHeight?.threshold_note}</p>
      </div>

      <div className="rounded-2xl border border-border bg-background/60 p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">High-speed aero</div>
            <div className="mt-2 text-xl font-semibold text-primary">
              Pitch {formatNumber(aero?.aero_pitch_median_mm, 1, ' mm')}
            </div>
          </div>
          <Layers3 className="h-8 w-8 text-primary" />
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 text-sm">
          <MetricMini label="Front median" value={formatNumber(aero?.front_ride_height_median_mm, 1, ' mm')} />
          <MetricMini label="Rear median" value={formatNumber(aero?.rear_ride_height_median_mm, 1, ' mm')} />
          <MetricMini label="High-speed splitter min" value={formatNumber(aero?.minimum_splitter_height_high_speed_mm, 1, ' mm')} />
          <MetricMini label="Rear wing" value={formatNumber(aero?.rear_wing_angle_deg, 1, '°')} />
        </div>
      </div>
    </div>
  );
}

function MetricMini({ label, value }) {
  return (
    <div className="rounded-xl border border-border bg-muted/70 p-3">
      <div data-help={label} className="flex items-center gap-1 text-xs uppercase tracking-wider text-muted-foreground">{label}<HelpHint topic={label} /></div>
      <div className="mt-1.5 font-mono text-base font-semibold text-foreground">{value}</div>
    </div>
  );
}

function ClassificationPanel({ classification }) {
  if (!classification) return null;
  const label = classification.label;
  const parallel = classification.parallel_assessment || {};
  const tone =
    label === 'SETUP_LIMITED'
      ? 'border-destructive/40 bg-destructive/20 text-destructive'
      : label === 'DRIVER_LIMITED'
      ? 'border-primary/40 bg-primary/20 text-primary'
      : 'border-warning/40 bg-warning/20 text-warning';
  return (
    <div className={cx('rounded-2xl border p-5', tone)}>
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
            Limitation classification
          </div>
          <div className="mt-2 text-2xl font-semibold">{CLASSIFICATION_LABELS[label] || label}</div>
        </div>
        <div className="rounded-xl border border-current/20 bg-background/50 px-4 py-3 text-center">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">Confidence</div>
          <div className="mt-1 font-mono text-xl font-semibold">
            {formatNumber((classification.confidence || 0) * 100, 0, '%')}
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-col gap-2 text-base leading-relaxed text-foreground">
        {(classification.reasons || []).map((reason) => (
          <div key={reason} className="flex items-start gap-2">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
            <span>{reason}</span>
          </div>
        ))}
      </div>

      {(parallel.external_gap_s !== undefined || parallel.note) && (
        <div className="mt-5 rounded-2xl border border-border/80 bg-background/65 p-4 text-foreground">
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Performance optimization
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            <MetricMini
              label="외부 기준 격차"
              value={parallel.external_gap_s === null || parallel.external_gap_s === undefined
                ? '--'
                : formatSigned(parallel.external_gap_s)}
            />
            <MetricMini
              label="드라이빙 개선"
              value={parallel.driver_improvement_required ? '필요' : '낮음'}
            />
            <MetricMini
              label="Setup Assist"
              value={parallel.setup_assist_opportunity ? 'A/B 후보 있음' : '후보 없음'}
            />
          </div>
          {parallel.note && (
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{parallel.note}</p>
          )}
        </div>
      )}
    </div>
  );
}

function hasSetupNumericValue(value) {
  if (value === null || value === undefined || value === '') return false;
  const numeric = Number(value);
  return Number.isFinite(numeric);
}

function formatSetupNumber(value) {
  if (!hasSetupNumericValue(value)) return '';
  const numeric = Number(value);
  return Number.isInteger(numeric) ? String(numeric) : String(Number(numeric.toFixed(4)));
}

function localizeSetupStep(stepText, direction) {
  const text = String(stepText || '').trim();
  const normalized = text.toLowerCase();
  if (normalized.includes('toward 0')) return 'Garage 1 click · 0 방향';
  if (normalized.includes('rearward')) return 'Garage 1 click · 뒤쪽';
  if (normalized.includes('forward')) return 'Garage 1 click · 앞쪽';
  if (normalized.includes('decrease')) return 'Garage 1 click · 감소';
  if (normalized.includes('increase')) return 'Garage 1 click · 증가';
  if (text) return text;
  const directionMap = {
    DECREASE: 'Garage 1 click · 감소',
    INCREASE: 'Garage 1 click · 증가',
    TOWARD_ZERO: 'Garage 1 click · 0 방향',
  };
  return directionMap[String(direction || '').toUpperCase()] || 'Garage 1 click 변경';
}

function formatSetupOriginal(adjustment) {
  if (!hasSetupNumericValue(adjustment?.original_value)) return '--';
  const number = formatSetupNumber(adjustment.original_value);
  return `${number}${adjustment?.unit ? ` ${adjustment.unit}` : ''}`;
}

function formatSetupTarget(adjustment) {
  if (adjustment?.target_display) return String(adjustment.target_display);
  if (hasSetupNumericValue(adjustment?.value)) {
    const number = formatSetupNumber(adjustment.value);
    const suffix = adjustment?.numeric_target_estimated ? ' ≈' : '';
    return `${number}${adjustment?.unit ? ` ${adjustment.unit}` : ''}${suffix}`;
  }
  return localizeSetupStep(adjustment?.step_text, adjustment?.direction);
}

function SetupAdjustmentCard({ adjustment, experimental = false, onStart, canStart }) {
  const [actualOriginal, setActualOriginal] = useState(adjustment.original_value ?? '');
  const [actualTarget, setActualTarget] = useState(adjustment.value ?? '');
  useEffect(() => { setActualOriginal(adjustment.original_value ?? ''); setActualTarget(adjustment.value ?? ''); }, [adjustment.original_value, adjustment.value]);
  const confidence = Number(adjustment?.confidence);
  const priorityScore = Number(adjustment?.priority_score);
  const sourceLabels = {
    GARAGE61_STRAIGHT_CONSENSUS: 'Garage61 직선 컨센서스',
    GARAGE61_CORNER_CONSENSUS: 'Garage61 코너 컨센서스',
    GARAGE61_HIGH_SPEED_STABILITY: 'Garage61 고속 안정성',
    GARAGE61_REFERENCE_CONSENSUS: 'Garage61 컨센서스',
    CURRENT_SESSION_PARTIAL_PATTERN: '현재 세션 부분 반복',
    CURRENT_SESSION_SINGLE_OBJECTIVE_EVENT: '현재 세션 단일 객관 신호',
    CURRENT_SESSION_SINGLE_BOTTOMING: '현재 세션 단일 신호',
    CURRENT_SESSION_REPEATABILITY: '현재 세션 반복성',
    DRIVER_FEEDBACK_EXPLORATION: '선택한 증상 · 탐색 가설',
  };
  const categoryLabels = {
    AERO_DRAG: '공기저항 · 최고속',
    AERO_DRAG_SECONDARY: '공기저항 (보조)',
    TOP_SPEED_OPPORTUNITY: '최고속',
    EXIT_TRACTION: '탈출 트랙션',
    ROTATION_DEFICIT: '회전 부족',
    REAR_STABILITY: '리어 안정성',
    DIFF_ROTATION: '디퍼렌셜 회전',
    HIGH_SPEED_STABILITY: '고속 에어로 안정성',
    BRAKE_BALANCE: '브레이크 밸런스',
  };
  const sourceLabel = sourceLabels[adjustment?.source] || adjustment?.source || '현재 세션 근거';
  const originalText = formatSetupOriginal(adjustment);
  const targetText = formatSetupTarget(adjustment);

  return (
    <article className={cx(
      'rounded-2xl border bg-background/70 p-5',
      experimental ? 'border-warning/30' : 'border-primary/30'
    )}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-base font-bold text-foreground">{settingName(adjustment)}</div>
            {SETTING_NAMES[adjustment.setting] && adjustment.label && <span className="text-xs text-muted-foreground">Garage: {adjustment.label}</span>}
            {adjustment.category && (
              <span className="rounded-md border border-sky-500/25 bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-sky-300">
                {categoryLabels[adjustment.category] || adjustment.category}
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-baseline gap-2 font-mono">
            <span className="text-lg font-bold text-muted-foreground line-through">{originalText}</span>
            <span className="text-muted-foreground">→</span>
            <span className={cx('text-2xl font-semibold', experimental ? 'text-warning' : 'text-primary')}>
              {targetText}
            </span>
          </div>
          {adjustment?.numeric_target_estimated && (
            <div className="mt-1 text-xs font-bold text-warning/80">
              수치는 로컬 SQLite의 동일 차량 셋업 이력에서 click 간격을 추정했습니다. Garage에서 1 click인지 확인 후 적용하십시오.
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <span className={cx(
            'rounded-lg border px-2 py-1 text-xs font-semibold',
            experimental
              ? 'border-warning/30 bg-warning/10 text-warning'
              : 'border-primary/30 bg-primary/10 text-primary'
          )}>
            {adjustment.tier === 'EXPLORATORY' ? '탐색 후보 · 근거 약함' : experimental ? 'A/B 시험' : '근거 확인 · 시험'}
          </span>
          {Number.isFinite(confidence) && (
            <span className="font-mono text-xs font-bold text-muted-foreground">
              근거 점수 {Math.round(confidence * 100)}%
            </span>
          )}
          {Number.isFinite(priorityScore) && (
            <span className="font-mono text-xs font-bold text-muted-foreground">
              우선도 {Math.round(priorityScore * 100)}
            </span>
          )}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 text-xs font-semibold">
        <span className="rounded-lg border border-border bg-muted px-2 py-1 text-muted-foreground">
          {sourceLabel}
        </span>
        {adjustment.reference_support?.reference_count > 0 && (
          <span className="rounded-lg border border-accent/25 bg-accent/10 px-2 py-1 text-foreground">
            빠른 기준 {adjustment.reference_support.reference_count}개 · 드라이버 {adjustment.reference_support.unique_driver_count ?? '--'}명
          </span>
        )}
        {adjustment.status === 'TEST_ONLY_DIRECTIONAL' && (
          <span className="rounded-lg border border-border bg-muted px-2 py-1 text-muted-foreground">
            수치 미추정 · Garage 1-click
          </span>
        )}
        {adjustment.memory_support?.matching_outcomes > 0 && (
          <span className="rounded-lg border border-success/25 bg-success/10 px-2 py-1 text-success">
            개인 A/B 이력 {adjustment.memory_support.matching_outcomes}건 반영
          </span>
        )}
        {adjustment.personal_sensitivity_support?.decisive_count > 0 && (
          <span className="rounded-lg border border-fuchsia-500/25 bg-fuchsia-500/10 px-2 py-1 text-fuchsia-300">
            차량 민감도 {adjustment.personal_sensitivity_support.keep_count}/{adjustment.personal_sensitivity_support.decisive_count} KEEP
          </span>
        )}
      </div>
      <div className="mt-4 flex flex-col gap-2 text-sm leading-relaxed">
        {adjustment.expected_effect && (
          <p className="font-semibold text-sky-200">목표: {adjustment.expected_effect}</p>
        )}
        <p className="text-foreground">근거: {adjustment.evidence}</p>
        <p className="text-muted-foreground">트레이드오프: {adjustment.tradeoff}</p>
        <p className={experimental ? 'text-warning' : 'text-primary'}>검증: {adjustment.validation}</p>
        {adjustment.validator?.messages?.length > 0 && (
          <div className="rounded-lg border border-border bg-muted/70 p-2 text-xs text-muted-foreground">
            {adjustment.validator.messages.join(' · ')}
          </div>
        )}
      </div>
      {onStart && <div className="mt-4"><p className="mb-2 flex items-center gap-1 text-xs text-muted-foreground">실제 차고 값으로 시험 준비<HelpHint topic="actualValue" /></p><div className="grid gap-2 sm:grid-cols-2"><label className="text-xs text-muted-foreground">Garage 현재값<Input data-help="actualValue" aria-label={`${adjustment.label || adjustment.setting} 실제 현재값`} type="number" step="any" value={actualOriginal} onChange={(event) => setActualOriginal(event.target.value)} className="mt-1 w-full  border   p-2 text-sm" /></label><label className="text-xs text-muted-foreground">Garage 목표값<Input data-help="actualValue" aria-label={`${adjustment.label || adjustment.setting} 실제 목표값`} type="number" step="any" value={actualTarget} onChange={(event) => setActualTarget(event.target.value)} className="mt-1 w-full  border   p-2 text-sm" /></label></div><Button variant="default" data-help="startTrial" type="button" onClick={() => onStart({ ...adjustment, original_value: Number(actualOriginal), value: Number(actualTarget) })} disabled={!canStart || !hasSetupNumericValue(actualOriginal) || !hasSetupNumericValue(actualTarget) || Number(actualOriginal) === Number(actualTarget)} className="mt-3  coach-primary-action px-4 py-2 text-sm   disabled:opacity-40">이 변경 시험하기</Button></div>}
    </article>
  );
}

function SetupRecommendation({ recommendation, onStart, canStart }) {
  if (!recommendation) return <EmptyState text="셋업 판정 결과가 없습니다." />;
  const confirmed = recommendation.confirmed_adjustments || recommendation.adjustments || [];
  const experimental = recommendation.experimental_candidates || [];
  const hasCandidate = confirmed.length > 0 || experimental.length > 0;
  const isHold = !hasCandidate || recommendation.decision === 'HOLD';
  const basisLabel = recommendation.decision_basis === 'SETUP_ASSIST_EXTERNAL_CONSENSUS'
    ? '외부 기준 컨센서스'
    : recommendation.decision_basis === 'CONFIRMED_REPEATABILITY'
    ? '현재 세션 반복성'
    : recommendation.decision_basis === 'EXPERIMENTAL_CURRENT_SESSION'
    ? '현재 세션 실험 후보'
    : recommendation.decision_basis === 'DRIVER_FEEDBACK_EXPLORATION'
    ? '선택한 증상으로 탐색'
    : recommendation.decision_basis || '근거 부족';
  const straight = recommendation.performance_diagnosis?.straight_line || {};
  const straightDiagnosisLabels = {
    AERO_DRAG_SUSPECT: '직선 후반 Drag / 최고속 병목',
    PERSISTENT_TOP_SPEED_DEFICIT: '반복적인 최고속 부족 · 셋업 보조 가치 있음',
    EXIT_SPEED_LIMITED: '직선 진입속도 병목',
    MIXED: '탈출 + 직선 성능 혼합 병목',
    NO_STRAIGHT_BOTTLENECK: '측정 완료 · 반복적인 직선 병목 없음',
    NO_REPEATED_STRAIGHT_DEFICIT: '측정 완료 · 반복적인 직선 병목 없음',
    INSUFFICIENT_STRAIGHT_DATA: '직선 분석 불충분 · 병목 판정 보류',
  };
  const straightInsufficient = ['INSUFFICIENT_STRAIGHT_DATA', 'NO_STRAIGHTS', 'MISSING_CHANNELS'].includes(straight.status)
    || straight.diagnosis === 'INSUFFICIENT_STRAIGHT_DATA';
  const straightNoBottleneck = ['NO_STRAIGHT_BOTTLENECK', 'NO_REPEATED_STRAIGHT_DEFICIT'].includes(straight.diagnosis);
  const straightHasResult = Boolean(straight.status || straight.diagnosis);
  const eligibleStraights = Number(straight.eligible_straights || 0);
  const requiredStraights = Number(straight.required_straights || 2);
  const formatStraightKph = (value, signed = false) => {
    if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '측정 불가';
    const numeric = Number(value);
    const prefix = signed && numeric > 0 ? '+' : '';
    return `${prefix}${numeric.toFixed(1)} km/h`;
  };

  return (
    <div className="flex flex-col gap-5">
      <div className={cx(
        'rounded-2xl border p-5',
        isHold ? 'border-warning/40 bg-warning/20' : 'border-primary/40 bg-primary/20'
      )}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            {isHold ? (
              <AlertTriangle className="h-6 w-6 text-warning" />
            ) : (
              <FlaskConical className="h-6 w-6 text-primary" />
            )}
            <div>
              <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Decision</div>
              <div className={cx('mt-1 text-xl font-semibold', isHold ? 'text-warning' : 'text-primary')}>
                {recommendation.decision_basis === 'DRIVER_FEEDBACK_EXPLORATION' ? '작은 변경부터 탐색' : hasCandidate ? 'A/B 시험' : '추천 후보 없음'}
              </div>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-background/65 px-4 py-3 text-right">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">판정 근거</div>
            <div className="mt-1 text-sm font-semibold text-foreground">{basisLabel}</div>
          </div>
        </div>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          드라이빙 제한으로 분류되어도 셋업으로 외부 기준 격차를 줄일 가능성이 있으면 Setup Assist A/B 후보를 별도로 제시합니다.
          실험 후보는 정답 판정이 아니라 한 단계 변경 후 검증할 항목입니다.
        </p>
      </div>

      {recommendation.setup_mode === 'EXPLORE' && <div className="rounded-xl border border-primary/25 bg-primary/15 p-4 text-sm leading-relaxed text-foreground">
        <p className="font-bold text-primary">탐색 추천 · 일정한 랩타임은 필요하지 않습니다</p>
        <p className="mt-2">{recommendation.exploration?.reason}</p>
        <p className="mt-2 text-muted-foreground">체감은 1~2랩으로 먼저 확인하고, 속도 효과는 자연 편차를 고려한 A/B로 따로 확인합니다.</p>
      </div>}

      {straightHasResult && (
        <div className={cx(
          'rounded-2xl border p-5',
          straightInsufficient
            ? 'border-warning/35 bg-warning/15'
            : straight.aero_drag_suspect || straight.persistent_top_speed_opportunity
            ? 'border-fuchsia-500/30 bg-fuchsia-950/15'
            : straightNoBottleneck
            ? 'border-success/25 bg-success/10'
            : 'border-border bg-background/55'
        )}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Performance setup diagnosis</div>
              <div className={cx(
                'mt-1 text-lg font-semibold',
                straightInsufficient
                  ? 'text-warning'
                  : straight.aero_drag_suspect || straight.persistent_top_speed_opportunity
                  ? 'text-fuchsia-300'
                  : straightNoBottleneck
                  ? 'text-success'
                  : 'text-foreground'
              )}>
                {straightDiagnosisLabels[straight.diagnosis] || straightDiagnosisLabels[straight.status] || straight.diagnosis || '직선 성능 분석'}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">분석 직선</div>
                <div className={cx('mt-1 font-mono font-semibold', straightInsufficient ? 'text-warning' : 'text-foreground')}>
                  {eligibleStraights}개
                </div>
                <div className="mt-0.5 text-[10px] text-muted-foreground">반복 판정 최소 {requiredStraights}개</div>
              </div>
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">Drag 병목</div>
                <div className="mt-1 font-mono font-semibold text-foreground">{straight.drag_support_count ?? 0}/{eligibleStraights}</div>
              </div>
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">후반 속도 부족</div>
                <div className={cx('mt-1 font-mono font-semibold', straightInsufficient ? 'text-warning' : 'text-destructive')}>
                  {formatStraightKph(straight.median_late_speed_deficit_kph)}
                </div>
              </div>
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">Gap 증가</div>
                <div className={cx('mt-1 font-mono font-semibold', straightInsufficient ? 'text-warning' : 'text-destructive')}>
                  {formatStraightKph(straight.median_gap_growth_kph, true)}
                </div>
              </div>
            </div>
          </div>

          {straightInsufficient ? (
            <p className="mt-3 text-sm leading-relaxed text-warning/80">
              {straight.status === 'MISSING_CHANNELS'
                ? '필수 속도·스로틀·브레이크·조향 채널이 부족해 직선 병목을 판정할 수 없습니다.'
                : `유효 장거리 풀스로틀 직선이 ${eligibleStraights}개로 반복 판정 최소 ${requiredStraights}개에 못 미칩니다.`}
              {' '}이 상태는 "병목 없음"이 아니라 "측정 불충분"이며, 셋업의 Rear wing/drag 방향을 부재 근거로 판단하지 않습니다.
            </p>
          ) : straightNoBottleneck ? (
            <p className="mt-3 text-sm leading-relaxed text-success/75">
              유효 직선 {eligibleStraights}개를 실제 비교했지만 반복적인 Drag/최고속 병목 기준을 넘지 않았습니다.
              위 속도 수치는 문제 구간이 아니라 전체 유효 직선의 중앙값이므로, 이제 "-- km/h"와 병목 없음이 혼동되지 않습니다.
            </p>
          ) : (
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              직선 진입부터 느린 경우와 풀스로틀 직선 후반에서 속도차가 계속 커지는 경우를 분리합니다.
              후자만 반복될 때 Rear wing/drag 계열 셋업 후보의 우선도를 올립니다.
            </p>
          )}

          {straight.data_quality === 'LIMITED' && !straightInsufficient && (
            <div className="mt-3 rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs text-warning/75">
              직선 표본은 반복 판정 최소치는 충족하지만 강한 진단 권장치({straight.recommended_straights || 3}개)보다 적습니다. 셋업 후보 신뢰도는 보수적으로 해석하십시오.
            </div>
          )}
        </div>
      )}

      {confirmed.length > 0 && (
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-primary">확정 근거 A/B 후보</h3>
            <span className="text-xs text-muted-foreground">복수 랩의 객관적 반복 신호</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {confirmed.map((adjustment, index) => (
              <SetupAdjustmentCard key={`${adjustment.setting}-${adjustment.value}-${index}`} adjustment={adjustment} onStart={onStart} canStart={canStart} />
            ))}
          </div>
        </div>
      )}

      {experimental.length > 0 && (
        <div>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-warning">Setup Assist · 실험 A/B 후보</h3>
            <span className="text-xs text-muted-foreground">후보마다 측정 근거와 탐색 가설을 표시합니다.</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {experimental.map((adjustment, index) => (
              <SetupAdjustmentCard
                key={`${adjustment.setting}-${adjustment.value}-${adjustment.source}-${index}`}
                adjustment={adjustment}
                onStart={onStart}
                canStart={canStart}
                experimental
              />
            ))}
          </div>
        </div>
      )}

      {!hasCandidate && (
        <div className="rounded-2xl border border-border bg-background/60 p-5 text-base leading-relaxed text-foreground">
          {recommendation.setup_mode === 'EXPLORE' ? '위 피드백에서 구간·속도·증상을 선택하고 분석을 갱신하세요. 선택한 코너 측정과 현재 셋업 값으로 작은 변경 후보를 찾습니다.' : '현재 측정 근거로 만들 수 있는 셋업 후보가 없습니다. 탐색 추천으로 전환하면 선택한 증상으로 작은 변경을 시험할 수 있습니다.'}
        </div>
      )}

      <div>
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Validation plan
        </h3>
        <div className="flex flex-col gap-2">
          {(recommendation.validation_plan || []).map((item, index) => (
            <div key={`${item}-${index}`} className="flex items-start gap-3 rounded-xl border border-border bg-background/50 p-3 text-base text-foreground">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 font-mono text-sm font-semibold text-primary">
                {index + 1}
              </span>
              <span className="pt-0.5 leading-relaxed">{item}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BriefingViewer({ briefing }) {
  if (!briefing) return <EmptyState text="브리핑이 없습니다." />;
  return (
    <div className="flex flex-col gap-5">
      <div className="border border-border bg-surface p-5 text-base leading-relaxed text-foreground">
        {briefing.summary}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {(briefing.sections || []).map((section) => (
          <article key={section.title} className="rounded-2xl border border-border bg-background/55 p-5">
            <h3 className="font-semibold text-foreground">{section.title}</h3>
            <div className="mt-4 flex flex-col gap-3 text-base leading-relaxed text-foreground">
              {(section.items || []).map((item) => (
                <div key={item} className="flex items-start gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>
      <div className="text-right text-xs font-bold uppercase tracking-wider text-muted-foreground">
        Source: {briefing.source === 'GCP_VERTEX_AI_BRIEFING'
          ? 'GCP Vertex AI · 토큰 사용'
          : '로컬 결정론적 엔진 · 토큰 미사용'}
      </div>
    </div>
  );
}

function AIUsageBanner({ usage, aiEnabled }) {
  const tokens = usage?.tokens || {};
  if (usage?.used) {
    return <Alert><Sparkles aria-hidden="true" /><AlertTitle>이번 결과에 GCP AI가 사용되었습니다.</AlertTitle><AlertDescription>
      <p>{usage.purpose} · {usage.model} · 입력/출력 토큰 과금 대상</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {tokens.input_tokens !== undefined && <Badge variant="secondary">입력 {tokens.input_tokens.toLocaleString()}</Badge>}
        {tokens.output_tokens !== undefined && <Badge variant="secondary">출력 {tokens.output_tokens.toLocaleString()}</Badge>}
        <Badge variant="outline">{tokens.total_tokens !== undefined ? `총 ${tokens.total_tokens.toLocaleString()} tokens` : '토큰 수 메타데이터 미제공'}</Badge>
      </div>
    </AlertDescription></Alert>;
  }
  if (usage?.requested && !usage?.used) {
    return <Alert variant="destructive"><AlertTriangle aria-hidden="true" /><AlertTitle>GCP AI 요청이 완료되지 않아 로컬 결과를 유지했습니다.</AlertTitle><AlertDescription>{usage.error || usage.status}</AlertDescription></Alert>;
  }
  return <Alert><Gauge aria-hidden="true" /><AlertTitle>현재 화면은 로컬 분석 결과입니다.</AlertTitle><AlertDescription>
    <p>IBT 파싱·랩 델타·코너·셋업 판정은 GCP 토큰을 사용하지 않습니다.</p>
    <Badge variant="secondary" className="mt-3">{aiEnabled ? 'GCP AI 사용 가능' : 'GCP AI 미연결'}</Badge>
  </AlertDescription></Alert>;
}

function RuleList({ title, rules = [], tone = 'emerald' }) {
  const toneClass = tone === 'rose'
    ? 'border-destructive/20 bg-destructive/15 text-destructive'
    : 'border-success/20 bg-success/15 text-success';
  return (
    <div>
      <h4 className="mb-2 text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">{title}</h4>
      <div className="flex flex-col gap-2">
        {rules.length ? rules.map((rule) => (
          <div key={`${rule.metric}-${rule.operator}-${rule.target}`} className={cx('rounded-xl border p-3 text-base', toneClass)}>
            <div className="font-mono text-sm font-semibold">
              {rule.metric} {rule.operator} {rule.target} {rule.unit}
            </div>
            <div className="mt-1 text-sm leading-relaxed text-foreground">{rule.description}</div>
          </div>
        )) : <div className="text-sm text-muted-foreground">해당 조건이 없습니다.</div>}
      </div>
    </div>
  );
}

function AIExperimentPlanner({
  selectedFile,
  sessionMode,
  pitRule,
  targetLap,
  referenceLap,
  userFeedback,
  feedbackPhase,
  feedbackSpeed,
  feedbackSymptom,
  setupMode,
  aiEnabled,
  externalReferenceFiles = [],
  onStored,
}) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setResult(null);
    setError('');
  }, [selectedFile, sessionMode, targetLap, referenceLap, externalReferenceFiles, userFeedback, feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode]);

  const generate = async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await requestJson('/api/ai/experiment-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: selectedFile || null,
          session_mode: sessionMode,
          pit_rule: sessionMode === 'ENDURANCE' ? pitRule : 'NOT_APPLICABLE',
          target_lap: targetLap ? Number(targetLap) : null,
          reference_lap: referenceLap ? Number(referenceLap) : null,
          user_feedback: userFeedback,
          feedback_phase: feedbackPhase || null,
          feedback_speed: feedbackSpeed || null,
          feedback_symptom: feedbackSymptom || null,
          setup_mode: setupMode,
          external_reference_files: externalReferenceFiles,
        }),
      });
      setResult(payload);
      if (payload.persisted && onStored) await onStored();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };

  const plan = result?.plan;
  const usage = result?.ai_usage;
  const setupChange = plan?.action?.setup_change;

  return (
    <div className="flex flex-col gap-5">
      <div className="border border-border bg-surface p-5">
        <div className="flex items-start gap-3">
          <Sparkles className="mt-0.5 h-6 w-6 shrink-0 text-foreground" />
          <div className="min-w-0">
            <h3 className="text-lg font-semibold text-foreground">GCP AI Setup Experiment Planner</h3>
            <p className="mt-2 max-w-5xl text-base leading-relaxed text-foreground">
              로컬 엔진이 만든 드라이빙·셋업 후보 중 다음 주행에서 검증할 항목 하나를 선택합니다.
              원본 60Hz 데이터와 Garage61 CSV 원문·드라이버 식별자는 전송하지 않고, 로컬에서 만든 익명 컨센서스 요약과 셋업 후보만 사용합니다. 실행할 때 GCP 입력·출력 토큰을 사용합니다.
            </p>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="default"
            type="button"
            onClick={generate}
            disabled={!aiEnabled || !selectedFile || loading}
            className="flex min-h-[50px] w-full items-center justify-center gap-2  coach-primary-action px-5 py-3 text-base   transition  disabled:cursor-not-allowed disabled: disabled: sm:w-auto"
          >
            {loading ? (
              <><span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-white" /> AI 계획 생성 중</>
            ) : (
              <><Sparkles className="h-4 w-4" /> {aiEnabled ? 'GCP AI 계획 생성 · 토큰 사용' : 'GCP AI 미연결'}</>
            )}
          </Button>
        </div>
      </div>

      {error && <ErrorMessage message={error} />}

      {plan && (
        <div className="flex flex-col gap-5 rounded-2xl border border-border bg-background/60 p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-foreground">GCP AI가 선택한 다음 실험</div>
              <h3 className="mt-1 text-xl font-semibold text-foreground">{plan.title}</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                <span className="rounded-lg border border-accent/30 bg-accent/10 px-2.5 py-1 text-sm font-semibold text-foreground">{plan.decision}</span>
                <span className="rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1 text-sm font-semibold text-primary">신뢰도 {Math.round((plan.confidence || 0) * 100)}%</span>
                <span className="rounded-lg border border-border bg-muted px-2.5 py-1 text-sm font-mono text-muted-foreground">{plan.model}</span>
                <span className="rounded-lg border border-success/30 bg-success/10 px-2.5 py-1 text-sm font-semibold text-success">
                  장기 기억 {plan.memory_context?.available_similar_sessions ?? 0}개 조회 · {plan.memory_context?.similar_sessions_used ?? 0}개 인용
                </span>
                <span className={cx(
                  'rounded-lg border px-2.5 py-1 text-sm font-semibold',
                  result?.persisted
                    ? 'border-success/30 bg-success/10 text-success'
                    : 'border-warning/30 bg-warning/10 text-warning'
                )}>
                  {result?.persisted ? 'AI 계획 SQLite 저장됨' : 'AI 계획 SQLite 미저장'}
                </span>
              </div>
            </div>
            <div className="text-right text-xs font-mono text-muted-foreground">Plan {plan.plan_id}</div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-2xl border border-border bg-background p-4">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">가설</div>
              <p className="mt-2 text-base leading-relaxed text-foreground">{plan.hypothesis}</p>
            </div>
            <div className="rounded-2xl border border-border bg-background p-4">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">선택 이유</div>
              <p className="mt-2 text-base leading-relaxed text-foreground">{plan.rationale}</p>
            </div>
          </div>

          <div className="rounded-2xl border border-primary/25 bg-primary/15 p-4">
            <div className="text-sm font-semibold uppercase tracking-wider text-primary">이번 주행에서 할 일</div>
            <p className="mt-2 text-base font-bold leading-relaxed text-foreground">{plan.action?.instruction}</p>
            {setupChange && (
              <div className="mt-3 flex flex-wrap items-baseline gap-2 rounded-xl border border-warning/25 bg-warning/20 p-3 font-mono">
                <span className="text-base text-muted-foreground">{setupChange.label}</span>
                <span className="text-muted-foreground line-through">{formatSetupOriginal(setupChange)}</span>
                <span className="text-muted-foreground">→</span>
                <span className="text-xl font-semibold text-warning">{formatSetupTarget(setupChange)}</span>
              </div>
            )}
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <RuleList title="성공 조건" rules={plan.success_rules} />
            <RuleList title="원복 조건" rules={plan.revert_rules} tone="rose" />
          </div>

          <div className="grid gap-4 lg:grid-cols-[0.7fr_1.3fr]">
            <div className="rounded-2xl border border-border bg-background p-4 text-base text-foreground">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">필요 표본</div>
              <div className="mt-3 flex flex-col gap-1 font-mono text-sm">
                <div>new runs: {plan.required_samples?.new_runs}</div>
                <div>out laps/run: {plan.required_samples?.out_laps_per_run}</div>
                <div>timed laps/run: {plan.required_samples?.timed_laps_per_run}</div>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{plan.required_samples?.note}</p>
            </div>
            <div className="rounded-2xl border border-border bg-background p-4">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">사용 근거</div>
              <div className="mt-3 flex flex-col gap-2">
                {(plan.evidence || []).map((item) => (
                  <div key={item.id} className="rounded-xl border border-border bg-muted/50 p-3 text-sm leading-relaxed text-foreground">
                    <span className="mr-2 font-mono font-semibold text-foreground">{item.id}</span>{item.summary}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <AIUsageBanner usage={usage} aiEnabled={aiEnabled} />
        </div>
      )}
    </div>
  );
}

function scrollToCoachSection(id) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent('coach-navigate', { detail: id }));
}

function workflowTone(status) {
  if (status === 'DONE' || status === 'ACTIVE') return 'border-success/30 bg-success/20 text-success';
  if (status === 'READY') return 'border-primary/30 bg-primary/20 text-primary';
  if (status === 'OPTIONAL') return 'border-accent/30 bg-accent/20 text-foreground';
  if (status === 'LIMITED') return 'border-warning/30 bg-warning/20 text-warning';
  return 'border-border bg-background/50 text-muted-foreground';
}

function ClosedLoopCoachPanel({ coach, currentFile, memory }) {
  if (!coach) return <EmptyState text="Closed-loop Coach 계산을 기다리고 있습니다." />;
  const workflow = coach.workflow || [];
  const focus = coach.top_focus;
  const nextRun = coach.next_run || {};
  const personalization = coach.personalization || {};
  const setup = coach.top_setup_candidate;
  const recovery = focus?.estimated_recovery_s;
  const changeTarget = setup
    ? (hasSetupNumericValue(setup.value)
      ? `${formatSetupNumber(setup.value)} ${setup.unit || ''}`.trim()
      : localizeSetupStep(setup.step_text, setup.direction))
    : null;

  return (
    <div className="flex flex-col gap-5">
      <details className="rounded-xl border border-border p-3">
        <summary data-help="quality" className="cursor-pointer text-sm font-bold text-muted-foreground">코칭 근거와 저장 상태</summary>
      <div className="grid gap-2 md:grid-cols-5">
        {workflow.map((item, index) => (
          <div key={item.key} className={cx('rounded-2xl border p-3', workflowTone(item.status))}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold uppercase tracking-[0.16em] opacity-70">STEP {item.step}</span>
              <span className="rounded-md border border-current/20 px-1.5 py-0.5 text-[11px] font-semibold">{item.status}</span>
            </div>
            <div className="mt-2 font-semibold text-foreground">{item.label}</div>
            <div className="mt-1 text-sm leading-snug opacity-80">{item.detail}</div>
            {index < workflow.length - 1 && <div className="mt-2 hidden text-center text-muted-foreground md:block">→</div>}
          </div>
        ))}
      </div>

      <div className={cx('rounded-xl border px-4 py-3 text-sm leading-relaxed', memory?.saved ? 'border-success/25 bg-success/10 text-success' : 'border-warning/25 bg-warning/10 text-warning')}>
        {memory?.saved
          ? '현재 IBT는 장기 기억 DB에 포함되어 있습니다. 이후 분석과 A/B 결과가 이 차량의 개인화 모델에 누적됩니다.'
          : '현재 IBT는 아직 개인화 DB에 포함되지 않았습니다. 상단에서 세션 유형을 확정하면 이 주행의 랩/코너 특성이 Driver Fingerprint 학습에 사용됩니다.'}
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-border bg-background/45 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">현재 자연 편차</div>
          <div className="mt-1 font-mono text-lg font-semibold text-foreground">±{Number(coach.natural_variability?.natural_variability_s || 0).toFixed(3)} s</div>
          <div className="mt-1 text-xs text-muted-foreground">표본 {coach.natural_variability?.valid_laps || 0}랩 · {coach.natural_variability?.confidence_label || 'NO_DATA'} 신뢰도</div>
        </div>
        <div className="rounded-xl border border-primary/20 bg-primary/10 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-primary">재현성 사용 방식</div>
          <div className="mt-1 text-sm font-bold text-foreground">분석 통과조건 아님</div>
          <div className="mt-1 text-xs text-muted-foreground">효과가 자연 편차보다 큰지 판단하는 보정값으로만 사용합니다.</div>
        </div>
        <div className="rounded-xl border border-accent/20 bg-accent/10 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-foreground">셋업 Assist</div>
          <div className="mt-1 text-sm font-bold text-foreground">낮은 재현성에서도 계속 사용</div>
          <div className="mt-1 text-xs text-muted-foreground">차를 더 쉽게 반복할 수 있게 만드는 셋업도 A/B 후보로 평가합니다.</div>
        </div>
      </div>

      </details>
      <div className="grid gap-4 xl:grid-cols-12">
        <div className="rounded-2xl border border-primary/30 bg-primary/15 p-5 xl:col-span-7">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs font-medium text-muted-foreground">다음 주행 목표</div>
              <h3 className="mt-2 text-xl font-semibold text-foreground">{nextRun.mode_label || '다음 런 준비'}</h3>
              <p className="mt-2 text-lg font-bold text-primary">{nextRun.title || '--'}</p>
            </div>
            <div className="rounded-xl border border-border bg-background/60 px-4 py-3 text-right">
              <div className="text-xs font-bold uppercase text-muted-foreground">최소 / 권장 유효랩</div>
              <div className="mt-1 font-mono text-2xl font-semibold text-foreground">
                {nextRun.required_valid_laps ?? '--'} / {nextRun.recommended_valid_laps ?? nextRun.required_valid_laps ?? '--'}
              </div>
            </div>
          </div>
          <p className="mt-4 text-base font-semibold leading-relaxed text-foreground">{nextRun.primary_instruction}</p>

          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="rounded-xl border border-border bg-background/50 p-4">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">이번 런에서 고정</div>
              <div className="mt-2 flex flex-col gap-2 text-sm text-foreground">
                {(nextRun.lock || []).map((item) => <div key={item}>• {item}</div>)}
              </div>
            </div>
            <div className="rounded-xl border border-border bg-background/50 p-4">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">성공 조건</div>
              <div className="mt-2 flex flex-col gap-2 text-sm text-foreground">
                {(nextRun.success_criteria || []).map((item) => <div key={item}>• {item}</div>)}
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-accent/20 bg-accent/15 p-3 text-sm leading-relaxed text-foreground">
            완료 후: {nextRun.after_run}
          </div>
          {currentFile && nextRun.mode === 'SETUP_AB' && (
            <div className="mt-3 text-sm text-muted-foreground">A/B의 A(Baseline)는 현재 파일 <b className="text-foreground">{currentFile}</b>을 사용하면 됩니다.</div>
          )}
        </div>

        <div className="flex flex-col gap-3 xl:col-span-5">
          <div className="rounded-2xl border border-border bg-background/55 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">가장 먼저 고칠 것</div>
            {focus ? (
              <>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <div className="text-lg font-semibold text-foreground">{focus.root_cause_label}</div>
                  <div className="font-mono font-semibold text-primary">{Math.round(Number(focus.priority_score || 0) * 100)}</div>
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  {(focus.affected_corners || [focus.corner]).filter(Boolean).join(' · ')}
                  {recovery?.length === 2 && ` · 예상 회수 ${Number(recovery[0]).toFixed(2)}~${Number(recovery[1]).toFixed(2)} s`}
                </div>
                <p className="mt-3 text-sm leading-relaxed text-foreground">{focus.action}</p>
                {focus.root_cause === 'DRIVER_VARIANCE' && (
                  <div className="mt-3 rounded-lg border border-primary/20 bg-primary/10 px-3 py-2 text-xs leading-relaxed text-primary">
                    이 항목은 “실력을 먼저 올려야 한다”는 판정이 아닙니다. 현재 자연 편차를 추정하는 낮은 우선도의 관찰 항목이며 셋업 Assist를 차단하지 않습니다.
                  </div>
                )}
              </>
            ) : <div className="mt-2 text-sm text-muted-foreground">명확한 단일 드라이빙 병목이 없습니다.</div>}
          </div>

          <div className="rounded-2xl border border-border bg-background/55 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">셋업 A/B 후보</div>
            {setup ? (
              <>
                <div className="mt-2 font-semibold text-foreground">{setup.label}</div>
                <div className="mt-1 font-mono text-warning">
                  {formatSetupOriginal(setup)} → {changeTarget}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">근거 {Math.round(Number(setup.confidence || 0) * 100)}% · 우선도 {Math.round(Number(setup.priority_score || 0) * 100)}%</div>
              </>
            ) : <div className="mt-2 text-sm text-muted-foreground">현재 검증 가능한 셋업 후보가 없습니다.</div>}
          </div>

          <div className="rounded-2xl border border-success/20 bg-success/10 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-success/80">개인화 학습</div>
            <div className="mt-2 flex items-end justify-between gap-3">
              <div className="text-lg font-semibold text-foreground">{personalization.learning_stage || 'COLD_START'}</div>
              <div className="font-mono text-xl font-semibold text-success">{Math.round(Number(personalization.learning_progress || 0) * 100)}%</div>
            </div>
            <div className="mt-2 text-sm text-muted-foreground">A/B 근거 {personalization.decisive_sensitivity_count ?? 0}건 · Fingerprint {personalization.fingerprint_confidence_label || 'LOW'}</div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('priorities')} className=" border   px-4 py-2 text-sm   ">원인 상세</Button>
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('setup-decision')} className=" border   px-4 py-2 text-sm   ">셋업 후보</Button>
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('ab-manager')} className=" border   px-4 py-2 text-sm   ">A/B 비교</Button>
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('personalization')} className=" border   px-4 py-2 text-sm   ">개인화 결과</Button>
      </div>
    </div>
  );
}

function PersonalizationPanel({ memory }) {
  const fingerprint = memory?.driver_fingerprint || {};
  const sensitivity = memory?.setup_sensitivity || {};
  const settings = sensitivity.settings || [];
  const weaknesses = fingerprint.weaknesses || [];
  const strengths = fingerprint.strengths || [];
  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm leading-relaxed text-muted-foreground">저장한 주행에서 반복되는 특성을 찾습니다. 셋업은 한 항목씩 변경하고 A/B 결과를 저장하면 변경 효과도 함께 학습합니다.</p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricMini label="개인 세션" value={`${fingerprint.session_count || 0}개`} />
        <MetricMini label="학습 트랙" value={`${fingerprint.track_count || 0}개`} />
        <MetricMini label="결정형 A/B" value={`${sensitivity.decisive_event_count || 0}건`} />
        <MetricMini label="주행 특성 신뢰도" value={`${Math.round(Number(fingerprint.confidence || 0) * 100)}%`} />
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="coach-learning-list">
          <h3 className="font-semibold text-foreground">반복되는 개선 영역</h3>
          <div className="mt-3 flex flex-col gap-2">
            {weaknesses.length ? weaknesses.slice(0, 5).map((item) => (
              <div key={`${item.issue_type}-${item.speed_class}`} className="rounded-xl border border-border bg-muted/50 p-3">
                <div className="flex justify-between gap-3 text-sm">
                  <span className="font-bold text-foreground">{SPEED_LABELS[item.speed_class] || item.speed_class} · {item.issue_label}</span>
                  <span className="font-mono text-destructive">{Math.round(Number(item.session_support_ratio || 0) * 100)}%</span>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{item.session_support} 세션 · 평균 손실 {formatSigned(item.mean_loss_s)}</div>
              </div>
            )) : <div className="text-sm text-muted-foreground">같은 차량의 반복 데이터가 더 필요합니다.</div>}
          </div>
        </div>

        <div className="coach-learning-list">
          <h3 className="font-semibold text-foreground">상대적 강점 / 재현성</h3>
          <div className="mt-3 flex flex-col gap-2">
            {strengths.length ? strengths.slice(0, 5).map((item, index) => (
              <div key={`${item.type}-${item.speed_class || index}`} className="rounded-xl border border-border bg-muted/50 p-3">
                <div className="flex justify-between gap-3 text-sm">
                  <span className="font-bold text-foreground">{item.label}</span>
                  <span className="font-mono text-success">{Math.round(Number(item.score || 0) * 100)}</span>
                </div>
                {item.median_stdev_s !== undefined && item.median_stdev_s !== null && <div className="mt-1 text-xs text-muted-foreground">중앙 랩 편차 σ {Number(item.median_stdev_s).toFixed(3)} s</div>}
              </div>
            )) : <div className="text-sm text-muted-foreground">강점을 판정할 표본이 아직 부족합니다.</div>}
          </div>
        </div>
      </div>

      <div className="coach-learning-list">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold text-foreground">셋업 민감도 학습</h3>
          <Badge variant="secondary">{{ COLD_START: '표본 수집 중', LEARNING: '학습 중', MATURE: '학습 누적' }[sensitivity.learning_stage] || sensitivity.learning_stage || '표본 수집 중'}</Badge>
        </div>
        {settings.length ? (
          <div className="mt-3 overflow-x-auto">
            <Table className="w-full min-w-[760px] text-left text-sm">
              <TableHeader className="text-xs uppercase tracking-wider text-muted-foreground">
                <TableRow><TableHead className="py-2">항목</TableHead><TableHead>방향</TableHead><TableHead>결정 표본</TableHead><TableHead>KEEP</TableHead><TableHead>REVERT</TableHead><TableHead>평균 효과</TableHead><TableHead>단계</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {settings.slice(0, 8).map((item) => (
                  <TableRow key={`${item.setting}-${item.direction}`} className="border-t border-border">
                    <TableCell className="py-3 font-bold text-foreground">{settingName(item)}</TableCell>
                    <TableCell className="text-foreground">{item.direction_label}</TableCell>
                    <TableCell className="font-mono text-foreground">{item.decisive_count}</TableCell>
                    <TableCell className="font-mono text-success">{item.keep_count}</TableCell>
                    <TableCell className="font-mono text-destructive">{item.revert_count}</TableCell>
                    <TableCell className={cx('font-mono', Number(item.weighted_median_delta_s || 0) < 0 ? 'text-success' : 'text-foreground')}>{item.weighted_median_delta_s === null || item.weighted_median_delta_s === undefined ? '--' : formatSigned(item.weighted_median_delta_s)}</TableCell>
                    <TableCell className="text-muted-foreground">{item.tier}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : <p className="mt-3 text-sm leading-relaxed text-muted-foreground">아직 저장된 단일 변경 A/B 결과가 없습니다. 셋업 후보 하나만 바꾸고 A/B 비교를 저장하면 자동으로 학습됩니다.</p>}
      </div>
    </div>
  );
}

function LearningEffectSummary({ result, memoryRecord }) {
  const effects = result?.effect_profile?.overall_corner_deltas || {};
  const learning = memoryRecord?.learning_update;
  if (!result) return null;
  return (
    <div className="mt-4 flex flex-col gap-3">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <MetricMini label="최저속 변화" value={formatSigned(effects.min_speed_kph, 2, ' km/h')} />
        <MetricMini label="탈출속도 변화" value={formatSigned(effects.exit_speed_kph, 2, ' km/h')} />
        <MetricMini label="ABS 변화" value={formatSigned(effects.abs_duty_pct, 2, '%p')} />
        <MetricMini label="코스팅 변화" value={formatSigned(effects.coast_time_s, 3, ' s')} />
        <MetricMini label="풀스로틀 위치 (−=앞당김)" value={formatSigned(effects.full_throttle_shift_m, 1, ' m')} />
      </div>
      {learning && (
        <div className={cx('rounded-xl border p-3 text-sm leading-relaxed', learning.stored_events > 0 ? 'border-success/25 bg-success/15 text-success' : 'border-border bg-background/40 text-muted-foreground')}>
          {learning.stored_events > 0
            ? `개인화 학습 ${learning.stored_events}건 갱신됨${learning.confounded ? ' · 여러 셋업 항목이 함께 바뀌어 낮은 가중치로 저장' : ' · 단일 변경 A/B로 강한 학습 근거 저장'}`
            : (learning.reason || '셋업 민감도 학습 항목 없음')}
        </div>
      )}
    </div>
  );
}

function ABAxisCard({ label, axis, detail }) {
  const status = axis?.status || 'UNAVAILABLE';
  const tone = status === 'IMPROVED'
    ? 'border-success/25 bg-success/15 text-success'
    : status === 'WORSENED'
    ? 'border-destructive/25 bg-destructive/15 text-destructive'
    : status === 'NEUTRAL'
    ? 'border-border bg-background/45 text-foreground'
    : 'border-border bg-background/35 text-muted-foreground';
  return (
    <div className={cx('rounded-xl border p-3', tone)}>
      <div className="text-xs font-semibold uppercase tracking-wider opacity-70">{label}</div>
      <div className="mt-1 text-lg font-semibold">{status}</div>
      {detail && <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{detail}</div>}
    </div>
  );
}

function ExperimentManager({ files = [], sessionMode, preferredBaseline = '', onStored, trial, goal = 'COMFORT', onComparison }) {
  const [baseline, setBaseline] = useState('');
  const [candidate, setCandidate] = useState('');
  const [result, setResult] = useState(null);
  const [memoryRecord, setMemoryRecord] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const preferredBaselineRef = useRef('');
  const requestRef = useRef({ id: 0, controller: null });
  const contextKey = JSON.stringify([baseline, candidate, trial?.session_mode || sessionMode, trial?.id || null, trial?.driver_feedback || null, trial?.goal || goal]);
  const contextRef = useRef(contextKey);
  contextRef.current = contextKey;

  useEffect(() => {
    if (!trial) return;
    setBaseline(trial.baseline_file);
    setCandidate(trial.candidate_file || '');
    setResult(trial.comparison || null);
  }, [trial?.id, trial?.baseline_file, trial?.candidate_file, trial?.comparison, trial]);

  useEffect(() => {
    requestRef.current.controller?.abort();
    setLoading(false);
    return () => requestRef.current.controller?.abort();
  }, [contextKey]);

  useEffect(() => {
    if (trial || !preferredBaseline || preferredBaselineRef.current === preferredBaseline) return;
    if (files.some((file) => file.name === preferredBaseline)) {
      preferredBaselineRef.current = preferredBaseline;
      setBaseline(preferredBaseline);
      setCandidate('');
      setResult(null);
      setMemoryRecord(null);
      setError('');
    }
  }, [files, preferredBaseline, trial]);

  const baselineFile = useMemo(
    () => files.find((file) => file.name === baseline) || (trial?.baseline_file === baseline ? trial.context : null),
    [baseline, files, trial]
  );

  const candidateFiles = useMemo(() => {
    if (!baselineFile) return [];
    return files.filter(
      (file) => file.analyzable !== false && file.name !== baselineFile.name && sameVehicleAndTrack(file, baselineFile)
    );
  }, [baselineFile, files]);

  useEffect(() => {
    if (trial) return;
    if (!files.length) {
      setBaseline('');
      setCandidate('');
      return;
    }
    if (!files.some((file) => file.name === baseline)) {
      const preferred = files.some((file) => file.name === preferredBaseline)
        ? preferredBaseline
        : files.find((file) => file.analyzable !== false)?.name || '';
      setBaseline(preferred);
    }
  }, [baseline, files, preferredBaseline, trial]);

  useEffect(() => {
    if (!trial && !candidateFiles.some((file) => file.name === candidate)) {
      setCandidate('');
    }
  }, [candidate, candidateFiles, trial]);

  const resetComparison = () => {
    setResult(null);
    setMemoryRecord(null);
    setError('');
  };

  const handleBaselineChange = (filename) => {
    resetComparison();
    setBaseline(filename);
  };

  const handleCandidateChange = (filename) => {
    resetComparison();
    setCandidate(filename);
  };

  const compare = async () => {
    requestRef.current.controller?.abort();
    const requestId = requestRef.current.id + 1;
    const controller = new AbortController();
    requestRef.current = { id: requestId, controller };
    const requestedContext = contextKey;
    const isCurrent = () => !controller.signal.aborted && requestRef.current.id === requestId && contextRef.current === requestedContext;
    setError('');
    setLoading(true);
    try {
      const payload = await requestJson('/api/experiments/compare', {
        signal: controller.signal,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          baseline_file: baseline,
          candidate_file: candidate,
          session_mode: trial?.session_mode || sessionMode,
          goal: trial?.goal || goal,
          driver_feedback: ['IMPROVED', 'SAME', 'WORSENED'].includes(trial?.driver_feedback?.change) ? trial.driver_feedback : null,
          trial_id: trial?.id || null,
        }),
      });
      if (!isCurrent()) return;
      setResult(payload.comparison);
      if (onComparison && trial) onComparison(payload.comparison);
      setMemoryRecord(payload.memory_record || null);
      if (payload.memory_record?.saved && onStored) await onStored(payload.memory_record || null);
    } catch (requestError) {
      if (isCurrent() && requestError.name !== 'AbortError') setError(requestError.message);
    } finally {
      if (isCurrent()) setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-primary/20 bg-primary/10 p-3 text-sm leading-relaxed text-muted-foreground">
        두 IBT를 PACE · CONSISTENCY · DRIVEABILITY로 나눠 비교합니다. 재현성은 실패 조건이 아니라 Baseline의 자연 편차로 계산되어 변화가 실제 효과인지 판단하는 기준이 됩니다. 일반 A/B는 최소 3랩, 권장 5랩이며 같은 차량·트랙·레이아웃만 비교합니다.
        {sessionMode === 'QUALIFYING' && ' 퀄리파잉 모드는 기존 규칙상 각 셋업 유효 계측랩 2개를 사용합니다.'}
      </div>

      {baselineFile && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-background/55 px-4 py-3 text-sm">
          <span className="font-bold text-foreground">{comparisonContextLabel(baselineFile)}</span>
          <span className="font-mono text-primary">
            비교 가능한 다른 IBT {candidateFiles.length}개
          </span>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
        <FileSelect
          label="A · 변경 전 주행"
          value={baseline}
          onChange={handleBaselineChange}
          files={files}
          disabled={Boolean(trial)}
        />
        <FileSelect
          label="B · 변경 후 주행 (동일 차량/트랙)"
          value={candidate}
          onChange={handleCandidateChange}
          files={candidateFiles}
          disabled={Boolean(trial)}
        />
        <Button variant="default"
          type="button"
          onClick={compare}
          data-help="abResult"
          disabled={loading || !baseline || !candidate || baseline === candidate || baselineFile?.analyzable === false}
          className="self-end  coach-primary-action px-5 py-3 text-base   transition  disabled:cursor-not-allowed disabled: disabled:"
        >
          {loading ? '비교 중' : 'A/B 결과 확인'}
        </Button>
      </div>

      {baselineFile && candidateFiles.length === 0 && (
        <div className="rounded-xl border border-warning/30 bg-warning/20 p-4 text-base text-warning">
          선택한 기준 파일과 같은 차량·트랙·레이아웃의 다른 IBT가 없습니다.
        </div>
      )}
      {error && <ErrorMessage message={error} />}
      {result && (
        <div className="rounded-2xl border border-border bg-background/60 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Verdict</div>
              <div
                className={cx(
                  'mt-1 text-2xl font-semibold',
                  result.verdict === 'KEEP'
                    ? 'text-success'
                    : result.verdict === 'REVERT'
                    ? 'text-destructive'
                    : 'text-warning'
                )}
              >
                {result.verdict}
              </div>
              <div className="mt-1 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Objective: {result.objective}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <MetricMini label="Median delta" value={formatSigned(result.median_delta_s)} />
              <MetricMini label="Best delta" value={formatSigned(result.best_delta_s)} />
              <MetricMini label="Stdev delta" value={formatSigned(result.stdev_delta_s)} />
              <MetricMini
                label="Pace slope delta"
                value={formatSigned(result.pace_slope_delta_s_per_lap, 4, ' s/lap')}
              />
            </div>
          </div>
          <p className="mt-4 text-base leading-relaxed text-foreground">{result.reason}</p>
          {result.subjective_feedback && <p className="mt-3 rounded-xl border border-accent/25 p-3 text-sm text-foreground">체감 기록: {({ IMPROVED: '개선', SAME: '동일', WORSENED: '악화' })[result.subjective_feedback.change] || '미입력'} · 운전 편의 {result.subjective_feedback.rating || '--'}/5 · 속도 효과와 별도 평가</p>}
          {result.conditions_quality?.reasons?.length > 0 && <p className="mt-3 text-sm text-warning">비교 조건: {result.conditions_quality.reasons.join(' · ')}</p>}
          {result.learning_eligibility && <p className="mt-2 text-xs text-muted-foreground">학습 상태: {result.learning_eligibility.eligible ? '사용 가능' : '보류'} · {(result.learning_eligibility.reasons || []).join(' · ')}</p>}
          <div className="mt-4 rounded-xl border border-accent/25 bg-accent/10 p-4">
            <h4 className="text-sm font-semibold text-foreground">실제로 바뀐 셋업</h4>
            {(result.setup_changes || []).length > 0 ? <div className="mt-3 flex flex-col gap-2">{result.setup_changes.map((change) => <div key={change.setting} className="flex flex-wrap justify-between gap-2 text-sm"><span className="font-bold text-foreground">{change.label || change.setting}</span><span className="font-mono text-warning">{formatNumber(change.baseline_value, 2)} → {formatNumber(change.candidate_value, 2)} {change.unit}</span></div>)}</div> : <p className="mt-2 text-sm text-muted-foreground">{result.setup_data_available === false ? '셋업 원본이 없어 변경점을 확인할 수 없습니다.' : '수치로 비교 가능한 셋업 변경이 없습니다.'}</p>}
            {(result.setup_changes || []).length > 1 && <p className="mt-3 text-sm text-warning">여러 항목이 바뀌어 어느 변경의 효과인지 분리하기 어렵습니다. 다음에는 한 항목만 변경하세요.</p>}
            {Object.keys(result.conditions?.differences || {}).length > 0 && <details className="mt-3 text-sm text-muted-foreground"><summary data-help="quality" className="cursor-pointer font-bold">서로 다른 주행 조건</summary><div className="mt-2 flex flex-col gap-1">{Object.entries(result.conditions.differences).map(([key, values]) => <p key={key}>{({ track_temp_c: '노면 온도', air_temp_c: '기온', fuel_level_l: '연료량', weather_type: '날씨', track_wetness: '노면 젖음', session_type: '세션 유형' })[key] || key}: {String(values.baseline ?? '측정 불가')} → {String(values.candidate ?? '측정 불가')}</p>)}</div></details>}
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <ABAxisCard
              label="PACE"
              axis={result.performance_axes?.pace}
              detail={`중앙값 ${formatSigned(result.median_delta_s)} · 효과 기준 ${Number(result.ab_quality?.effect_threshold_s || 0).toFixed(3)} s`}
            />
            <ABAxisCard
              label="CONSISTENCY"
              axis={result.performance_axes?.consistency}
              detail={`자연 편차 ${Number(result.ab_quality?.natural_variability_s || 0).toFixed(3)} s · robust Δ ${formatSigned(result.robust_variability_delta_s)}`}
            />
            <ABAxisCard
              label="DRIVEABILITY"
              axis={result.performance_axes?.driveability}
              detail={`코너 지표 ${result.performance_axes?.driveability?.evidence_count ?? 0}개 · score ${result.performance_axes?.driveability?.score ?? '--'}`}
            />
            {result.performance_axes?.stability && <ABAxisCard label="완주와 안정성" axis={result.performance_axes.stability} detail={`성공률 A ${formatNumber(result.performance_axes.stability.baseline_success_rate == null ? null : Number(result.performance_axes.stability.baseline_success_rate) * 100, 0, '%')} → B ${formatNumber(result.performance_axes.stability.candidate_success_rate == null ? null : Number(result.performance_axes.stability.candidate_success_rate) * 100, 0, '%')} · 시도 A ${result.performance_axes.stability.baseline_attempted_laps ?? '--'} / B ${result.performance_axes.stability.candidate_attempted_laps ?? '--'}`} />}
          </div>
          <div className="mt-3 rounded-xl border border-primary/20 bg-primary/10 px-3 py-2 text-sm text-foreground">
            A/B 표본 신뢰도 <b className="text-primary">{result.ab_quality?.sample_confidence || '--'}</b> · 최소 {result.minimum_valid_laps_per_file ?? '--'}랩 / 권장 {result.recommended_valid_laps_per_file ?? '--'}랩 · 효과/자연편차 {result.ab_quality?.effect_vs_noise_ratio ?? '--'}×
          </div>
          {result.lap_distribution && (
            <details className="mt-4 rounded-xl border border-border p-3" open>
              <summary className="cursor-pointer text-sm font-semibold text-foreground">랩타임 분포 A/B</summary>
              <div className="mt-3"><LapDistribution distribution={result.lap_distribution} /></div>
            </details>
          )}
          {(result.gg?.baseline || result.gg?.candidate) && (
            <details className="mt-4 rounded-xl border border-border p-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">G-G 그림 A/B</summary>
              <div className="mt-3"><GGDiagram baseline={result.gg.baseline} candidate={result.gg.candidate} /></div>
            </details>
          )}
          {(result.damper_velocity?.baseline || result.damper_velocity?.candidate) && (
            <details className="mt-4 rounded-xl border border-border p-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">댐퍼 속도 분포 A/B</summary>
              <div className="mt-3"><DamperHistogram baseline={result.damper_velocity.baseline} candidate={result.damper_velocity.candidate} /></div>
            </details>
          )}
          <LearningEffectSummary result={result} memoryRecord={memoryRecord} />
          {memoryRecord && (
            <div className={cx(
              'mt-4 rounded-xl border p-3 text-sm',
              memoryRecord.saved
                ? 'border-success/25 bg-success/15 text-success'
                : 'border-warning/25 bg-warning/15 text-warning'
            )}>
              {memoryRecord.saved
                ? `A/B 판정과 실제 셋업 차이가 장기 기억 DB에 저장되었습니다.${memoryRecord.linked_plan_id ? ` AI 계획 ${memoryRecord.linked_plan_id}과 연결되었습니다.` : ''}`
                : `A/B 결과 DB 저장 실패: ${memoryRecord.reason || '원인 미상'}`}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FileSelect({ label, value, onChange, files, disabled = false, hideLabel = false }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef(null);
  const selectedOptionRef = useRef(null);
  const selectedFile = files.find((file) => file.name === value);
  const selectedLapCount = Number(selectedFile?.lap_count || 0);

  useEffect(() => {
    const handlePointerDown = (event) => {
      if (!containerRef.current?.contains(event.target)) setOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const frame = window.requestAnimationFrame(() => {
      selectedOptionRef.current?.scrollIntoView({ block: 'nearest' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  const chooseFile = (filename) => {
    setOpen(false);
    onChange(filename);
  };

  return (
    <div ref={containerRef} className={cx('relative block min-w-0', open && 'z-[120]')}>
      <span hidden={hideLabel} className={hideLabel ? 'coach-file-label' : 'mb-2 block text-xs font-semibold uppercase tracking-wider text-muted-foreground'}>
        {label} <HelpHint topic={label} />
      </span>
      <Button variant="outline"
        type="button"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="listbox"
        data-help={label}
        aria-expanded={open}
        className={cx(
          'flex min-h-[50px] w-full items-center gap-3  border  px-4 py-3 text-base outline-none transition',
          open
            ? ' ring-2 ring-cyan-500/15'
            : ' '
        )}
      >
        {value && (
          <span
            title={`DB 분류: ${sessionModeLabel(assignedModeForFile(selectedFile))}`}
            className={cx(
              'shrink-0 rounded-lg border px-2.5 py-1 text-sm font-semibold',
              lapBadgeClass(selectedFile, false)
            )}
          >
            {selectedFile?.retryable ? '읽기 대기' : selectedFile?.metadata_pending ? '확인 중' : selectedFile?.status === 'COACHING_ONLY' ? '부분 코너' : `유효 ${selectedFile?.valid_laps ?? '--'} / ${selectedLapCount}랩`}
          </span>
        )}
        <span className="min-w-0 flex-1 truncate text-left text-foreground">
          {selectedFile?.car_name && selectedFile?.track_name ? <><span className="block truncate text-sm font-bold">{selectedFile.car_name} · {selectedFile.track_name}</span><span className="mt-1 block truncate text-xs text-muted-foreground">{value}</span></> : value || '파일 선택'}
        </span>
        <ChevronDown
          className={cx(
            'h-5 w-5 shrink-0 text-muted-foreground transition-transform',
            open && 'rotate-180 text-primary'
          )}
        />
      </Button>

      {open && (
        <div className="absolute left-0 top-full z-[130] mt-2 w-full overflow-hidden rounded-xl border border-border bg-background shadow-2xl shadow-black/60">
          <div className="border-b border-border p-2"><Input aria-label={`${label} 검색`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="파일·차량·트랙 검색" className="w-full  border   px-3 py-2 text-sm  outline-none " /></div>
          <div role="listbox" aria-label={label} className="max-h-[420px] overflow-y-auto overscroll-contain py-1">
            {!files.length && (
              <div className="px-4 py-4 text-sm text-muted-foreground">표시할 파일이 없습니다.</div>
            )}
            {files.filter((file) => [file.name, file.car_name, file.track_name, file.track_config].join(' ').toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).map((file) => {
              const isSelected = file.name === value;
              const lapCount = Number(file.lap_count || 0);
              return (
                <Button variant="surface"
                  key={file.name}
                  ref={isSelected ? selectedOptionRef : undefined}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  disabled={file.analyzable === false}
                  aria-disabled={file.analyzable === false}
                  onClick={() => chooseFile(file.name)}
                  className={cx(
                    'flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-55 sm:text-base',
                    isSelected
                      ? '  ring-1 ring-inset ring-cyan-500/55'
                      : ' '
                  )}
                >
                  <span
                    title={`DB 분류: ${sessionModeLabel(assignedModeForFile(file))}`}
                    className={cx(
                      'shrink-0 rounded-lg border px-2.5 py-1 text-sm font-semibold',
                      lapBadgeClass(file, isSelected)
                    )}
                  >
                    {file.retryable ? '읽기 대기' : file.metadata_pending ? '확인 중' : file.status === 'COACHING_ONLY' ? '부분 코너' : `유효 ${file.valid_laps ?? '--'} / ${lapCount}랩`}
                  </span>
                  <span className="min-w-0 flex-1 font-medium">
                    {file.car_name && file.track_name && <span className="mb-1 block truncate text-sm font-bold">{file.car_name} · {file.track_name}</span>}
                    <span className="block truncate">{file.name}</span>
                    {(file.eligibility_reason || file.reason) && <span className={cx('mt-1 block text-xs font-normal leading-relaxed', file.comparison_ready === false ? 'text-warning' : 'text-muted-foreground')}>{file.eligibility_reason || file.reason}</span>}
                  </span>
                  {isSelected && <CheckCircle2 className="h-5 w-5 shrink-0" />}
                </Button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}


function buildRacingLineGeometry(target, reference, connector, options = {}) {
  const width = options.width || 560;
  const height = options.height || 300;
  const pad = options.pad || 28;
  const mode = options.mode || 'emphasis';
  const emphasisFactor = Number(options.emphasisFactor || 3);

  const pairCount = Math.min(target.length, reference.length);
  const paired = [];
  for (let index = 0; index < pairCount; index += 1) {
    const t = target[index];
    const r = reference[index];
    const tx = Number(t?.x_m);
    const ty = Number(t?.y_m);
    const rx = Number(r?.x_m);
    const ry = Number(r?.y_m);
    if (![tx, ty, rx, ry].every(Number.isFinite)) continue;
    paired.push({
      target: { x_m: tx, y_m: ty, speed_kph: Number(t?.speed_kph), distance_pct: Number(t?.distance_pct) },
      reference: { x_m: rx, y_m: ry, speed_kph: Number(r?.speed_kph), distance_pct: Number(r?.distance_pct) },
      distance_pct: Number.isFinite(Number(t?.distance_pct))
        ? Number(t.distance_pct)
        : (Number.isFinite(Number(r?.distance_pct)) ? Number(r.distance_pct) : index / Math.max(1, pairCount - 1)),
    });
  }

  if (paired.length < 4) return null;

  let working = paired;
  let focusSummary = '코너 전체';
  if (mode === 'zoom') {
    const centerPct = Number(connector?.distance_pct);
    let centerIndex = Math.floor(paired.length / 2);
    if (Number.isFinite(centerPct)) {
      let bestDelta = Number.POSITIVE_INFINITY;
      paired.forEach((pair, idx) => {
        const delta = Math.abs(Number(pair.distance_pct) - centerPct);
        if (delta < bestDelta) {
          bestDelta = delta;
          centerIndex = idx;
        }
      });
    }
    const halfWindow = Math.max(4, Math.floor(paired.length * 0.20));
    const start = Math.max(0, centerIndex - halfWindow);
    const end = Math.min(paired.length, centerIndex + halfWindow + 1);
    working = paired.slice(start, end);
    focusSummary = '최대 차이 구간 확대';
  }

  const transformed = working.map((pair) => {
    let tx = pair.target.x_m;
    let ty = pair.target.y_m;
    let rx = pair.reference.x_m;
    let ry = pair.reference.y_m;
    if (mode === 'emphasis') {
      const mx = (tx + rx) / 2;
      const my = (ty + ry) / 2;
      tx = mx + (tx - mx) * emphasisFactor;
      ty = my + (ty - my) * emphasisFactor;
      rx = mx + (rx - mx) * emphasisFactor;
      ry = my + (ry - my) * emphasisFactor;
    }
    return {
      ...pair,
      targetView: { ...pair.target, x_m: tx, y_m: ty },
      referenceView: { ...pair.reference, x_m: rx, y_m: ry },
    };
  });

  const allPoints = transformed.flatMap((pair) => [pair.targetView, pair.referenceView]);
  const xs = allPoints.map((point) => point.x_m);
  const ys = allPoints.map((point) => point.y_m);
  let minX = Math.min(...xs);
  let maxX = Math.max(...xs);
  let minY = Math.min(...ys);
  let maxY = Math.max(...ys);
  if (Math.abs(maxX - minX) < 1) { minX -= 0.5; maxX += 0.5; }
  if (Math.abs(maxY - minY) < 1) { minY -= 0.5; maxY += 0.5; }
  const dataW = maxX - minX;
  const dataH = maxY - minY;
  const scale = Math.min((width - pad * 2) / dataW, (height - pad * 2) / dataH);
  const drawnW = dataW * scale;
  const drawnH = dataH * scale;
  const offsetX = (width - drawnW) / 2;
  const offsetY = (height - drawnH) / 2;
  const project = (point) => ({
    x: offsetX + (Number(point.x_m) - minX) * scale,
    y: height - (offsetY + (Number(point.y_m) - minY) * scale),
  });

  const projectedPairs = transformed.map((pair) => ({
    ...pair,
    targetProj: project(pair.targetView),
    referenceProj: project(pair.referenceView),
  }));

  const lineString = (key) => projectedPairs.map((pair) => {
    const p = pair[key];
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(' ');

  const fillPolygon = [
    ...projectedPairs.map((pair) => `${pair.targetProj.x.toFixed(1)},${pair.targetProj.y.toFixed(1)}`),
    ...projectedPairs.slice().reverse().map((pair) => `${pair.referenceProj.x.toFixed(1)},${pair.referenceProj.y.toFixed(1)}`),
  ].join(' ');

  let focusIndex = Math.floor(projectedPairs.length / 2);
  const connectorPct = Number(connector?.distance_pct);
  if (Number.isFinite(connectorPct)) {
    let bestDelta = Number.POSITIVE_INFINITY;
    projectedPairs.forEach((pair, idx) => {
      const delta = Math.abs(Number(pair.distance_pct) - connectorPct);
      if (delta < bestDelta) {
        bestDelta = delta;
        focusIndex = idx;
      }
    });
  }

  const focusPair = projectedPairs[Math.max(0, Math.min(projectedPairs.length - 1, focusIndex))];
  const focusTarget = focusPair?.targetProj || null;
  const focusReference = focusPair?.referenceProj || null;
  const focusMid = focusTarget && focusReference
    ? { x: (focusTarget.x + focusReference.x) / 2, y: (focusTarget.y + focusReference.y) / 2 }
    : null;

  return {
    width,
    height,
    mode,
    focusSummary,
    emphasisFactor,
    targetLine: lineString('targetProj'),
    referenceLine: lineString('referenceProj'),
    fillPolygon,
    focusTarget,
    focusReference,
    focusMid,
    focusGapLabel: formatNumber(connector?.gap_m, 2, 'm'),
    start: projectedPairs[0]?.targetProj || null,
    end: projectedPairs[projectedPairs.length - 1]?.targetProj || null,
    pairedCount: projectedPairs.length,
  };
}

function RacingLineSvg({ geometry, labelPrefix }) {
  if (!geometry) return null;
  return (
    <svg viewBox={`0 0 ${geometry.width} ${geometry.height}`} className="h-auto w-full" role="img" aria-label={`${labelPrefix} 내 주행라인과 Garage61 기준라인 비교`}>
      <polygon points={geometry.fillPolygon} fill="#f59e0b" opacity="0.12" />
      <polyline points={geometry.referenceLine} fill="none" stroke={COMPARISON_COLORS.reference} strokeWidth="5" strokeDasharray="9 6" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
      <polyline points={geometry.targetLine} fill="none" stroke={COMPARISON_COLORS.target} strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" opacity="0.96" />
      {geometry.focusTarget && geometry.focusReference && (
        <>
          <line x1={geometry.focusTarget.x} y1={geometry.focusTarget.y} x2={geometry.focusReference.x} y2={geometry.focusReference.y} stroke="#fbbf24" strokeWidth="2.5" strokeDasharray="7 6" />
          <circle cx={geometry.focusTarget.x} cy={geometry.focusTarget.y} r="5" fill={COMPARISON_COLORS.target} stroke={COMPARISON_COLORS.canvas} strokeWidth="2" />
          <circle cx={geometry.focusReference.x} cy={geometry.focusReference.y} r="5" fill={COMPARISON_COLORS.reference} stroke={COMPARISON_COLORS.canvas} strokeWidth="2" />
          {geometry.focusMid && (
            <text x={geometry.focusMid.x} y={geometry.focusMid.y - 10} textAnchor="middle" fill="#fde68a" fontSize="15" fontWeight="800">
              {geometry.focusGapLabel}
            </text>
          )}
        </>
      )}
      {geometry.start && (
        <>
          <circle cx={geometry.start.x} cy={geometry.start.y} r="4" fill="#e2e8f0" />
          <text x={geometry.start.x + 8} y={geometry.start.y - 8} fill="#bbbbbb" fontSize="13" fontWeight="700">IN</text>
        </>
      )}
      {geometry.end && (
        <>
          <circle cx={geometry.end.x} cy={geometry.end.y} r="4" fill="#e2e8f0" />
          <text x={geometry.end.x + 8} y={geometry.end.y - 8} fill="#bbbbbb" fontSize="13" fontWeight="700">OUT</text>
        </>
      )}
    </svg>
  );
}

function RacingLineMiniMap({ corner }) {
  const [viewMode, setViewMode] = useState('emphasis');
  const target = useMemo(
    () => (corner?.target_points || []).filter((p) => Number.isFinite(Number(p?.x_m)) && Number.isFinite(Number(p?.y_m))),
    [corner]
  );
  const reference = useMemo(
    () => (corner?.reference_points || []).filter((p) => Number.isFinite(Number(p?.x_m)) && Number.isFinite(Number(p?.y_m))),
    [corner]
  );
  const connector = useMemo(() => corner?.max_gap_connector || {}, [corner]);

  const mainGeometry = useMemo(
    () => buildRacingLineGeometry(target, reference, connector, { mode: viewMode, width: 560, height: 300, emphasisFactor: 3 }),
    [target, reference, connector, viewMode]
  );
  const zoomGeometry = useMemo(
    () => buildRacingLineGeometry(target, reference, connector, { mode: 'zoom', width: 240, height: 180, emphasisFactor: 3 }),
    [target, reference, connector]
  );

  if (!mainGeometry) return <EmptyState text="라인 좌표가 부족합니다." />;

  const modeMeta = {
    overlay: {
      label: '겹쳐 보기',
      caption: '실제 상대 위치 그대로 표시',
      detail: '두 라인 사이 영역을 채워서 겹침 속에서도 차이 구간을 보이게 합니다.',
    },
    emphasis: {
      label: '차이 강조 x3',
      caption: '실제 차이를 시각적으로 3배 확대',
      detail: '거의 겹치는 코너도 어디서 벌어지는지 한눈에 확인하기 쉽습니다.',
    },
    zoom: {
      label: '최대 차이 확대',
      caption: '최대 차이 구간만 확대',
      detail: '코너 전체보다 가장 벌어지는 구간을 자세히 확인하는 용도입니다.',
    },
  };

  return (
    <div className="rounded-2xl border border-border bg-background/80 p-3">
      <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">라인 비교 가시화</div>
          <div className="mt-1 text-sm text-foreground">{modeMeta[viewMode]?.caption}</div>
          <div className="text-xs text-muted-foreground">{modeMeta[viewMode]?.detail}</div>
        </div>
        <div className="inline-flex flex-wrap rounded-xl border border-border bg-muted/80 p-1">
          {[
            ['overlay', '겹쳐 보기'],
            ['emphasis', '차이 강조 x3'],
            ['zoom', '최대 차이 확대'],
          ].map(([key, label]) => (
            <Button variant="outline"
              key={key}
              type="button"
              onClick={() => setViewMode(key)}
              className={cx(
                ' px-3 py-2 text-xs  transition',
                viewMode === key
                  ? '  ring-1 ring-inset ring-cyan-400/40'
                  : ' '
              )}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_240px]">
        <div className="rounded-2xl border border-border bg-background/85 p-2">
          <RacingLineSvg geometry={mainGeometry} labelPrefix={corner?.label || '코너'} />
        </div>
        <div className="grid gap-3">
          <div className="rounded-2xl border border-border bg-muted/70 p-3 text-sm text-foreground">
            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">보기 안내</div>
            <div className="mt-2 flex flex-col gap-1">
              <div>• 채워진 영역: 두 라인 사이 차이 영역</div>
              <div>• 주황색 점선: 기준 주행 라인</div>
              <div>• 노란 점선: 최대 라인 차이 지점</div>
              <div>• 현재 모드: {modeMeta[viewMode]?.label}</div>
            </div>
          </div>
          {zoomGeometry && (
            <div className="rounded-2xl border border-border bg-muted/70 p-3">
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-warning">Inset zoom</div>
              <div className="mt-1 text-sm font-bold text-foreground">최대 차이 구간 확대</div>
              <div className="mt-2 rounded-xl border border-border bg-background/80 p-2">
                <RacingLineSvg geometry={zoomGeometry} labelPrefix={`${corner?.label || '코너'} 최대 차이 구간`} />
              </div>
              <div className="mt-2 text-xs text-muted-foreground">거의 겹쳐 보이는 경우 우측 확대창으로 차이를 먼저 확인하십시오.</div>
            </div>
          )}
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
        <span className="flex items-center gap-2" style={{ color: COMPARISON_COLORS.target }}><span className="h-1 w-8 rounded" style={{ background: COMPARISON_COLORS.target }} />내 라인</span>
        <span className="flex items-center gap-2" style={{ color: COMPARISON_COLORS.reference }}><span className="w-8 border-t-4 border-dashed" style={{ borderColor: COMPARISON_COLORS.reference }} />Garage61 대표 실주행 기준</span>
        <span className="flex items-center gap-2 text-warning"><span className="h-3 w-8 rounded bg-warning/30" />라인 차이 영역</span>
        <span className="flex items-center gap-2 text-warning"><span className="w-8 border-t-2 border-dashed border-warning" />가장 큰 라인 차이</span>
      </div>
    </div>
  );
}

function RacingLineComparisonPanel({ data }) {
  const corners = (data?.corners || []).slice(0, 2);
  if (data?.status !== 'READY' || corners.length === 0) return null;
  return (
    <div className="rounded-2xl border border-primary/25 bg-primary/10 p-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Racing Line Focus · Top 2</div>
          <h4 className="mt-1 text-lg font-semibold text-foreground">속도 손실 + 라인 차이가 같이 나온 코너만 비교</h4>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{data?.note}</p>
        </div>
        <div className="text-xs text-muted-foreground">
          기준 {data?.reference_count || 0}랩 · {data?.unique_driver_count || 0}명 · {data?.consensus_tier || '--'}
        </div>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {corners.map((corner, index) => {
          const metrics = corner?.metrics || {};
          return (
            <div key={corner?.id || index} className="rounded-2xl border border-border bg-background/55 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="rounded-md border border-primary/30 bg-primary/10 px-2 py-1 text-xs font-semibold text-primary">#{index + 1}</span>
                    <span className="text-lg font-semibold text-foreground">{corner?.label}</span>
                  </div>
                  <div className="mt-1 text-sm text-muted-foreground">집중 구간: {corner?.focus_phase || '코너'} · {ISSUE_LABELS[corner?.issue_type] || corner?.issue_type}</div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-lg font-semibold text-destructive">{formatSigned(corner?.delta_s)}</div>
                  <div className="text-xs text-muted-foreground">코너 손실</div>
                </div>
              </div>

              <div className="mt-3 grid grid-cols-3 gap-2">
                <MetricMini label="라인 차이 P75" value={formatNumber(metrics?.p75_line_gap_m, 2, 'm')} />
                <MetricMini label="속도 부족 P75" value={formatNumber(metrics?.p75_speed_deficit_kph, 1, 'km/h')} />
                <MetricMini label="최저속 부족" value={formatNumber(metrics?.min_speed_deficit_kph, 1, 'km/h')} />
              </div>

              <div className="mt-3"><RacingLineMiniMap corner={corner} /></div>

              <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                <div className="rounded-xl border border-border bg-muted/70 p-3 text-foreground">
                  <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">입력 차이</div>
                  <div className="mt-2 flex flex-col gap-1">
                    <div>제동 시작: {metrics?.brake_start_delta_m === null || metrics?.brake_start_delta_m === undefined ? '--' : `${Number(metrics.brake_start_delta_m) > 0 ? '+' : ''}${Number(metrics.brake_start_delta_m).toFixed(1)} m`}</div>
                    <div>풀스로틀: {metrics?.full_throttle_delta_m === null || metrics?.full_throttle_delta_m === undefined ? '--' : `${Number(metrics.full_throttle_delta_m) > 0 ? '+' : ''}${Number(metrics.full_throttle_delta_m).toFixed(1)} m`}</div>
                  </div>
                </div>
                <div className="rounded-xl border border-border bg-muted/70 p-3 text-foreground">
                  <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">판정 근거</div>
                  <div className="mt-2 flex flex-col gap-1">
                    <div>최대 라인 차이 {formatNumber(metrics?.peak_line_gap_m, 2, 'm')}</div>
                    <div>손실 동의율 {formatNumber((metrics?.loss_agreement_ratio || 0) * 100, 0, '%')}</div>
                  </div>
                </div>
              </div>
              <p className="mt-3 rounded-xl border border-primary/20 bg-primary/5 p-3 text-sm leading-relaxed text-primary">{corner?.coaching_note}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ExternalReferencePanel({
  referenceStatus,
  files = [],
  selected = [],
  analysis,
  loading,
  collector,
  collectorLoading,
  apiStatus,
  onToggle,
  onRefresh,
  onApply,
  onAutoSelect,
  onClear,
  onCollect,
  onForceCollect,
  onCancelCollect,
}) {
  const maxSelection = Number(referenceStatus?.display_limit || referenceStatus?.max_selection || 9);
  const minimumForSetup = Number(referenceStatus?.minimum_for_setup_conclusion || 2);
  const strongConsensusCount = Number(referenceStatus?.strong_consensus_count || 3);
  const recommendedCohort = Number(referenceStatus?.recommended_cohort_size || 5);
  const aggregate = analysis?.aggregate || {};
  const warnings = analysis?.warnings || [];
  const errors = analysis?.errors || [];
  const selectedSet = new Set(selected);
  const selectedFiles = files.filter((file) => selectedSet.has(file.name));
  const health = collector?.health || referenceStatus?.collector_health || {};
  const capabilities = collector?.collector_capabilities || {
    api_token_configured: health?.collection_policy?.api_token_configured,
    preferred_mode: health?.collection_policy?.preferred_mode,
  };
  const job = collector?.job || null;
  const collectorBusy = Boolean(job?.busy);
  const jobState = job?.state || 'IDLE';
  const progress = Math.max(0, Math.min(100, Math.round(Number(job?.progress || 0) * 100)));
  const cacheTone = health?.enough_data && health?.cache_fresh
    ? 'border-success/30 bg-success/15'
    : health?.enough_data
    ? 'border-warning/30 bg-warning/15'
    : 'border-accent/30 bg-accent/15';

  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-2xl border border-accent/25 bg-accent/15 p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-foreground" />
              <h3 className="font-semibold text-foreground">Garage61 · IBT 조건 기반 Reference Library</h3>
            </div>
            <p className="mt-2 break-all font-mono text-sm text-foreground">
              {referenceStatus?.directory || '데이터 폴더의 Garage61_References'}
            </p>
            <p className="mt-2 max-w-5xl text-sm leading-relaxed text-muted-foreground">
              IBT에서 차량·트랙·레이아웃·노면온도·기온·연료·습도·바람·강수·가능한 BOP/타이어 조건을 읽습니다.
              Garage61 API를 우선 사용해 최대 1,000개 후보를 조건별 단계 검색하고, 공개 telemetry만 내려받습니다. API 토큰이 없을 때만 기존 브라우저 수집기를 fallback으로 사용합니다.
              CSV는 무결성·표본수·거리 커버리지·PositionType을 검증한 뒤 조건 가중 median + IQR + 코너 컨센서스로 분석합니다. 코너별 조작 비교 지도에서는 모든 자동 구간의 실제 라인을 비교할 수 있습니다. 아래 요약은 속도 손실과 GPS 차이가 큰 상위 2개 구간을 표시합니다.
            </p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className={cx(
                'rounded-lg border px-2.5 py-1 font-semibold',
                apiStatus?.connected
                  ? 'border-success/30 bg-success/10 text-success'
                  : capabilities?.api_token_configured
                  ? 'border-warning/30 bg-warning/10 text-warning'
                  : 'border-border bg-muted text-muted-foreground'
              )}>
                {apiStatus?.connected ? 'API CONNECTED' : capabilities?.api_token_configured ? 'API TOKEN SET' : 'API TOKEN 없음'}
              </span>
              <span className="rounded-lg border border-primary/25 bg-primary/10 px-2.5 py-1 font-semibold text-primary">
                {capabilities?.preferred_mode === 'API' ? 'API FIRST' : 'BROWSER FALLBACK'}
              </span>
              {apiStatus?.user_slug && <span className="rounded-lg border border-border bg-muted px-2.5 py-1 text-muted-foreground">{apiStatus.user_slug}</span>}
              {apiStatus?.subscription_plan && <span className="rounded-lg border border-border bg-muted px-2.5 py-1 text-muted-foreground">Plan {apiStatus.subscription_plan}</span>}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button variant="default"
              type="button"
              onClick={onCollect}
              disabled={collectorBusy || collectorLoading || !health?.enabled}
              className=" coach-primary-action px-4 py-2 text-sm   transition  disabled:cursor-not-allowed disabled: disabled:"
            >
              {collectorBusy ? 'Garage61 수집 중' : '조건 데이터 보충'}
            </Button>
            <Button variant="outline"
              type="button"
              onClick={onForceCollect}
              disabled={collectorBusy || collectorLoading || !health?.enabled}
              className=" border   px-4 py-2 text-sm   transition   disabled:opacity-40"
            >
              강제 새로 수집
            </Button>
            {collectorBusy && (
              <Button variant="outline"
                type="button"
                onClick={onCancelCollect}
                className=" border   px-4 py-2 text-sm   transition  "
              >
                수집 중지
              </Button>
            )}
            <Button variant="outline"
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className=" border   px-4 py-2 text-sm   transition   disabled:opacity-40"
            >
              캐시 새로고침
            </Button>
          </div>
        </div>
      </div>

      <div className={cx('rounded-2xl border p-5', cacheTone)}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={cx(
                'rounded-lg border px-2.5 py-1 text-xs font-semibold',
                health?.enough_data && health?.cache_fresh
                  ? 'border-success/30 bg-success/10 text-success'
                  : health?.enough_data
                  ? 'border-warning/30 bg-warning/10 text-warning'
                  : 'border-accent/30 bg-accent/10 text-foreground'
              )}>
                {health?.enough_data && health?.cache_fresh ? 'CACHE READY' : health?.enough_data ? 'CACHE STALE' : 'REFERENCE 부족'}
              </span>
              <span className="font-mono text-xs text-muted-foreground">{health?.recommendation || '--'}</span>
            </div>
            <p className="mt-3 text-sm leading-relaxed text-foreground">
              조건 유사 + 현재보다 빠른 기준 {health?.usable_reference_count ?? 0}/{health?.minimum_usable_references ?? 5}개,
              서로 다른 드라이버 {health?.usable_distinct_drivers ?? 0}/{health?.minimum_distinct_drivers ?? 3}명.
              {health?.last_context_scan_age_hours !== null && health?.last_context_scan_age_hours !== undefined
                ? ` 마지막 동일 조건 수집 ${Number(health.last_context_scan_age_hours).toFixed(1)}시간 전.`
                : ' 동일 조건의 수집 이력이 없습니다.'}
            </p>
            {(health?.reasons || []).length > 0 && (
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{health.reasons.join(' · ')}</p>
            )}
          </div>
          <div className="grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-4 xl:min-w-[470px]">
            <MetricMini label="적합 빠른 CSV" value={`${health?.usable_reference_count ?? 0}개`} />
            <MetricMini label="드라이버" value={`${health?.usable_distinct_drivers ?? 0}명`} />
            <MetricMini label="수집 목표" value={`${health?.target_reference_count ?? 8}개`} />
            <MetricMini label="캐시 유효" value={health?.cache_fresh ? 'YES' : 'NO'} />
          </div>
        </div>

        {(collectorBusy || (job && jobState !== 'IDLE')) && (
          <div className="mt-4 rounded-xl border border-border bg-background/70 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  {collectorBusy ? <RefreshCw className="h-4 w-4 animate-spin text-foreground" /> : <CheckCircle2 className="h-4 w-4 text-muted-foreground" />}
                  <span className="font-semibold text-foreground">{jobState}</span>
                  {job?.collector_mode && <span className="rounded-md border border-primary/25 bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">{job.collector_mode}</span>}
                </div>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{job?.message || 'Garage61 수집 상태 확인 중'}</p>
              </div>
              <div className="text-right font-mono text-xs text-muted-foreground">
                {job?.elapsed_s !== undefined ? `${Number(job.elapsed_s).toFixed(0)}s` : ''}
                {job?.scanned_rows !== undefined ? ` · ${job.scanned_rows} laps` : ''}
                {job?.context_csv_count !== undefined
                  ? ` · REF ${job.context_csv_count}/${job.target_count || health?.target_reference_count || 8}`
                  : job?.cached_csv_count !== undefined
                  ? ` · CSV ${job.cached_csv_count}/${job.target_count || health?.target_reference_count || 8}`
                  : ''}
              </div>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-accent">
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${progress}%` }} />
            </div>
            {jobState === 'WAITING_FOR_LOGIN' && (
              <p className="mt-3 text-sm text-warning">Garage61 전용 Chromium 창에서 한 번 로그인하면 브라우저 프로필에 세션이 유지됩니다.</p>
            )}
            {['AUTHENTICATING', 'RESOLVING_IDS', 'QUERYING_LAPS', 'RANKING', 'DOWNLOADING', 'VALIDATING', 'RETRY_WAIT', 'WAITING_RESULTS', 'SCANNING_ROWS'].includes(jobState) && (
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                API 모드에서는 조건 범위를 단계적으로 완화해 후보를 확보하고 CSV를 검증합니다. 브라우저 fallback은 기존 긴 타임아웃/백오프를 유지합니다. 분석 앱은 수집을 기다리지 않고 현재 캐시 결과를 계속 표시합니다.
              </p>
            )}
          </div>
        )}
      </div>

      {referenceStatus?.error && (
        <ErrorMessage message={`외부 기준 목록 조회 실패: ${referenceStatus.error}`} />
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <MetricMini label="호환 CSV" value={`${referenceStatus?.total_compatible ?? files.length}개`} />
        <MetricMini label="화면 표시" value={`${files.length}/${referenceStatus?.display_limit || 9}`} />
        <MetricMini label="현재 선택" value={`${selected.length}/${maxSelection}`} />
        <MetricMini label="자동 코호트" value={`${recommendedCohort}개`} />
        <MetricMini label="Setup Assist 최소" value={`${minimumForSetup}개`} />
        <MetricMini label="강한 컨센서스" value={`${strongConsensusCount}개+`} />
      </div>

      {Number(referenceStatus?.scanned_reference_count || 0) > 0 && Number(referenceStatus?.total_compatible || 0) === 0 && (
        <div className="rounded-xl border border-warning/30 bg-warning/20 px-4 py-3 text-sm leading-relaxed text-warning">
          Garage61 CSV는 디스크에서 발견됐지만 현재 IBT와 호환 판정된 파일이 없습니다.
          v2.9.3은 Top 2 코너 라인 비교에 차이 강조 x3·영역 채우기·최대 차이 확대 인셋을 추가해, 거의 겹치는 라인도 더 쉽게 읽도록 개선했습니다.
          {referenceStatus?.identity_rejections && Object.keys(referenceStatus.identity_rejections).length > 0 && (
            <span className="ml-2 font-mono text-xs text-warning">
              {Object.entries(referenceStatus.identity_rejections).map(([key, value]) => `${key}:${value}`).join(' · ')}
            </span>
          )}
        </div>
      )}

      {referenceStatus?.current_conditions && (
        <div className="rounded-2xl border border-border bg-background/55 p-4">
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">현재 IBT 조건 → Garage61 검색 기준</div>
          <div className="flex flex-wrap gap-2 text-sm">
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Track {formatNumber(referenceStatus.current_conditions.track_temp_c, 1, '°C')}</span>
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Air {formatNumber(referenceStatus.current_conditions.air_temp_c, 1, '°C')}</span>
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Fuel {formatNumber(referenceStatus.current_conditions.fuel_l, 1, ' L')}</span>
            {referenceStatus.current_conditions.fuel_used_l !== undefined && referenceStatus.current_conditions.fuel_used_l !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Fuel/Lap {formatNumber(referenceStatus.current_conditions.fuel_used_l, 2, ' L')}</span>}
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Humidity {formatNumber(referenceStatus.current_conditions.relative_humidity_pct, 0, '%')}</span>
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Wind {formatNumber(referenceStatus.current_conditions.wind_speed_kph, 1, ' km/h')}</span>
            {referenceStatus.current_conditions.fog_level_pct !== undefined && referenceStatus.current_conditions.fog_level_pct !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Fog {formatNumber(referenceStatus.current_conditions.fog_level_pct, 0, '%')}</span>}
            {referenceStatus.current_conditions.weight_penalty_kg !== undefined && referenceStatus.current_conditions.weight_penalty_kg !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">BOP {formatNumber(referenceStatus.current_conditions.weight_penalty_kg, 1, ' kg')}</span>}
            {referenceStatus.current_conditions.power_adjust_pct !== undefined && referenceStatus.current_conditions.power_adjust_pct !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Power {formatNumber(referenceStatus.current_conditions.power_adjust_pct, 2, '%')}</span>}
            {referenceStatus.current_conditions.tire_compound !== undefined && referenceStatus.current_conditions.tire_compound !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Tire #{referenceStatus.current_conditions.tire_compound}</span>}
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">{referenceStatus.current_conditions.weather || 'Weather --'}</span>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <p className="text-xs leading-relaxed text-muted-foreground">
          전체 호환 {referenceStatus?.total_compatible ?? files.length}개 중 셋업 호환성 → 빠른 페이스 → 조건/메타 품질 → 드라이버 다양성 순으로 상위 {referenceStatus?.display_limit || 9}개만 표시합니다. 오래된 캐시 CSV는 보관되지만 이 목록을 늘리지 않습니다.
        </p>
        <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline"
          type="button"
          onClick={onAutoSelect}
          disabled={loading || files.length === 0}
          className=" border   px-4 py-2 text-sm   transition   disabled:opacity-40"
        >
          자동 추천 {Math.min(recommendedCohort, files.length)}개
        </Button>
        <Button variant="outline"
          type="button"
          onClick={onClear}
          disabled={loading || selected.length === 0}
          className=" border   px-4 py-2 text-sm   transition  disabled:opacity-40"
        >
          선택 해제
        </Button>
        <Button variant="default"
          type="button"
          onClick={onApply}
          disabled={loading || selected.length === 0}
          className=" coach-primary-action px-4 py-2 text-sm   transition  disabled:cursor-not-allowed disabled: disabled:"
        >
          선택 코호트로 재분석
        </Button>
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-background/25 p-3">
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {files.length ? files.map((file) => {
            const checked = selectedSet.has(file.name);
            const selectionFull = file.usable === false || (!checked && selected.length >= maxSelection);
            const similarity = Number(file.condition_similarity?.score ?? 0.5);
            const similarityPct = Math.round(similarity * 100);
            return (
              <label
                key={file.name}
                className={cx(
                  'block cursor-pointer rounded-2xl border p-4 transition',
                  checked
                    ? 'border-accent/45 bg-accent/25'
                    : selectionFull
                    ? 'cursor-not-allowed border-border bg-background/30 opacity-50'
                    : 'border-border bg-background/55 hover:border-accent/35'
                )}
              >
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={selectionFull}
                    onChange={() => onToggle(file.name)}
                    className="mt-1 h-4 w-4 accent-violet-500"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-foreground">{file.driver_name || 'Unknown Driver'}</span>
                      <div className="flex items-center gap-2">
                        <span className={cx(
                          'rounded-lg border px-2 py-1 text-xs font-semibold',
                          similarityPct >= 75
                            ? 'border-success/30 bg-success/10 text-success'
                            : similarityPct >= 55
                            ? 'border-warning/30 bg-warning/10 text-warning'
                            : 'border-border bg-muted text-muted-foreground'
                        )}>
                          조건 {similarityPct}%
                        </span>
                        <span className="rounded-lg border border-primary/25 bg-primary/10 px-2 py-1 font-mono text-sm font-semibold text-primary">
                          {file.lap_time || formatLapSeconds(file.lap_time_s)}
                        </span>
                      </div>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">{file.car_name}</p>
                    {file.usable === false && <p className="mt-2 text-sm leading-relaxed text-warning">비교 불가: {file.validation_error || '기준 텔레메트리의 시간·거리 데이터가 부족합니다.'}</p>}
                    <p className="mt-1 text-sm text-muted-foreground">
                      {file.track_name}{file.track_config ? ` · ${file.track_config}` : ''}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                      {file.metadata_enriched ? (
                        <span className="rounded-md border border-success/20 bg-success/5 px-2 py-1 text-success">{file.metadata_source === 'GARAGE61_API' ? 'API META' : 'WEB META'}</span>
                      ) : (
                        <span className="rounded-md border border-border bg-muted px-2 py-1 text-muted-foreground">CSV ONLY</span>
                      )}
                      {file.faster_than_current_best && <span className="rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-primary">FASTER</span>}
                      {file.condition_similarity?.setup_compatible === false && <span className="rounded-md border border-destructive/20 bg-destructive/5 px-2 py-1 text-destructive">SETUP 비교 제외</span>}
                      {file.setup_available && <span className="rounded-md border border-warning/20 bg-warning/5 px-2 py-1 text-warning">SETUP 공개</span>}
                      {!file.setup_available && file.setup_access === 'LOCKED' && <span className="rounded-md border border-border bg-muted px-2 py-1 text-muted-foreground">SETUP 잠김</span>}
                      {file.track_temp_c !== undefined && file.track_temp_c !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Track {formatNumber(file.track_temp_c, 1, '°C')}</span>}
                      {file.air_temp_c !== undefined && file.air_temp_c !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Air {formatNumber(file.air_temp_c, 1, '°C')}</span>}
                      {file.fuel_l !== undefined && file.fuel_l !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Fuel {formatNumber(file.fuel_l, 1, 'L')}</span>}
                      {file.fuel_used_l !== undefined && file.fuel_used_l !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Fuel/Lap {formatNumber(file.fuel_used_l, 2, 'L')}</span>}
                      {file.fog_level_pct !== undefined && file.fog_level_pct !== null && Number(file.fog_level_pct) > 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Fog {formatNumber(file.fog_level_pct, 0, '%')}</span>}
                      {file.track_wetness_pct !== undefined && file.track_wetness_pct !== null && Number(file.track_wetness_pct) > 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Wet {formatNumber(file.track_wetness_pct, 0, '%')}</span>}
                      {file.weather && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">{file.weather}</span>}
                      {file.track_usage_pct !== undefined && file.track_usage_pct !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Usage {formatNumber(file.track_usage_pct, 0, '%')}</span>}
                      {file.driver_rating !== undefined && file.driver_rating !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">iR {Math.round(Number(file.driver_rating))}</span>}
                      {file.weight_penalty_kg !== undefined && file.weight_penalty_kg !== null && Number(file.weight_penalty_kg) !== 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">BOP {formatNumber(file.weight_penalty_kg, 1, 'kg')}</span>}
                      {file.power_adjust_pct !== undefined && file.power_adjust_pct !== null && Number(file.power_adjust_pct) !== 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Power {formatNumber(file.power_adjust_pct, 2, '%')}</span>}
                      {file.tire_compound !== undefined && file.tire_compound !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Tire #{file.tire_compound}</span>}
                      {file.track_usage && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">{file.track_usage}</span>}
                    </div>
                    {(file.condition_similarity?.reasons || []).length > 0 && (
                      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                        {(file.condition_similarity.reasons || []).slice(0, 4).join(' · ')}
                      </p>
                    )}
                    <p className="mt-2 truncate text-xs text-muted-foreground" title={file.name}>{file.name}</p>
                  </div>
                </div>
              </label>
            );
          }) : (
            <div className="lg:col-span-2 2xl:col-span-3">
              <EmptyState text={collectorBusy
                ? '현재 IBT 조건에 맞는 Garage61 기준을 백그라운드에서 수집 중입니다. 분석 앱은 기존 IBT 결과를 계속 사용할 수 있습니다.'
                : '현재 IBT와 같은 차량·트랙·레이아웃의 Garage61 CSV가 없습니다. 조건 데이터 보충을 누르면 IBT 정보를 기준으로 Garage61 수집을 시작합니다.'} />
            </div>
          )}
        </div>
      </div>

      <div className={cx(
        'rounded-2xl border p-4 text-sm leading-relaxed',
        selected.length >= minimumForSetup
          ? 'border-success/25 bg-success/15 text-success'
          : 'border-warning/30 bg-warning/20 text-warning'
      )}>
        선택 {selected.length}/{maxSelection}개. 단일 CSV는 셋업 결론에 사용하지 않습니다.
        {selected.length === 1
          ? ' 현재는 비교·코칭 전용입니다.'
          : selected.length >= strongConsensusCount
          ? ' 여러 드라이버·조건 유사도·코너 동의율을 함께 사용해 강한 컨센서스를 시도합니다.'
          : selected.length >= minimumForSetup
          ? ' 2개 기준은 PROVISIONAL Setup Assist로 사용하며 신뢰도 상한을 낮춥니다.'
          : ` 최소 ${minimumForSetup}개가 있어야 Setup Assist 후보를 만들 수 있습니다.`}
      </div>

      {selectedFiles.length > 0 && (
        <div className="text-xs text-muted-foreground">
          선택 코호트: {selectedFiles.map((file) => `${file.driver_name || 'Unknown'} ${file.lap_time || ''}`).join(' · ')}
        </div>
      )}

      {analysis?.status === 'READY' && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <MetricCard label="내 대상 랩" value={aggregate.target_time || '--'} icon={TimerReset} tone="cyan" />
            <MetricCard label={aggregate.reference_label || "여러 랩 합성 기준"} value={aggregate.reference_median_time || '--'} icon={Target} tone="violet" />
            <MetricCard
              label="외부 대비 격차"
              value={formatSigned(aggregate.gap_to_reference_median_s)}
              note={aggregate.gap_to_reference_median_s == null ? '측정 불가' : Number(aggregate.gap_to_reference_median_s) > 0 ? '내 랩이 느림' : Number(aggregate.gap_to_reference_median_s) < 0 ? '내 랩이 빠름' : '동일한 랩타임'}
              icon={TrendingUp}
              tone={aggregate.gap_to_reference_median_s == null ? 'slate' : Number(aggregate.gap_to_reference_median_s) > 0 ? 'rose' : 'emerald'}
            />
            <MetricCard
              label="컨센서스"
              value={analysis.consensus_tier || '--'}
              note={`빠른 기준 ${analysis.decision_reference_count || 0}개`}
              icon={Database}
              tone={analysis.strong_consensus ? 'emerald' : analysis.decision_ready ? 'amber' : 'slate'}
            />
            <MetricCard
              label="서로 다른 드라이버"
              value={`${analysis.unique_driver_count || 0}명`}
              note={`선택 ${analysis.selected_reference_count || 0}개`}
              icon={Activity}
              tone="slate"
            />
            <MetricCard
              label="조건 유사도"
              value={aggregate.condition_similarity_median !== null && aggregate.condition_similarity_median !== undefined
                ? `${Math.round(Number(aggregate.condition_similarity_median) * 100)}%`
                : '--'}
              note={`메타 보강 ${aggregate.metadata_enriched_count || 0}개`}
              icon={Thermometer}
              tone="slate"
            />
          </div>

          {(warnings.length > 0 || errors.length > 0) && (
            <div className="flex flex-col gap-2">
              {warnings.map((message, index) => (
                <div key={`warning-${index}`} className="rounded-xl border border-warning/25 bg-warning/15 p-3 text-sm text-warning">
                  {message}
                </div>
              ))}
              {errors.map((message, index) => (
                <div key={`error-${index}`} className="rounded-xl border border-destructive/25 bg-destructive/15 p-3 text-sm text-destructive">
                  {message}
                </div>
              ))}
            </div>
          )}

          <RacingLineComparisonPanel data={analysis.line_comparison} />

          {(analysis.top_losses || []).length > 0 && (
            <div>
              <div className="mb-3 flex items-center justify-between gap-3">
                <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">외부 코호트 대비 주요 손실</h4>
                <span className="text-xs text-muted-foreground">거리 정규화 median · IQR</span>
              </div>
              <div className="grid gap-3 lg:grid-cols-3">
                {(analysis.top_losses || []).slice(0, 6).map((corner) => (
                  <div key={corner.id} className="rounded-2xl border border-border bg-background/60 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="font-semibold text-foreground">{corner.label}</div>
                        <div className="mt-1 text-xs text-muted-foreground">{ISSUE_LABELS[corner.issue_type] || corner.issue_type}</div>
                      </div>
                      <span className="font-mono font-semibold text-destructive">{formatSigned(corner.delta_s)}</span>
                    </div>
                    <div className="mt-3 text-sm text-muted-foreground">
                      손실 동의율 {formatNumber((corner.reference_consensus?.loss_agreement_ratio || 0) * 100, 0, '%')}
                      {' · '}기준 {corner.reference_consensus?.reference_count || 0}개
                      {' · '}드라이버 {corner.reference_consensus?.unique_driver_count || 0}명
                    </div>
                    <div className="mt-1 font-mono text-xs text-muted-foreground">
                      IQR {formatSigned(corner.reference_consensus?.loss_p25_s)} ~ {formatSigned(corner.reference_consensus?.loss_p75_s)}
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-foreground">{corner.driver_action}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(analysis.visualizations?.trace || []).length > 0 && (
            <TraceChart
              trace={analysis.visualizations.trace}
              targetLap={aggregate.target_lap}
              referenceLap="코호트 median"
            />
          )}
        </>
      )}
    </div>
  );
}

function LongTermMemoryPanel({ memoryStatus, sessions = [], currentMemory }) {
  const enabled = Boolean(memoryStatus?.enabled);
  const counts = memoryStatus?.counts || {};
  const similar = currentMemory?.similar_sessions || [];
  const outLapAggregate = currentMemory?.current_out_lap?.aggregate || {};
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h3 className="font-semibold">개인 주행 기록</h3><p className="mt-1 text-sm text-muted-foreground">주행 지표와 셋업 실험을 로컬에 저장합니다. 저장·검색에는 AI 토큰을 사용하지 않습니다.</p></div>
        <Badge variant={enabled ? 'secondary' : 'destructive'}>{enabled ? '저장 활성화' : '저장 비활성화'}</Badge>
      </div>
      {!enabled && memoryStatus?.error && <ErrorMessage message={memoryStatus.error} />}
      <div className="grid grid-cols-3 gap-3">
        <MetricMini label="저장 세션" value={counts.sessions ?? 0} />
        <MetricMini label="AI 계획" value={counts.experiment_plans ?? 0} />
        <MetricMini label="DB 용량" value={memoryStatus?.size_bytes == null ? '--' : formatBytes(memoryStatus.size_bytes)} />
      </div>
      {currentMemory?.saved && (
        <Alert><CheckCircle2 aria-hidden="true" /><AlertDescription>현재 분석이 저장되었습니다. 유사한 과거 주행을 아래에서 확인할 수 있습니다.<span className="mt-1 block text-xs text-muted-foreground">기록 ID · {String(currentMemory.session_id || '').slice(0, 16)}</span></AlertDescription></Alert>
      )}
      {currentMemory?.current_out_lap?.available && (
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">현재 아웃랩 준비 패턴</h4>
            <span className="text-sm font-mono text-muted-foreground">로컬 요약 · 토큰 미사용</span>
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <MetricMini label="Brake events" value={formatNumber(outLapAggregate.brake_events_median, 1)} />
            <MetricMini label="Steering work" value={formatNumber(outLapAggregate.steering_work_deg_median, 0, '°')} />
            <MetricMini label="Surface temp Δ" value={formatSigned(outLapAggregate.surface_temp_delta_c_median, 2, ' °C')} />
            <MetricMini label="Pressure Δ" value={formatSigned(outLapAggregate.pressure_delta_kpa_median, 2, ' kPa')} />
          </div>
        </div>
      )}

      <div>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            현재 조건과 유사한 과거 세션
          </h4>
          <span className="text-sm font-mono text-muted-foreground">{similar.length}개 검색</span>
        </div>
        {similar.length ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {similar.map((item) => {
              const topIssue = item.memory_summary?.top_issues?.[0];
              const outcome = item.experiment_outcomes?.[0];
              return (
                <div key={item.session_id} className="rounded-2xl border border-border bg-background/60 p-4 text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-bold text-foreground">{item.car_name} @ {item.track_name}</div>
                      <div className="mt-1 text-muted-foreground">{item.track_config || '기본 레이아웃'} · {sessionModeLabel(item.session_mode)}</div>
                    </div>
                    <span className="rounded-lg border border-accent/25 bg-accent/10 px-2 py-1 font-mono font-semibold text-foreground">
                      {Math.round(Number(item.similarity_score || 0) * 100)}%
                    </span>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 font-mono text-sm">
                    <div><span className="text-muted-foreground">최고 랩</span><div className="mt-1 text-primary">{formatLapSeconds(item.best_lap_s)}</div></div>
                    <div><span className="text-muted-foreground">노면 온도</span><div className="mt-1 text-warning">{formatNumber(item.track_temp_c, 1, ' °C')}</div></div>
                    <div><span className="text-muted-foreground">셋업 판단</span><div className="mt-1 text-foreground">{{ TEST: '시험 권장', KEEP: '유지', REVERT: '원복', HOLD: '보류' }[item.setup_decision] || item.setup_decision || '--'}</div></div>
                  </div>
                  {topIssue && (
                    <p className="mt-3 leading-relaxed text-muted-foreground">
                      {topIssue.corner_id} · {SPEED_LABELS[topIssue.speed_class] || topIssue.speed_class} · {ISSUE_LABELS[topIssue.issue_type] || topIssue.issue_type} · {formatSigned(topIssue.delta_s)}
                    </p>
                  )}
                  {outcome && (
                    <div className="mt-3 rounded-xl border border-warning/20 bg-warning/15 p-2 text-warning">
                      과거 A/B: <b>{outcome.verdict}</b> · {outcome.reason}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState text="동일 차량·트랙의 과거 세션이 아직 없습니다. 파일을 분석할수록 개인화 근거가 누적됩니다." />
        )}
      </div>

      <div>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">최근 DB 저장 세션</h4>
          <span className="text-sm font-mono text-muted-foreground">최근 {sessions.length}개</span>
        </div>
        <HistoryPanel history={sessions} />
      </div>
    </div>
  );
}

function HistoryPanel({ history = [] }) {
  if (!history.length) return <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Database aria-hidden="true" /></EmptyMedia><EmptyTitle>저장된 주행 분석이 없습니다.</EmptyTitle><EmptyDescription>주행 유형을 지정해 개인 기록으로 저장하세요.</EmptyDescription></EmptyHeader></Empty>;
  return (
    <div className="flex flex-col gap-2">
      {history.map((item) => (
        <div key={item.session_id || item.id} className="coach-history-entry flex flex-col gap-3 text-sm xl:flex-row xl:items-center xl:justify-between">
          <div className="min-w-0">
            <div className="truncate font-bold text-foreground">
              {item.car_name || item.car} · {item.track_name || item.track} {item.track_config || ''}
            </div>
            <div className="mt-1 truncate text-muted-foreground">{item.filename || item.file}</div>
          </div>
          <div className="flex flex-wrap items-center gap-3 font-mono">
            <span className="text-muted-foreground">{sessionModeLabel(item.session_mode)}</span>
            <span className="text-primary">{formatLapSeconds(item.best_lap_s) || item.targetTime || '--'}</span>
            <span className="text-foreground">{CLASSIFICATION_LABELS[item.classification] || item.classification || '--'}</span>
            <span className="text-muted-foreground">
              {new Date((item.analyzed_at || item.createdAt || 0) * (item.analyzed_at ? 1000 : 1)).toLocaleString()}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyState({ text }) {
  return <Empty className="min-h-[150px] border"><EmptyHeader><EmptyMedia variant="icon"><Database aria-hidden="true" /></EmptyMedia><EmptyDescription>{text}</EmptyDescription></EmptyHeader></Empty>;
}

function ErrorMessage({ message }) {
  return <Alert variant="destructive"><XCircle aria-hidden="true" /><AlertDescription>{message}</AlertDescription></Alert>;
}

function SessionModeChangeDialog({ prompt, loading, onCancel, onConfirm }) {
  if (!prompt) return null;
  const currentLabels = (prompt.currentModes || []).map(sessionModeLabel).join(', ');
  return <Dialog open onOpenChange={(open) => { if (!open && !loading) onCancel(); }}>
    <DialogContent onCloseAutoFocus={(event) => { event.preventDefault(); prompt.returnFocus?.focus(); }} onEscapeKeyDown={(event) => { if (loading) event.preventDefault(); }} onInteractOutside={(event) => { if (loading) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>DB 세션 유형을 변경하시겠습니까?</DialogTitle><DialogDescription className="break-all">{prompt.filename}</DialogDescription></DialogHeader>
      <div className="grid gap-3 sm:grid-cols-2">
        <Card><CardHeader><CardDescription>현재 DB 분류</CardDescription><CardTitle>{currentLabels || '미지정'}</CardTitle></CardHeader></Card>
        <Card><CardHeader><CardDescription>변경 후</CardDescription><CardTitle>{sessionModeLabel(prompt.nextMode)}</CardTitle></CardHeader></Card>
      </div>
      <p className="text-sm leading-relaxed text-muted-foreground">해당 IBT의 기존 세션·랩·코너 장기 기억을 새 유형으로 교체합니다. 원본 텔레메트리 파일은 변경되지 않습니다.</p>
      <DialogFooter><Button variant="outline" onClick={onCancel} disabled={loading}>취소</Button><Button onClick={onConfirm} disabled={loading}>{loading ? '변경 중' : 'DB 유형 변경'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}

function RunSummary({ analysis, onNavigate, referenceMode = 'LOCAL' }) {
  if (analysis.data_quality?.analysis_scope === 'CLEAN_SEGMENTS_ONLY') {
    const corners = analysis.coaching?.clean_segment_corners || [];
    return <div className="flex flex-col gap-4"><p className="text-sm text-warning">유효 완주랩 0개 · 깨끗한 코너 구간만 관측합니다. 이 구간을 랩타임이나 A/B 속도 효과로 환산하지 않습니다.</p><div className="grid gap-3 sm:grid-cols-2">{corners.slice(0, 2).map((corner, index) => <Button variant="surface" type="button" key={corner.id || index} onClick={() => onNavigate('corner-inputs', corner.id || corner.corner_id)} className=" border   p-4 text-left"><h3 className="font-bold text-foreground">{corner.label || corner.id} · 랩 {corner.lap_number ?? '--'}</h3><p className="mt-2 text-sm text-muted-foreground">최저속도 {formatNumber(corner.target?.min_speed_kph, 1, ' km/h')} · {corner.speed_class || '속도 구간 미분류'}</p><p className="mt-2 text-sm text-primary">코너 입력 확인하기</p></Button>)}</div><p className="text-sm text-muted-foreground">셋업 실험에서 구간·속도·증상을 선택하면 작은 변경을 탐색할 수 있습니다.</p></div>;
  }
  const external = analysis.external_reference;
  const aggregate = external?.aggregate || {};
  const comparison = referenceMode === 'LOCAL' ? analysis.comparison || {} : {};
  const externalReady = referenceMode !== 'LOCAL' && external?.status === 'READY';
  const gap = externalReady ? aggregate.gap_to_reference_median_s : comparison.total_delta_s;
  const candidates = externalReady ? external.top_losses || [] : referenceMode === 'LOCAL' ? analysis.performance?.corners || [] : [];
  const losses = candidates.filter((corner) => corner.delta_s !== null && corner.delta_s !== undefined && Number(corner.delta_s) > 0)
    .sort((left, right) => Number(right.delta_s) - Number(left.delta_s)).slice(0, 2);
  return <div className="flex flex-col gap-5">
    <div className="coach-overview-metrics grid gap-3 sm:grid-cols-3">
      <MetricCard label="내 대상 랩" value={externalReady ? aggregate.target_time : comparison.target_time || formatLapSeconds(analysis.laps?.find((lap) => String(lap.lap_number) === String(analysis.lap_selection?.target_lap))?.lap_time_s)} note={`랩 ${comparison.target_lap ?? analysis.lap_selection?.target_lap ?? '--'}`} icon={TimerReset} />
      <MetricCard label={externalReady ? referenceMode === 'SINGLE' ? '실제 드라이버 기준' : '여러 드라이버 합성 기준' : '내 기준 랩'} value={externalReady ? aggregate.reference_median_time : comparison.reference_time || '비교 기준 없음'} note={externalReady ? aggregate.reference_label || `Garage61 · ${external.unique_driver_count || 0}명 · ${referenceMode === 'SINGLE' ? '실제 랩' : '중앙값'}` : comparison.reference_lap != null ? `랩 ${comparison.reference_lap}` : '기준랩을 추가하면 코너 손실을 비교합니다.'} icon={Target} tone="orange" />
      <MetricCard label="기준 대비 차이" value={gap == null ? '측정 불가' : formatSigned(gap)} note={gap == null ? '비교 가능한 기준이 필요합니다.' : Number(gap) > 0 ? '내 대상 랩이 느립니다.' : Number(gap) < 0 ? '내 대상 랩이 빠릅니다.' : '동일한 랩타임입니다.'} icon={TrendingDown} tone={gap == null ? 'slate' : Number(gap) > 0 ? 'rose' : 'emerald'} />
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="font-semibold text-foreground">먼저 확인할 코너</h3><p className="mt-1 text-sm text-muted-foreground">측정된 시간 손실이 큰 두 구간부터 입력과 라인을 확인하세요.</p></div>
      <Button variant="outline" type="button" onClick={() => onNavigate('corner-inputs')} className=" border  px-4 py-2 text-sm  ">코너 비교 열기</Button>
    </div>
    {losses.length ? <div className="grid gap-3 md:grid-cols-2">{losses.map((corner, index) => <Button variant="surface" key={corner.id || corner.corner_id || index} type="button" onClick={() => onNavigate('corner-inputs', corner.id || corner.corner_id)} className=" border   p-4 text-left ">
      <div className="flex items-center justify-between gap-3"><span className="font-semibold text-foreground">{corner.label || corner.corner_id || corner.id || `코너 ${index + 1}`}</span><span className="font-mono font-semibold text-destructive">{formatSigned(corner.delta_s)}</span></div>
      <div className="mt-2 text-sm text-muted-foreground">집중 단계: {ISSUE_LABELS[corner.issue_type] || corner.issue_type || '코너 전체'} · {SPEED_LABELS[corner.speed_class] || corner.speed_class || '속도 구간 미분류'}</div>
      <p className="mt-2 text-sm leading-relaxed text-foreground">{corner.coaching_note || corner.coaching || '브레이크 해제, 최저속도, 가속 시작점을 기준 주행과 비교하세요.'}</p>
    </Button>)}</div> : <EmptyState text="측정 가능한 코너 손실이 없습니다. 유효랩과 기준 데이터 상태를 확인하세요." />}
    {externalReady && <p className="text-xs leading-relaxed text-muted-foreground">Garage61 기준 조건 유사도 {aggregate.condition_similarity_median == null ? '측정 불가' : `${Math.round(Number(aggregate.condition_similarity_median) * 100)}%`} · 서로 다른 주행 조건과 자연 편차를 고려해 다음 런에서 검증하세요.</p>}
  </div>;
}

const FEEDBACK_OPTION_DESCRIPTIONS = {
  EXPLORE: '적은 랩으로 시작 · 처음 사용 시 권장', EVIDENCE: '반복 측정 근거가 있을 때',
  ENTRY: '감속하며 들어갈 때', MID_CORNER: '꼭짓점 부근', EXIT: '가속하며 나갈 때',
  LOW: '105km/h 미만', MEDIUM: '105 이상 · 165 미만', HIGH: '165km/h 이상',
  UNDERSTEER: '앞이 바깥으로 밀림', OVERSTEER: '뒤가 흐르며 돌아감',
  BRAKING: '감속 중 흔들림', KERB: '연석에서 접지 잃음',
};
function FeedbackSelector({ label, value, onChange, options, allowClear = true, number, tone = 'violet' }) {
  const id = useId();
  return <FieldSet className="coach-input-group" data-tone={tone}>
    <FieldLegend data-help={label} className="coach-feedback-legend">{number && <span className="coach-group-number" aria-hidden="true">{number}</span>}<span className="coach-group-label">{label}</span><HelpHint topic={label} /></FieldLegend>
    <ToggleGroup type="multiple" variant="outline" value={value ? [value] : []} onValueChange={(next) => onChange(next.find(key => key !== value) || (allowClear ? next[0] || '' : value))} aria-label={label}
      className={cx('coach-feedback-options', options.length === 3 && 'three-options', options.length === 4 && 'four-options')}>
      {options.map(([key, text]) => <ToggleGroupItem key={key} value={key} aria-label={text} aria-describedby={`${id}-${key}`} data-help={key} className="coach-option">
        {value === key && <CheckCircle2 aria-hidden="true" className="coach-option-check" />}<span className="coach-option-label">{text}</span><span id={`${id}-${key}`} className="coach-option-description">{FEEDBACK_OPTION_DESCRIPTIONS[key]}</span>
      </ToggleGroupItem>)}
    </ToggleGroup>
  </FieldSet>;
}

function StructuredFeedback({ phase, speed, symptom, setupMode, onSetupMode, onPhase, onSpeed, onSymptom, value, onChange, result, coach, onAnalyze, onNavigate, loading, goal, onGoal, trialActive }) {
  const setup = coach?.top_setup_candidate;
  const next = coach?.next_run || {};
  const complete = Boolean(phase && speed && symptom);
  const selectedMatchesResult = complete && result && (result.phase || '') === phase && (result.speed || '') === speed && (result.symptom || '') === symptom && (result.setup_mode || 'EXPLORE') === setupMode;
  const target = setup ? hasSetupNumericValue(setup.value) ? `${formatSetupNumber(setup.value)} ${setup.unit || ''}`.trim() : localizeSetupStep(setup.step_text, setup.direction) : null;
  const missing = [!phase && '구간', !speed && '속도', !symptom && '증상'].filter(Boolean);
  return <div className="flex flex-col gap-5">
    <WorkflowSteps steps={['증상 선택', '추천 확인', '한 항목 변경', '결과 기록']} current={trialActive ? 2 : selectedMatchesResult ? 1 : 0} />
    <div className="coach-feedback-settings">
      <div className="coach-input-group coach-goal-group" data-tone="mint"><GoalSelector value={goal} onChange={onGoal} disabled={trialActive} /><p className="coach-goal-note">추천과 A/B 검증에서 가장 먼저 볼 기준입니다.</p></div>
      <FeedbackSelector label="셋업 추천 방식" value={setupMode} onChange={onSetupMode} allowClear={false} options={[["EXPLORE", "탐색 추천"], ["EVIDENCE", "근거 우선 추천"]]} />
    </div>
    <p className="coach-feedback-note">{setupMode === 'EXPLORE' ? '유효랩 1개 또는 깨끗한 코너로 시작해, 작은 변경의 체감을 확인합니다.' : '반복 측정과 외부 기준에서 확인한 근거로 추천합니다.'}</p>
    <div className="coach-feedback-grid">
      <FeedbackSelector number="01" tone="cyan" label="어느 구간인가요?" value={phase} onChange={onPhase} options={[["ENTRY", "진입"], ["MID_CORNER", "코너 중간"], ["EXIT", "탈출"]]} />
      <FeedbackSelector number="02" tone="amber" label="코너 속도" value={speed} onChange={onSpeed} options={[["LOW", "저속"], ["MEDIUM", "중속"], ["HIGH", "고속"]]} />
      <FeedbackSelector number="03" tone="rose" label="느낀 증상" value={symptom} onChange={onSymptom} options={[["UNDERSTEER", "언더스티어"], ["OVERSTEER", "오버스티어"], ["BRAKING", "제동 불안"], ["KERB", "연석 불안"]]} />
    </div>
    <div className="coach-feedback-detail"><label htmlFor="driver-feedback" className="mb-2 block text-xs text-muted-foreground">코너 위치와 느낌을 더 적어주세요 · 선택 사항</label><Textarea id="driver-feedback" data-help="feedback" aria-label="드라이버 추가 피드백" value={value} onChange={(event) => onChange(event.target.value)} placeholder="예: T1 진입에서 앞이 밀려 브레이크를 놓기 어렵습니다." className="min-h-[88px] w-full  border   p-3 text-sm " /></div>
    <div className="coach-feedback-submit"><Button variant="default" type="button" data-help="feedbackApply" onClick={onAnalyze} disabled={loading || !complete} className="coach-primary-action px-5 py-3 text-sm">{loading ? '분석 중' : '피드백으로 분석 갱신'}</Button><p aria-live="polite">{missing.length ? `추천을 보려면 ${missing.join(' · ')}를 선택하세요.` : selectedMatchesResult ? '선택한 증상이 분석에 반영되었습니다.' : '세 항목 선택 완료. 분석을 갱신해 추천을 확인하세요.'}</p></div>
    {!selectedMatchesResult && (phase || speed || symptom || (result?.setup_mode && result.setup_mode !== setupMode)) && <p className="text-sm text-warning">현재 추천은 이전 입력 기준입니다. 분석을 갱신하면 선택한 증상과 추천 방식을 적용합니다.</p>}
    {selectedMatchesResult && result.status !== 'NOT_SELECTED' && <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-2xl border border-primary/25 bg-primary/10 p-4"><h3 className="font-semibold text-primary">측정된 근거</h3><p className="mt-1 text-xs text-muted-foreground">{result.source === 'GARAGE61' ? 'Garage61 기준 비교' : '내 주행 데이터'}</p>{result.observations?.length ? result.observations.map((item, index) => <div key={item.corner_id || index} className="mt-3 border-t border-border pt-3 text-sm"><div className="font-bold text-foreground">{item.label || item.corner || item.corner_id} · {formatSigned(item.delta_s)}</div>{(item.evidence || []).map((evidence, i) => <p key={i} className="mt-1 leading-relaxed text-foreground">{evidence}</p>)}</div>) : <p className="mt-3 text-sm text-muted-foreground">선택한 조건에서 비교 가능한 근거가 부족합니다. 증상만으로 셋업 원인을 확정하지 않습니다.</p>}</div>
      <div className="rounded-2xl border border-warning/25 bg-warning/10 p-4"><h3 className="font-semibold text-warning">검증할 가설</h3><p className="mt-3 text-sm leading-relaxed text-foreground">{result.hypothesis || '관측과 드라이버 느낌을 확인할 추가 주행이 필요합니다.'}</p><p className="mt-3 text-sm leading-relaxed text-foreground">다음 런: {result.next_run || next.primary_instruction || '동일 조건에서 증상을 다시 확인하세요.'}</p></div>
    </div>}
    <div className="rounded-2xl border border-accent/25 bg-accent/10 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold text-foreground">{next.mode === 'SETUP_EXPLORATION' ? '한 가지 변경으로 체감 확인' : '한 가지 변경으로 A/B 검증'}</h3>
        <Button variant="outline" type="button" onClick={() => onNavigate('ab-manager')} className=" border  px-3 py-2 text-sm  ">A/B 비교 열기</Button>
      </div>
      {setup ? <><p className="mt-3 font-bold text-foreground">{setup.label}</p><p className="mt-1 font-mono text-warning">{formatSetupOriginal(setup)} → {target}</p><p className="mt-2 text-sm text-muted-foreground">이 변경만 적용하고 나머지 셋업·연료·주행 조건은 고정하세요.</p></> : <p className="mt-3 text-sm text-muted-foreground">검증 가능한 수치 셋업 후보가 아직 없습니다. 증상과 코너 입력부터 확인하세요.</p>}
      {setup && <Button variant="default" type="button" onClick={() => onNavigate('setup-decision')} className="coach-primary-action mt-3 px-4 py-3 text-sm">추천 후보 자세히 보기</Button>}
      <p className="mt-3 text-sm text-foreground">{next.required_clean_corners > 0 ? `관측할 깨끗한 코너 최소 ${next.required_clean_corners}개 · 체감은 1~2랩으로 확인` : `${next.mode === 'SETUP_EXPLORATION' ? '체감 관찰' : '최소'} 유효랩 ${next.required_valid_laps ?? '--'} / 권장 ${next.recommended_valid_laps ?? next.required_valid_laps ?? '--'}`} 
        {next.mode === 'SETUP_EXPLORATION' && <span className="mt-1 block text-muted-foreground">A/B 효과 확인은 각 셋업 유효랩 {next.effect_confirmation_minimum_laps}개부터입니다.</span>}
      </p>
      {next.success_criteria?.length > 0 && <div className="mt-2 flex flex-col gap-1 text-sm text-muted-foreground">{next.success_criteria.map((rule) => <p key={rule}>• {rule}</p>)}</div>}
    </div>

  </div>;
}

export default function App() {
  const [hoverHelpEnabled, toggleHoverHelp] = useHoverHelpSetting();
  const workflow = useTrialWorkflow();
  const { activeTrial, history: trialHistory, readDraft, saveDraft, patchTrial, startTrial, completeTrial, savePreferences } = workflow;
  const bootDraft = useRef(readDraft(workflow.preferences.selectedFile));
  const [activeTab, setActiveTab] = useState('summary');
  const [requestedCorner, setRequestedCorner] = useState(null);
  const [controlsExpanded, setControlsExpanded] = useState(false);
  const selectTab = useCallback((tab) => {
    setActiveTab(tab);
    window.requestAnimationFrame(() => document.getElementById('coach-content')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }, []);
  const [feedbackPhase, setFeedbackPhase] = useState(bootDraft.current.feedbackPhase || '');
  const [feedbackSpeed, setFeedbackSpeed] = useState(bootDraft.current.feedbackSpeed || '');
  const [feedbackSymptom, setFeedbackSymptom] = useState(bootDraft.current.feedbackSymptom || '');
  const [setupMode, setSetupMode] = useState(bootDraft.current.setupMode || 'EXPLORE');
  const [goal, setGoal] = useState(activeTrial?.goal || workflow.preferences.goal || 'COMFORT');
  const [referenceMode, setReferenceMode] = useState(activeTrial?.reference_mode || bootDraft.current.referenceMode || 'AGGREGATE');
  const [sessionMode, setSessionMode] = useState('BASELINE_3LAP');
  const [pitRule, setPitRule] = useState('NOT_APPLICABLE');
  const [status, setStatus] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  // Laps picked by lap time for the per-lap statistics (time use, corner consistency); a new file starts with all.
  const [lapRange, setLapRange] = useState(null);
  const analysedFileName = analysis?.file?.name;
  useEffect(() => { setLapRange(null); }, [analysedFileName]);
  const pickedLaps = useMemo(() => lapsInRange(analysis?.laps || [], lapRange), [analysis, lapRange]);
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState('');
  const [targetLap, setTargetLap] = useState('');
  const [referenceLap, setReferenceLap] = useState('');
  const [targetLapManual, setTargetLapManual] = useState(false);
  const [referenceLapManual, setReferenceLapManual] = useState(false);
  const [userFeedback, setUserFeedback] = useState(bootDraft.current.userFeedback || '');
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState('');
  const [memoryStatus, setMemoryStatus] = useState(null);
  const [memorySessions, setMemorySessions] = useState([]);
  const [externalReferenceStatus, setExternalReferenceStatus] = useState(null);
  const [externalReferences, setExternalReferences] = useState([]);
  const [selectedExternalReferences, setSelectedExternalReferences] = useState(activeTrial?.reference_files || bootDraft.current.referenceFiles || []);
  const [garage61Collector, setGarage61Collector] = useState(null);
  const [garage61CollectorLoading, setGarage61CollectorLoading] = useState(false);
  const [garage61ApiStatus, setGarage61ApiStatus] = useState(null);
  const [modeChangePrompt, setModeChangePrompt] = useState(null);
  const [fileIndex, setFileIndex] = useState(null);
  const [pendingReferences, setPendingReferences] = useState(null);
  const [newFileNotice, setNewFileNotice] = useState('');
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [analysisSnapshot, setAnalysisSnapshot] = useState(null);
  const signatureRef = useRef(null);
  const waitingFileRef = useRef('');
  const filesRef = useRef([]);
  const selectedFileRef = useRef('');
  const collectorHandledJobRef = useRef('');
  const collectorAutoContextRef = useRef('');
  const collectorTerminalRetryRef = useRef({ jobId: '', attempts: 0 });
  const runAnalysisRef = useRef(null);
  const analysisRequestRef = useRef({ id: 0, controller: null });
  const detailRequestRef = useRef({ id: 0, controller: null });
  const activeTrialRef = useRef(activeTrial);
  activeTrialRef.current = activeTrial;
  const restoredFileRef = useRef(workflow.preferences.selectedFile || activeTrial?.baseline_file || '');
  const draftFileRef = useRef(restoredFileRef.current);
  const referenceModeRef = useRef(referenceMode);
  referenceModeRef.current = referenceMode;

  const restoreFileDraft = useCallback((filename) => {
    const draft = readDraft(filename);
    draftFileRef.current = filename;
    setFeedbackPhase(draft.feedbackPhase || ''); setFeedbackSpeed(draft.feedbackSpeed || '');
    setFeedbackSymptom(draft.feedbackSymptom || ''); setUserFeedback(draft.userFeedback || '');
    setSetupMode(draft.setupMode || 'EXPLORE');
    setReferenceMode(activeTrialRef.current?.reference_mode || draft.referenceMode || 'AGGREGATE');
    setSelectedExternalReferences(activeTrialRef.current?.reference_files || draft.referenceFiles || []);
  }, [readDraft]);

  useEffect(() => {
    if (!selectedFile || draftFileRef.current !== selectedFile) return;
    saveDraft(selectedFile, { feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, userFeedback, referenceMode, referenceFiles: selectedExternalReferences });
    savePreferences({ selectedFile, goal });
  }, [feedbackPhase, feedbackSpeed, feedbackSymptom, goal, referenceMode, saveDraft, savePreferences, selectedExternalReferences, selectedFile, setupMode, userFeedback]);

  const navigateToSection = useCallback((id, cornerId = null) => {
    if (id === 'corner-inputs' && cornerId !== null && cornerId !== undefined) setRequestedCorner(typeof cornerId === 'object' ? cornerId : { id: cornerId });
    const tab = ({ priorities: 'compare', comparison: 'compare', 'lap-detail': 'compare', 'external-reference': 'compare',
      'corner-inputs': 'compare', decision: 'setup', 'setup-decision': 'setup', 'ab-manager': 'setup', briefing: 'setup',
      personalization: 'history', settings: 'settings', summary: 'summary', 'user-guide': 'guide', 'closed-loop-coach': 'summary', 'live-coach': 'live', 'voice-review': 'summary' })[id] || 'summary';
    setActiveTab(tab);
    window.dispatchEvent(new CustomEvent('coach-reveal-section', { detail: id }));
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
  }, []);

  useEffect(() => {
    const navigate = (event) => navigateToSection(event.detail);
    window.addEventListener('coach-navigate', navigate);
    return () => window.removeEventListener('coach-navigate', navigate);
  }, [navigateToSection]);

  useEffect(() => () => { analysisRequestRef.current.controller?.abort(); detailRequestRef.current.controller?.abort(); }, []);

  useEffect(() => {
    selectedFileRef.current = selectedFile;
  }, [selectedFile]);

  useEffect(() => {
    if (sessionMode === 'ENDURANCE') {
      if (pitRule === 'NOT_APPLICABLE') setPitRule('CONCURRENT');
    } else {
      setPitRule('NOT_APPLICABLE');
    }
  }, [pitRule, sessionMode]);

  const loadFiles = useCallback(async () => {
    const payload = await requestJson('/api/files?limit=100');
    const nextFiles = payload.files || [];
    filesRef.current = nextFiles;
    setFiles(nextFiles);
    setFileIndex({ index_status: payload.index_status, pending_count: payload.pending_count, index_signature: payload.index_signature });
    const restored = nextFiles.find((file) => file.name === restoredFileRef.current && file.analyzable !== false);
    const firstAnalyzable = restored || nextFiles.find((file) => file.analyzable === true);
    if (!selectedFileRef.current && firstAnalyzable) {
      restoreFileDraft(firstAnalyzable.name);
      setSelectedFile(firstAnalyzable.name);
      selectedFileRef.current = firstAnalyzable.name;
    }
    return nextFiles;
  }, [restoreFileDraft]);

  const loadStatus = useCallback(async () => {
    const payload = await requestJson('/api/status');
    setStatus(payload);
    const telemetry = payload.telemetry;
    if (telemetry?.file_name && telemetry.analyzable !== false && !selectedFileRef.current && !restoredFileRef.current) {
      restoreFileDraft(telemetry.file_name);
      setSelectedFile(telemetry.file_name);
      selectedFileRef.current = telemetry.file_name;
    }
    return payload;
  }, [restoreFileDraft]);

  const loadMemory = useCallback(async () => {
    try {
      const [statusPayload, sessionsPayload] = await Promise.all([
        requestJson('/api/memory/status'),
        requestJson('/api/memory/sessions?limit=100'),
      ]);
      setMemoryStatus(statusPayload);
      setMemorySessions(sessionsPayload.sessions || []);
      return { status: statusPayload, sessions: sessionsPayload.sessions || [] };
    } catch (memoryError) {
      const fallback = {
        enabled: false,
        path: null,
        error: memoryError.message,
      };
      setMemoryStatus(fallback);
      return { status: fallback, sessions: [] };
    }
  }, []);

  const loadGarage61ApiStatus = useCallback(async () => {
    try {
      const payload = await requestJson('/api/garage61/api/status');
      setGarage61ApiStatus(payload.api || null);
      return payload.api || null;
    } catch (apiError) {
      const fallback = { connected: false, configured: false, message: apiError.message };
      setGarage61ApiStatus(fallback);
      return fallback;
    }
  }, []);

  const loadExternalReferences = useCallback(async (filename = null, autoSelect = true) => {
    const effectiveFilename = filename || selectedFileRef.current || '';
    if (!effectiveFilename) {
      const empty = { files: [], recommended_selection: [], total_compatible: 0 };
      setExternalReferenceStatus(empty);
      setExternalReferences([]);
      if (autoSelect) setSelectedExternalReferences([]);
      return empty;
    }
    try {
      const payload = await requestJson(`/api/external-references?filename=${encodeURIComponent(effectiveFilename)}`);
      if (selectedFileRef.current !== effectiveFilename) return payload;
      const nextFiles = payload.files || [];
      let appliedSelection;
      setExternalReferenceStatus(payload);
      setExternalReferences(nextFiles);
      if (payload.collector_health) {
        setGarage61Collector((current) => ({
          ...(current || {}),
          health: payload.collector_health,
          job: current?.job || null,
        }));
      }
      if (activeTrialRef.current) {
        appliedSelection = activeTrialRef.current.reference_files;
        setSelectedExternalReferences(activeTrialRef.current.reference_files);
        const nextRecommended = (payload.recommended_selection || []).slice(0, payload.display_limit || payload.max_selection || 9);
        if (JSON.stringify(nextRecommended) !== JSON.stringify(activeTrialRef.current.reference_files)) setPendingReferences(nextRecommended);
      } else if (autoSelect) {
        const draftReferences = readDraft(effectiveFilename).referenceFiles;
        const available = new Set(nextFiles.filter((item) => item.usable !== false).map((item) => item.name));
        const restoredReferences = Array.isArray(draftReferences) ? draftReferences.filter((name) => available.has(name)) : null;
        appliedSelection = (restoredReferences?.length ? restoredReferences : payload.recommended_selection || []).slice(0, payload.display_limit || payload.max_selection || 9);
        setSelectedExternalReferences(appliedSelection);
      } else {
        const available = new Set(nextFiles.filter((item) => item.usable !== false).map((item) => item.name));
        setSelectedExternalReferences((current) => current.filter((name) => available.has(name)).slice(0, payload.display_limit || payload.max_selection || 9));
      }
      return { ...payload, applied_selection: appliedSelection };
    } catch (referenceError) {
      const fallback = {
        directory: null,
        files: [],
        recommended_selection: [],
        total_compatible: 0,
        error: referenceError.message,
        max_selection: 12,
        recommended_cohort_size: 5,
        minimum_for_setup_conclusion: 2,
        strong_consensus_count: 3,
      };
      if (selectedFileRef.current !== effectiveFilename) return fallback;
      setExternalReferenceStatus(fallback);
      setExternalReferences([]);
      if (autoSelect && !activeTrialRef.current) setSelectedExternalReferences([]);
      return fallback;
    }
  }, [readDraft]);

  const loadGarage61CollectorStatus = useCallback(async (filename = null) => {
    const effectiveFilename = filename || selectedFileRef.current || '';
    if (!effectiveFilename) {
      const empty = { health: null, job: null };
      setGarage61Collector(empty);
      return empty;
    }
    try {
      const payload = await requestJson(`/api/garage61/collector/status?filename=${encodeURIComponent(effectiveFilename)}`);
      if (selectedFileRef.current !== effectiveFilename) return payload;
      setGarage61Collector(payload);
      return payload;
    } catch (collectorError) {
      const fallback = {
        health: null,
        job: { state: 'FAILED', message: collectorError.message, busy: false },
      };
      if (selectedFileRef.current !== effectiveFilename) return fallback;
      setGarage61Collector(fallback);
      return fallback;
    }
  }, []);

  const startGarage61Collection = useCallback(async (filename = null, force = false) => {
    const effectiveFilename = filename || selectedFileRef.current || '';
    if (!effectiveFilename) return null;
    setGarage61CollectorLoading(true);
    try {
      const payload = await requestJson('/api/garage61/collector/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: effectiveFilename,
          force: Boolean(force),
          target_count: 8,
          headless: false,
          collector_mode: 'AUTO',
        }),
      });
      setGarage61Collector(payload);
      return payload;
    } catch (collectorError) {
      setGarage61Collector((current) => ({
        ...(current || {}),
        health: current?.health || null,
        job: { state: 'FAILED', message: collectorError.message, busy: false },
      }));
      return null;
    } finally {
      setGarage61CollectorLoading(false);
    }
  }, []);

  const cancelGarage61Collection = useCallback(async () => {
    try {
      const payload = await requestJson('/api/garage61/collector/cancel', { method: 'POST' });
      setGarage61Collector((current) => ({
        ...(current || {}),
        health: current?.health || null,
        job: payload.job || null,
      }));
      return payload;
    } catch (collectorError) {
      setError(collectorError.message);
      return null;
    }
  }, []);

  const maybeStartGarage61Collection = useCallback(async (filename, referencePayload = null) => {
    if (!filename) return null;
    const health = referencePayload?.collector_health || (await loadGarage61CollectorStatus(filename))?.health;
    if (!health?.enabled || !health?.needs_collection) return null;
    const contextKey = health?.context?.context_key || `${filename}:unknown`;
    if (collectorAutoContextRef.current === contextKey) return null;
    collectorAutoContextRef.current = contextKey;
    const result = await startGarage61Collection(filename, false);
    const state = result?.job?.state;
    const returnedContext = result?.job?.context_key;
    if (state === 'FAILED' || (result?.job?.busy && returnedContext && returnedContext !== contextKey)) {
      collectorAutoContextRef.current = '';
    }
    return result;
  }, [loadGarage61CollectorStatus, startGarage61Collection]);

  const runAnalysis = useCallback(
    async ({
      includeAI = false,
      quiet = false,
      filename = null,
      sessionModeOverride = null,
      pitRuleOverride = null,
      persistMemory = null,
      replaceMemoryMode = false,
      externalReferencesOverride = null,
      referenceModeOverride = null,
      forceAutoLaps = false,
      targetLapOverride = undefined,
      referenceLapOverride = undefined,
    } = {}) => {
      const effectiveFilename = filename || selectedFileRef.current || null;
      if (!effectiveFilename || effectiveFilename !== selectedFileRef.current) return null;
      const knownFile = filesRef.current.find((item) => item.name === effectiveFilename);
      if (knownFile?.analyzable === false) { setError(knownFile.eligibility_reason || knownFile.reason || '이 파일은 분석할 유효랩이 없습니다.'); return null; }
      analysisRequestRef.current.controller?.abort();
      const requestId = analysisRequestRef.current.id + 1;
      const controller = new AbortController();
      analysisRequestRef.current = { id: requestId, controller };
      const isCurrent = () => !controller.signal.aborted && analysisRequestRef.current.id === requestId && selectedFileRef.current === effectiveFilename;
      if (!quiet) setLoading(true);
      setError('');
      try {
        const fileDraft = forceAutoLaps && filename ? readDraft(effectiveFilename) : null;
        const effectiveFeedback = fileDraft ? {
          feedbackPhase: fileDraft.feedbackPhase || '', feedbackSpeed: fileDraft.feedbackSpeed || '',
          feedbackSymptom: fileDraft.feedbackSymptom || '', setupMode: fileDraft.setupMode || 'EXPLORE', userFeedback: fileDraft.userFeedback || '',
        } : { feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, userFeedback };
        const effectiveSessionMode = sessionModeOverride || sessionMode;
        const effectivePitRule = effectiveSessionMode === 'ENDURANCE'
          ? (pitRuleOverride || pitRule || 'CONCURRENT')
          : 'NOT_APPLICABLE';
        const requestedReferences = externalReferencesOverride === null
          ? selectedExternalReferences
          : externalReferencesOverride;
        const chosenReferenceMode = activeTrialRef.current?.reference_mode || referenceModeOverride || fileDraft?.referenceMode || referenceModeRef.current;
        const effectiveMode = chosenReferenceMode !== 'LOCAL' && requestedReferences.length ? chosenReferenceMode : 'LOCAL';
        const effectiveExternalReferences = effectiveMode === 'LOCAL' ? [] : effectiveMode === 'SINGLE' ? requestedReferences.slice(0, 1) : requestedReferences;
        if (chosenReferenceMode !== effectiveMode) setReferenceMode(effectiveMode);
        const fileEntry = filesRef.current.find((item) => item.name === effectiveFilename);
        const storedModes = storedModesForFile(fileEntry);
        const shouldPersist = persistMemory === null
          ? storedModes.includes(effectiveSessionMode)
          : Boolean(persistMemory);
        const requestedTargetLap = targetLapOverride !== undefined ? targetLapOverride : forceAutoLaps
          ? null
          : (targetLapManual && targetLap ? Number(targetLap) : null);
        const requestedReferenceLap = referenceLapOverride !== undefined ? referenceLapOverride : forceAutoLaps
          ? null
          : (referenceLapManual && referenceLap ? Number(referenceLap) : null);
        const snapshot = { filename: effectiveFilename, ...effectiveFeedback,
          referenceMode: effectiveMode, references: effectiveExternalReferences, targetLap: requestedTargetLap, referenceLap: requestedReferenceLap };

        const payload = await requestJson('/api/analyze', {
          signal: controller.signal,
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_mode: effectiveSessionMode,
            pit_rule: effectivePitRule,
            user_feedback: effectiveFeedback.userFeedback,
            feedback_phase: effectiveFeedback.feedbackPhase || null,
            feedback_speed: effectiveFeedback.feedbackSpeed || null,
            feedback_symptom: effectiveFeedback.feedbackSymptom || null,
            setup_mode: effectiveFeedback.setupMode,
            target_lap: requestedTargetLap,
            reference_lap: requestedReferenceLap,
            include_ai: includeAI,
            filename: effectiveFilename,
            persist_memory: shouldPersist,
            replace_existing_mode: Boolean(replaceMemoryMode),
            external_reference_files: effectiveExternalReferences,
          }),
        });
        if (!isCurrent()) return null;
        setAnalysis(payload);
        setAnalysisSnapshot(snapshot);
        setDetail(null);
        detailRequestRef.current.controller?.abort();

        const validLaps = (payload.laps || []).filter((lap) => lap.status === 'VALID');
        const comparison = payload.comparison;
        if (comparison) {
          setTargetLap(String(comparison.target_lap));
          if (comparison.reference_lap !== null && comparison.reference_lap !== undefined) {
            setReferenceLap(String(comparison.reference_lap));
          } else {
            setReferenceLap('');
          }
        } else if (validLaps.length) {
          const fastestValid = [...validLaps].sort((a, b) => Number(a.lap_time_s || Infinity) - Number(b.lap_time_s || Infinity))[0];
          if (fastestValid) setTargetLap(String(fastestValid.lap_number));
        }

        await loadFiles();
        await loadMemory();
        return payload;
      } catch (requestError) {
        if (isCurrent() && requestError.name !== 'AbortError') setError(requestError.message);
        return null;
      } finally {
        if (isCurrent()) setLoading(false);
      }
    },
    [feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, loadFiles, loadMemory, pitRule, readDraft, referenceLap, referenceLapManual, selectedExternalReferences, sessionMode, targetLap, targetLapManual, userFeedback]
  );

  const handleExperimentStored = useCallback(async () => {
    await loadMemory();
    await runAnalysis({ includeAI: false, quiet: true });
  }, [loadMemory, runAnalysis]);

  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      setInitialLoading(true);
      try {
        const [filePayload, statusPayload] = await Promise.all([loadFiles(), loadStatus()]);
        void loadMemory();
        void loadGarage61ApiStatus();
        if (cancelled) return;
        const firstFile = selectedFileRef.current || filePayload.find((file) => file.analyzable !== false)?.name || (statusPayload.telemetry?.analyzable !== false ? statusPayload.telemetry?.file_name : null);
        if (firstFile) {
          const firstEntry = filePayload.find((item) => item.name === firstFile);
          if (!selectedFileRef.current) { selectedFileRef.current = firstFile; setSelectedFile(firstFile); restoreFileDraft(firstFile); }
          const initialMode = initialModeForFile(firstEntry);
          const initialPitRule = initialMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
          setSessionMode(initialMode);
          setPitRule(initialPitRule);
          const referencePayload = await loadExternalReferences(firstFile, true);
          await runAnalysisRef.current?.({
            includeAI: false,
            quiet: true,
            filename: firstFile,
            sessionModeOverride: initialMode,
            pitRuleOverride: initialPitRule,
            persistMemory: false,
            forceAutoLaps: true,
            externalReferencesOverride: (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9),
          });
          void maybeStartGarage61Collection(firstFile, referencePayload);
        }
      } catch (initializeError) {
        if (!cancelled) setError(initializeError.message);
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    };
    initialize();
    return () => {
      cancelled = true;
    };
  }, [loadExternalReferences, loadFiles, loadGarage61ApiStatus, loadMemory, loadStatus, maybeStartGarage61Collection, restoreFileDraft]);

  useEffect(() => {
    runAnalysisRef.current = runAnalysis;
  }, [runAnalysis]);

  useEffect(() => {
    let eventSource = null;
    let polling = null;

    const handleStatus = async (payload) => {
      const telemetry = payload?.telemetry;
      if (!telemetry) return;
      setStatus(payload);
      if (telemetry.retryable) {
        waitingFileRef.current = telemetry.file_name;
        await loadFiles();
        return;
      }
      const unlocked = waitingFileRef.current === telemetry.file_name;
      waitingFileRef.current = '';
      const signature = JSON.stringify(telemetry.file_signature || []);
      if (unlocked || (signatureRef.current ? signature !== signatureRef.current : telemetry.file_name !== selectedFileRef.current)) {
        signatureRef.current = signature;
        if (activeTrialRef.current) {
          setNewFileNotice(telemetry.file_name);
          await loadFiles();
          return;
        }
        analysisRequestRef.current.controller?.abort();
        setAnalysis(null);
        setLoading(true);
        setSelectedFile(telemetry.file_name);
        selectedFileRef.current = telemetry.file_name;
        restoreFileDraft(telemetry.file_name);
        const refreshedFiles = await loadFiles();
        const fileEntry = refreshedFiles.find((item) => item.name === telemetry.file_name);
        if (selectedFileRef.current !== telemetry.file_name) return;
        if (fileEntry?.analyzable === false) { setLoading(false); setError(fileEntry.reason || '새 주행의 유효랩이 부족합니다.'); return; }
        const nextMode = initialModeForFile(fileEntry);
        const nextPitRule = nextMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
        setSessionMode(nextMode);
        setPitRule(nextPitRule);
        setTargetLap('');
        setReferenceLap('');
        setTargetLapManual(false);
        setReferenceLapManual(false);
        const referencePayload = await loadExternalReferences(telemetry.file_name, true);
        if (selectedFileRef.current !== telemetry.file_name) return;
        const runner = runAnalysisRef.current;
        if (runner) {
          await runner({
            includeAI: false,
            quiet: true,
            filename: telemetry.file_name,
            sessionModeOverride: nextMode,
            pitRuleOverride: nextPitRule,
            persistMemory: false,
            forceAutoLaps: true,
            externalReferencesOverride: (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9),
          });
        }
        void maybeStartGarage61Collection(telemetry.file_name, referencePayload);
      } else if (!signatureRef.current) {
        signatureRef.current = signature;
      }
    };

    try {
      eventSource = new EventSource(`${API_BASE}/api/events`);
      eventSource.onmessage = (event) => {
        try {
          void handleStatus(JSON.parse(event.data)).catch(() => {});
        } catch {
          // Ignore malformed keepalive/event payloads.
        }
      };
      eventSource.onerror = () => {
        eventSource?.close();
        if (!polling) {
          polling = window.setInterval(async () => {
            try {
              const payload = await loadStatus();
              await handleStatus(payload);
            } catch {
              // Keep the last valid UI state during a temporary backend outage.
            }
          }, 10000);
        }
      };
    } catch {
      polling = window.setInterval(loadStatus, 10000);
    }

    return () => {
      eventSource?.close();
      if (polling) window.clearInterval(polling);
    };
  }, [loadExternalReferences, loadFiles, loadStatus, maybeStartGarage61Collection, restoreFileDraft, sessionMode]);

  useEffect(() => {
    if (fileIndex?.index_status !== 'SCANNING') return undefined;
    let cancelled = false; let inFlight = false;
    const timer = window.setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const previous = selectedFileRef.current;
        await loadFiles();
        if (!cancelled && !previous && selectedFileRef.current) {
          const filename = selectedFileRef.current;
          const references = await loadExternalReferences(filename, true);
          await runAnalysisRef.current?.({ filename, persistMemory: false, forceAutoLaps: true,
            externalReferencesOverride: references.applied_selection || references.recommended_selection || [] });
        }
      } catch { /* Metadata polling continues while the index has work. */ }
      finally { inFlight = false; }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [fileIndex?.index_status, fileIndex?.pending_count, loadExternalReferences, loadFiles]);

  useEffect(() => {
    if (!garage61Collector?.job?.job_id) return undefined;

    let cancelled = false;
    let stopped = false;
    let inFlight = false;
    let timer = null;

    const stopPolling = () => {
      stopped = true;
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = (delayMs) => {
      if (cancelled || stopped) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(poll, delayMs);
    };

    const poll = async () => {
      if (cancelled || stopped || inFlight) return;
      inFlight = true;
      let nextDelayMs = 5000;

      try {
        const filename = selectedFileRef.current;
        if (!filename) {
          stopPolling();
          return;
        }

        const payload = await loadGarage61CollectorStatus(filename);
        const job = payload?.job;
        if (!job?.job_id) {
          nextDelayMs = 5000;
          return;
        }

        if (job.busy) {
          nextDelayMs = 5000;
          return;
        }

        const terminal = ['COMPLETE', 'PARTIAL', 'FAILED', 'CANCELLED', 'SKIPPED_CACHE_SUFFICIENT', 'SKIPPED_COOLDOWN'].includes(job.state);
        if (!terminal) {
          nextDelayMs = 5000;
          return;
        }

        if (collectorHandledJobRef.current === job.job_id) {
          stopPolling();
          return;
        }

        collectorAutoContextRef.current = '';
        const currentContext = payload?.health?.context?.context_key;
        if (job.context_key && currentContext && job.context_key !== currentContext) {
          collectorHandledJobRef.current = job.job_id;
          stopPolling();
          void maybeStartGarage61Collection(filename);
          return;
        }

        if (!['COMPLETE', 'PARTIAL', 'SKIPPED_CACHE_SUFFICIENT', 'SKIPPED_COOLDOWN'].includes(job.state)) {
          collectorHandledJobRef.current = job.job_id;
          stopPolling();
          return;
        }

        // IMPORTANT: do not auto-select here. Updating selected references changes
        // runAnalysis's callback identity. In v2.7.0 that restarted this effect
        // before terminal handling could finish, producing an endless status /
        // external-reference request loop and preventing the final /api/analyze.
        const referencePayload = await loadExternalReferences(filename, false);
        if (cancelled || selectedFileRef.current !== filename) {
          stopPolling();
          return;
        }

        const expectedCached = Number(
          job.context_csv_count ?? job.cached_csv_count ?? job.downloaded_count ?? 0
        );
        const compatible = Number(referencePayload?.total_compatible ?? 0);
        if (collectorTerminalRetryRef.current.jobId !== job.job_id) {
          collectorTerminalRetryRef.current = { jobId: job.job_id, attempts: 0 };
        }
        if (expectedCached > 0 && compatible === 0 && collectorTerminalRetryRef.current.attempts < 6) {
          collectorTerminalRetryRef.current.attempts += 1;
          nextDelayMs = Math.min(3000 + collectorTerminalRetryRef.current.attempts * 1000, 8000);
          return;
        }

        const recommended = (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(
          0,
          referencePayload.display_limit || referencePayload.max_selection || 9
        );
        setPendingReferences(recommended);
        collectorHandledJobRef.current = job.job_id;
        stopPolling();
        return;


      } catch {
        // Garage61 polling must never interrupt the local telemetry analysis UI.
        nextDelayMs = 5000;
      } finally {
        inFlight = false;
        if (!cancelled && !stopped) schedule(nextDelayMs);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [garage61Collector?.job?.job_id, garage61Collector?.job?.busy, garage61Collector?.job?.state, loadExternalReferences, loadGarage61CollectorStatus, maybeStartGarage61Collection, pitRule, sessionMode]);

  const handleTargetLapChange = (value) => {
    setTargetLap(value);
    setTargetLapManual(true);
    if (referenceLap === value) {
      setReferenceLap('');
      setReferenceLapManual(false);
    }
  };

  const handleReferenceLapChange = (value) => {
    setReferenceLap(value);
    setReferenceLapManual(true);
    if (targetLap === value) {
      setTargetLap('');
      setTargetLapManual(false);
    }
  };

  const resetAutoLapSelection = async () => {
    setTargetLap('');
    setReferenceLap('');
    setTargetLapManual(false);
    setReferenceLapManual(false);
    await runAnalysis({ includeAI: false, forceAutoLaps: true });
  };

  const handleFileChange = async (filename) => {
    const fileEntry = files.find((item) => item.name === filename);
    if (fileEntry?.analyzable === false) return;
    analysisRequestRef.current.controller?.abort();
    setAnalysis(null);
    setLoading(true);
    const nextMode = initialModeForFile(fileEntry);
    const nextPitRule = nextMode === 'ENDURANCE' ? 'CONCURRENT' : 'NOT_APPLICABLE';
    setModeChangePrompt(null);
    setSelectedFile(filename);
    selectedFileRef.current = filename;
    restoreFileDraft(filename);
    setNewFileNotice('');
    setSessionMode(nextMode);
    setPitRule(nextPitRule);
    setTargetLap('');
    setReferenceLap('');
    setTargetLapManual(false);
    setReferenceLapManual(false);
    const referencePayload = await loadExternalReferences(filename, true);
    if (selectedFileRef.current !== filename) return;
    await runAnalysis({
      includeAI: false,
      filename,
      sessionModeOverride: nextMode,
      pitRuleOverride: nextPitRule,
      persistMemory: false,
      forceAutoLaps: true,
      externalReferencesOverride: (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(0, referencePayload.display_limit || referencePayload.max_selection || 9),
    });
    void maybeStartGarage61Collection(filename, referencePayload);
  };

  const commitSessionMode = async (nextMode, nextPitRule, replaceExistingMode = false) => {
    setSessionMode(nextMode);
    setPitRule(nextPitRule);
    setTargetLap('');
    setReferenceLap('');
    setTargetLapManual(false);
    setReferenceLapManual(false);
    if (selectedFileRef.current) {
      await runAnalysis({
        includeAI: false,
        sessionModeOverride: nextMode,
        pitRuleOverride: nextPitRule,
        persistMemory: true,
        replaceMemoryMode: replaceExistingMode,
        forceAutoLaps: true,
      });
    }
  };

  const handleSessionModeChange = async (nextMode) => {
    if (!selectedFileRef.current || loading) return;
    const fileEntry = files.find((item) => item.name === selectedFileRef.current);
    const currentModes = storedModesForFile(fileEntry);
    const nextPitRule = nextMode === 'ENDURANCE'
      ? (pitRule === 'NOT_APPLICABLE' ? 'CONCURRENT' : pitRule)
      : 'NOT_APPLICABLE';
    const requiresReplacement = currentModes.length > 1 || (
      currentModes.length === 1 && !currentModes.includes(nextMode)
    );

    if (requiresReplacement) {
      setModeChangePrompt({
        filename: selectedFileRef.current,
        currentModes,
        nextMode,
        nextPitRule,
        returnFocus: document.activeElement,
      });
      return;
    }

    await commitSessionMode(nextMode, nextPitRule, false);
  };

  const confirmSessionModeChange = async () => {
    const prompt = modeChangePrompt;
    if (!prompt) return;
    setModeChangePrompt(null);
    await commitSessionMode(prompt.nextMode, prompt.nextPitRule, true);
  };

  const toggleExternalReference = (filename) => {
    if (activeTrial) return;
    if (externalReferences.find((file) => file.name === filename)?.usable === false) return;
    const maxSelection = Number(externalReferenceStatus?.display_limit || externalReferenceStatus?.max_selection || 9);
    setSelectedExternalReferences((current) => {
      if (current.includes(filename)) return current.filter((name) => name !== filename);
      if (current.length >= maxSelection) return current;
      return [...current, filename];
    });
  };

  const refreshExternalReferences = async () => {
    const filename = selectedFileRef.current;
    const payload = await loadExternalReferences(filename, false);
    if (payload.error) {
      setError(payload.error);
      return payload;
    }
    const recommended = (payload.recommended_selection || []).slice(0, payload.display_limit || payload.max_selection || 9);
    if (filename && recommended.length) setPendingReferences(recommended);
    return payload;
  };

  const applyExternalReferences = async () => {
    await runAnalysis({
      includeAI: false,
      externalReferencesOverride: selectedExternalReferences,
    });
  };

  const session = analysis?.session;
  const quality = analysis?.data_quality || status?.telemetry?.data_quality;
  const comparison = analysis?.comparison;
  const classification = analysis?.performance?.classification;
  const externalReferenceAnalysis = analysis?.external_reference;
  const trace = analysis?.visualizations?.trace || [];
  const pathData = analysis?.visualizations?.track_path || [];
  const wantsExternalComparison = Boolean(analysisSnapshot && analysisSnapshot.referenceMode !== 'LOCAL');
  const hasExternalTrace = wantsExternalComparison && externalReferenceAnalysis?.status === 'READY';
  const comparisonTrace = wantsExternalComparison ? externalReferenceAnalysis?.visualizations?.trace || [] : trace;
  const comparisonCorners = wantsExternalComparison ? externalReferenceAnalysis?.corners || [] : analysis?.performance?.corners?.length ? analysis.performance.corners : analysis?.coaching?.clean_segment_corners || [];
  const comparisonPath = wantsExternalComparison && externalReferenceAnalysis?.visualizations?.track_path?.length ? externalReferenceAnalysis.visualizations.track_path : pathData;
  const selectedFileEntry = useMemo(
    () => files.find((item) => item.name === selectedFile),
    [files, selectedFile]
  );
  const selectedStoredModes = storedModesForFile(selectedFileEntry);
  const selectedAssignedMode = assignedModeForFile(selectedFileEntry);
  const cleanSectionOnly = Boolean(analysis && !quality?.valid_laps && quality?.coaching_ready && quality?.analysis_scope === 'CLEAN_SEGMENTS_ONLY');
  const desiredReferences = referenceMode === 'LOCAL' ? [] : referenceMode === 'SINGLE' ? selectedExternalReferences.slice(0, 1) : selectedExternalReferences;
  const desiredContext = { filename: selectedFile, feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode, userFeedback,
    referenceMode, references: desiredReferences, targetLap: targetLapManual && targetLap ? Number(targetLap) : null,
    referenceLap: referenceLapManual && referenceLap ? Number(referenceLap) : null };
  const analysisIsCurrent = Boolean(analysisSnapshot && analysisContextKey(analysisSnapshot) === analysisContextKey(desiredContext));
  const canStartTrial = Boolean(analysis && analysisIsCurrent && !loading && !activeTrial);
  const currentContext = { ...(selectedFileEntry || {}), ...(session || {}), name: selectedFile };
  const canAttachTrial = Boolean(activeTrial && selectedFile !== activeTrial.baseline_file && selectedFileEntry?.analyzable !== false
    && analysis && sameVehicleAndTrack(activeTrial.context, currentContext));
  const displayComparison = cleanSectionOnly ? null : hasExternalTrace ? { target_lap: comparison?.target_lap ?? analysis?.lap_selection?.target_lap,
    reference_lap: analysisSnapshot?.referenceMode === 'SINGLE' ? '선택한 Garage61 랩' : 'Garage61 중앙값',
    target_time: externalReferenceAnalysis.aggregate?.target_time, reference_time: externalReferenceAnalysis.aggregate?.reference_median_time,
    total_delta_s: externalReferenceAnalysis.aggregate?.gap_to_reference_median_s } : wantsExternalComparison ? null : comparison;
  const displayPriorities = wantsExternalComparison ? (externalReferenceAnalysis?.top_losses || []).slice(0, 3).map((corner, index) => ({
    rank: index + 1, corner: corner.label || corner.id, issue_type: corner.issue_type, distance_m: corner.distance_m, loss_s: corner.delta_s, action: corner.coaching_note || corner.coaching || '선택한 기준과 코너 입력을 비교하세요.',
    estimated_recovery_s: null, evidence: [`선택 기준 대비 코너 손실 ${formatSigned(corner.delta_s)}`],
  })) : analysis?.coaching?.priorities || [];

  const beginTrial = (adjustment) => {
    if (!canStartTrial || !hasSetupNumericValue(adjustment.original_value) || !hasSetupNumericValue(adjustment.value)
      || Number(adjustment.original_value) === Number(adjustment.value)) return;
    startTrial({ id: `trial-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, started_at: new Date().toISOString(),
      baseline_file: selectedFile, candidate_file: null, adjustment, original_value: Number(adjustment.original_value),
      target_value: Number(adjustment.value), original_text: formatSetupOriginal(adjustment), target_text: formatSetupTarget(adjustment),
      goal, session_mode: sessionMode, reference_mode: analysisSnapshot.referenceMode, reference_files: [...analysisSnapshot.references],
      target_lap: comparison?.target_lap || null, reference_lap: comparison?.reference_lap || null,
      feedback: { phase: feedbackPhase, speed: feedbackSpeed, symptom: feedbackSymptom, text: userFeedback },
      context: { name: selectedFile, car_name: currentContext.car_name, car_path: currentContext.car_path,
        track_name: currentContext.track_name, track_path: currentContext.track_path, track_config: currentContext.track_config },
      conditions: { track_temp_c: session?.track_temp_c, air_temp_c: session?.air_temp_c, weather: session?.weather,
        environment: session?.environment || null, setup: session?.setup || null },
      setup_snapshot: session?.setup || null,
      driver_feedback: { change: '', rating: null, side_effects: '' } });
    setActiveTab('setup');
  };

  const detailContextRef = useRef(null);
  detailContextRef.current = analysisSnapshot;
  const loadComparisonDetail = useCallback(async (range) => {
    detailRequestRef.current.controller?.abort();
    const id = detailRequestRef.current.id + 1;
    const controller = new AbortController(); detailRequestRef.current = { id, controller };
    if (!range || !analysisSnapshot) { setDetail(null); setDetailLoading(false); return; }
    setDetail(null);
    setDetailLoading(true);
    const snapshotKey = analysisContextKey(analysisSnapshot);
    const isCurrent = () => !controller.signal.aborted && selectedFileRef.current === analysisSnapshot.filename && detailRequestRef.current.id === id && detailContextRef.current && analysisContextKey(detailContextRef.current) === snapshotKey;
    try {
      const payload = await requestJson('/api/comparison-detail', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ filename: analysisSnapshot.filename, target_lap: comparison?.target_lap || null,
          reference_lap: comparison?.reference_lap || null, external_reference_files: analysisSnapshot.references,
          reference_mode: analysisSnapshot.referenceMode, start_pct: Array.isArray(range) ? range[0] : range.start_pct, end_pct: Array.isArray(range) ? range[1] : range.end_pct, points: 2400 }) });
      if (isCurrent()) setDetail(payload);
    } catch (requestError) { if (isCurrent() && requestError.name !== 'AbortError') setError(requestError.message); }
    finally { if (isCurrent()) setDetailLoading(false); }
  }, [analysisSnapshot, comparison?.target_lap, comparison?.reference_lap]);

  // Dense trace of one corner window for the annotated corner graph (CornerTrace). The practice card
  // reads the local corners, so it always asks for the local reference; the corner table follows the
  // reference the table is showing.
  const cornerWindowLoader = useCallback((mode) => (start, end) => requestJson('/api/comparison-detail', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: analysisSnapshot?.filename, target_lap: comparison?.target_lap || null,
      reference_lap: comparison?.reference_lap || null, external_reference_files: mode === 'LOCAL' ? [] : analysisSnapshot?.references || [],
      reference_mode: mode, start_pct: start, end_pct: end, points: 800 }),
  }).then((payload) => payload?.trace || []), [analysisSnapshot, comparison?.target_lap, comparison?.reference_lap]);
  const localCornerLoader = useMemo(() => (analysisSnapshot ? cornerWindowLoader('LOCAL') : null), [analysisSnapshot, cornerWindowLoader]);
  const overlayLoader = useCallback((body) => requestJson('/api/lap-overlay', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: analysisSnapshot?.filename, ...body }),
  }), [analysisSnapshot]);
  // Expert view: other drive files of the same track and layout, usable Garage61 laps, and a file's lap list.
  const overlayFiles = useMemo(() => {
    const current = files.find((item) => item.name === analysisSnapshot?.filename);
    if (!current) return [];
    const here = comparisonContext(current);
    const stamp = (name) => { const found = /(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})/.exec(name); return found ? `${found[1]}-${found[2]}-${found[3]} ${found[4]}:${found[5]}` : name; };
    return files.filter((item) => item.name !== current.name && item.analyzable !== false
      && comparisonContext(item).track === here.track && comparisonContext(item).config === here.config)
      .map((item) => ({ name: item.name, label: `${stamp(item.name)} · ${item.car_name || ''}` }));
  }, [files, analysisSnapshot?.filename]);
  const overlayReferences = useMemo(() => externalReferences.filter((item) => item.usable !== false && item.compatible !== false && item.name)
    .map((item) => ({ name: item.name, label: `${item.driver_name || '드라이버'} · ${item.lap_time || ''}${item.car_name ? ` · ${item.car_name}` : ''}` })), [externalReferences]);
  const loadFileLaps = useCallback((filename) => requestJson(`/api/file-laps?filename=${encodeURIComponent(filename)}`), []);
  // Garage61 laps the practice card can ask for: the ones chosen on screen, else the recommended ones.
  const practiceGarage61Refs = selectedExternalReferences.length ? selectedExternalReferences
    : (externalReferenceStatus?.recommended_selection || []).slice(0, externalReferenceStatus?.display_limit || externalReferenceStatus?.max_selection || 9);
  // Garage61 fast laps for the practice card: the mode on screen, or their composite when the screen shows the local lap.
  const externalCornerLoader = useMemo(() => (analysisSnapshot?.references?.length
    ? cornerWindowLoader(analysisSnapshot.referenceMode && analysisSnapshot.referenceMode !== 'LOCAL' ? analysisSnapshot.referenceMode : 'AGGREGATE')
    : null), [analysisSnapshot, cornerWindowLoader]);
  const tableCornerLoader = useMemo(() => (analysisSnapshot ? cornerWindowLoader(analysisSnapshot.referenceMode || 'LOCAL') : null), [analysisSnapshot, cornerWindowLoader]);


  return (
    <div className="coach-ui font-sans text-base leading-relaxed">
      <HelpLayer hoverEnabled={hoverHelpEnabled} />
      <SessionModeChangeDialog
        prompt={modeChangePrompt}
        loading={loading}
        onCancel={() => setModeChangePrompt(null)}
        onConfirm={confirmSessionModeChange}
      />

      <WorkspaceShell tabs={APP_TABS} activeTab={activeTab} onTab={selectTab}
        hoverHelpEnabled={hoverHelpEnabled} onToggleHelp={toggleHoverHelp} onGuide={() => navigateToSection('user-guide')}
        hasAnalysis={Boolean(analysis)} trialActive={Boolean(activeTrial)} sections={analysis ? SECTION_NAV[activeTab] : null}>
        <div hidden={!['summary', 'compare', 'setup'].includes(activeTab) && !activeTrial}>
          <Button variant="outline" type="button" className="coach-context-toggle" aria-expanded={controlsExpanded || !analysis || Boolean(activeTrial && !activeTrial.candidate_file)} disabled={!analysis || Boolean(activeTrial && !activeTrial.candidate_file)} onClick={() => setControlsExpanded((value) => !value)}>
            <span><span className="coach-context-subject">{selectedFileEntry?.car_name || session?.car_name || '주행 파일 선택'}</span><span className="coach-context-track">{selectedFileEntry?.track_name || session?.track_name || 'IBT 파일을 선택하세요.'}</span></span><span>주행·비교 기준 변경 <ChevronDown size={14} aria-hidden="true" /></span>
          </Button>
          <div className={cx('coach-control-body', (controlsExpanded || !analysis || Boolean(activeTrial && !activeTrial.candidate_file)) && 'is-open')}>
          <header className="coach-control-deck" aria-label="주행과 비교 기준 선택">
            <div className="coach-control-column">
              <div className="coach-control-title">주행 선택<HelpHint topic="file" /></div>
              <div className="coach-control-row">
                <FileSelect label="분석할 주행" value={selectedFile} onChange={handleFileChange} files={files} hideLabel />
                <Button variant="default" data-help="analyze" type="button" disabled={loading || !selectedFile || selectedFileEntry?.analyzable === false} onClick={() => runAnalysis({ includeAI: false })} className="coach-primary-action">
                  {loading ? '분석 중…' : '분석 갱신'}
                </Button>
              </div>
              {!selectedFileEntry && <p className="coach-control-context">IBT 파일을 선택하면 자동으로 분석합니다.</p>}
              {(selectedFileEntry?.analyzable === false || selectedFileEntry?.retryable) && (selectedFileEntry?.eligibility_reason || selectedFileEntry?.reason) && <p className="mt-2 text-xs text-warning">{selectedFileEntry.eligibility_reason || selectedFileEntry.reason}</p>}
              
            </div>
            <div className="coach-control-column">
              <div className="coach-control-title">비교 기준<HelpHint topic="reference" /></div>
              <div className="coach-reference-controls">
          <div className="text-sm text-muted-foreground"><label className="sr-only" htmlFor="reference-mode">비교 기준</label><NativeSelect id="reference-mode" data-help="reference" aria-label="비교 기준 방식" value={referenceMode} disabled={Boolean(activeTrial) || loading} onChange={(event) => setReferenceMode(event.target.value)} className="min-h-[50px] w-full"><option value="LOCAL">내 주행의 기준 랩</option><option value="AGGREGATE">Garage61 여러 랩 합성</option><option value="SINGLE">Garage61 실제 드라이버 한 랩</option></NativeSelect></div>
          {referenceMode === 'SINGLE' ? <div className="text-sm font-bold text-muted-foreground"><div className="flex items-center gap-1"><label htmlFor="reference-driver">실제 기준 드라이버</label><HelpHint topic="single" /></div><NativeSelect id="reference-driver" data-help="single" aria-label="Garage61 실제 기준 랩" value={selectedExternalReferences[0] || ''} disabled={Boolean(activeTrial) || loading} onChange={(event) => setSelectedExternalReferences([event.target.value])} className="mt-2 w-full"><option value="">기준 랩 선택</option>{externalReferences.map((file) => <option key={file.name} value={file.name} disabled={file.usable === false}>{file.driver_name || file.name} · {file.lap_time || formatLapSeconds(file.lap_time_s)}</option>)}</NativeSelect></div> : <p className="coach-reference-note">{referenceMode === 'LOCAL' ? '내 주행의 두 랩 비교' : `Garage61 ${selectedExternalReferences.length}개 선택 · 코너 비교에서 변경`}</p>}
          <Button variant="outline" data-help="referenceApply" type="button" onClick={() => runAnalysis({ referenceModeOverride: referenceMode })} disabled={loading || !selectedFile || Boolean(activeTrial) || (referenceMode !== 'LOCAL' && !selectedExternalReferences.length)} className="coach-secondary-action coach-reference-apply">비교 기준 적용</Button>

              </div>
            </div>
          </header>
          </div>
          <section className="coach-control-meta" aria-label="주행 유형 · 기록 저장"><div className="coach-control-meta-heading">주행 유형 · 기록 저장<Badge variant="secondary">{sessionModeLabel(selectedAssignedMode || sessionMode)}</Badge></div>
                
                <ToggleGroup type="multiple" variant="outline" size="sm" value={[sessionMode]} onValueChange={values => handleSessionModeChange(values.find(mode => mode !== sessionMode) || sessionMode)} disabled={loading || !selectedFile || selectedFileEntry?.analyzable === false} aria-label="주행 유형" className="flex flex-wrap justify-start gap-2">{SESSION_MODES.map(({ value, label }) => <ToggleGroupItem data-help="session" key={value} value={value}>{label}</ToggleGroupItem>)}</ToggleGroup><HelpHint topic="session" />
                {selectedStoredModes.length > 1 && <p>저장 유형: {selectedStoredModes.map(sessionModeLabel).join(' / ')}</p>}
                <SessionModeHint file={selectedFileEntry} stored={selectedAssignedMode} current={sessionMode} />
                {sessionMode === 'ENDURANCE' && <div className="mt-3 flex flex-wrap items-center gap-2"><span>피트 규정</span><HelpHint topic="pit" />{['CONCURRENT','SEQUENTIAL'].map((rule) => <Button variant="outline" type="button" data-help="pit" key={rule} aria-pressed={pitRule === rule} onClick={() => setPitRule(rule)} className="border  px-3 py-2 text-xs">{rule === 'CONCURRENT' ? '동시 교체' : '순차 교체'}</Button>)}
                  {pitServiceHint(analysis?.pit_service) && <span className="text-xs text-muted-foreground" data-testid="pit-service-hint">{pitServiceHint(analysis.pit_service)}</span>}</div>}
              </section>
        <details className="coach-quality-line"><summary data-help="quality" className="cursor-pointer text-sm font-bold text-muted-foreground">데이터 품질 · 유효랩 {quality?.valid_laps ?? selectedFileEntry?.valid_laps ?? '--'} · {cleanSectionOnly ? '부분 코너 관측' : QUALITY_THEME[quality?.state]?.label || '확인 중'}</summary><div className="mt-3"><QualityBanner quality={quality} /></div></details>
        </div>

        {error && <ErrorMessage message={error} />}
        {['summary', 'compare', 'setup'].includes(activeTab) && status?.telemetry?.retryable && <div role="status" className="rounded-xl border border-primary/30 bg-primary/15 p-3 text-sm text-primary">{status.telemetry.reason || 'IBT 파일 읽기 대기 · 텔레메트리 기록이 종료되면 자동으로 다시 확인합니다.'}</div>}
        {workflow.storageError && <ErrorMessage message={workflow.storageError} />}
        {['summary', 'compare', 'setup'].includes(activeTab) && fileIndex?.pending_count > 0 && <div className="rounded-xl border border-border bg-background/40 p-3 text-sm text-muted-foreground">상세 정보 미확인 파일 {fileIndex.pending_count}개 · 파일을 선택하면 랩 수와 사용 가능한 코너를 확인합니다.</div>}
        {['summary', 'compare', 'setup'].includes(activeTab) && newFileNotice && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-primary/15 p-3 text-sm text-primary"><span>새 주행 발견: {newFileNotice} · 실험의 변경 전 A는 유지합니다.</span><Button variant="outline" type="button" onClick={() => handleFileChange(newFileNotice)} className=" border  px-3 py-2 ">새 주행 확인</Button></div>}
        <TrialPanel trial={activeTrial} currentFile={selectedFile} canAttach={canAttachTrial} onAttach={() => canAttachTrial && patchTrial({ candidate_file: selectedFile, candidate_context: currentContext })}
          onPatch={patchTrial} onComplete={completeTrial} onCompare={() => navigateToSection('ab-manager')} />
        {['summary', 'compare', 'setup'].includes(activeTab) && pendingReferences && <div className="coach-notice flex flex-wrap items-center justify-between gap-3 rounded-xl border border-accent/25 bg-accent/10 p-3 text-sm text-foreground"><span>{activeTrial ? '새 기준 자료 도착 · 실험 기준 고정' : '새 기준 자료 도착'}</span><Button variant="outline" type="button" disabled={Boolean(activeTrial) || loading} onClick={() => { const next = pendingReferences; setSelectedExternalReferences(next); if (referenceMode === 'LOCAL') setReferenceMode('AGGREGATE'); setPendingReferences(null); runAnalysis({ externalReferencesOverride: next, referenceModeOverride: referenceMode === 'LOCAL' ? 'AGGREGATE' : referenceMode }); }} className=" border  px-3 py-2  disabled:opacity-40">새 기준 적용</Button></div>}
        {['summary', 'compare', 'setup'].includes(activeTab) && analysis && !analysisIsCurrent && <div className="rounded-xl border border-warning/30 bg-warning/15 p-3 text-sm text-warning">입력이나 비교 기준이 바뀌었습니다. 현재 카드에는 이전 분석 조건의 결과가 표시됩니다. 분석을 갱신하면 셋업 실험을 시작할 수 있습니다.</div>}
        {['summary', 'compare'].includes(activeTab) && analysisSnapshot?.referenceMode === 'AGGREGATE' && externalReferenceAnalysis?.aggregate?.representative_reference_file && <div className="coach-reference-context">여러 랩 합성 기준 <Button variant="outline" type="button" disabled={Boolean(activeTrial) || loading} onClick={() => { const filename = externalReferenceAnalysis.aggregate.representative_reference_file; setReferenceMode('SINGLE'); setSelectedExternalReferences([filename]); runAnalysis({ referenceModeOverride: 'SINGLE', externalReferencesOverride: [filename] }); }} className="ml-2  border  px-3 py-1   disabled:opacity-40">대표 드라이버의 실제 한 랩으로 보기</Button></div>}
        {cleanSectionOnly && <div className="rounded-xl border border-warning/30 bg-warning/15 p-3 text-sm text-warning">부분 코너 관측 · 유효 완주랩은 없습니다. 깨끗한 구간 {quality?.clean_segment_count || analysis.coaching?.clean_segment_corners?.length || 0}개를 코칭에 사용하며 랩 페이스와 A/B 속도 효과 근거에는 포함하지 않습니다.</div>}


        {activeTab === 'summary' && !analysis && <QuickStart hasAnalysis={false} onGuide={() => navigateToSection('user-guide')} onSetup={() => navigateToSection('briefing')} />}
        <div id="user-guide" className="scroll-mt-40" hidden={activeTab !== 'guide'} role="region" aria-labelledby="tab-guide"><UserManual hasAnalysis={Boolean(analysis)} onNavigate={navigateToSection} /></div>
        <LiveCoach apiBase={API_BASE} visible={activeTab === 'live'} />
        {analysis ? (
          <>
            <div hidden={activeTab !== 'summary'} role="region" aria-labelledby="tab-summary" className="flex flex-col gap-6">
<Panel title="오늘의 한 가지" icon={Target} id="practice-coach">
              <PracticeCoach apiBase={API_BASE} filename={analysis.file?.name || selectedFile} practice={analysis.practice} coach={analysis.closed_loop_coach} visualizations={analysis.visualizations} trackLength={analysis.visualizations?.track_length_m || analysis.session?.track_length_m}
                corners={analysis.performance?.corners || []} loadWindow={localCornerLoader} referenceLabel={comparison?.reference_lap != null ? `내 기준 랩 ${comparison.reference_lap}` : '내 기준 랩'}
                externalCorners={externalReferenceAnalysis?.status === 'READY' ? externalReferenceAnalysis.corners || [] : []}
                externalLoadWindow={externalCornerLoader} externalLabel={externalReferenceAnalysis?.aggregate?.reference_label}
                focusSets={analysis.practice_focuses} garage61Available={!activeTrial && practiceGarage61Refs.length > 0}
                onUseGarage61={() => { setSelectedExternalReferences(practiceGarage61Refs); setReferenceMode('AGGREGATE'); runAnalysis({ referenceModeOverride: 'AGGREGATE', externalReferencesOverride: practiceGarage61Refs }); }} />
            </Panel>
            <Panel title="이번 주행 요약" icon={TimerReset} id="run-summary">
              <RunSummary analysis={analysis} onNavigate={navigateToSection} referenceMode={analysisSnapshot?.referenceMode || 'LOCAL'} />
            </Panel>
            <QuickStart hasAnalysis onGuide={() => navigateToSection('user-guide')} onSetup={() => navigateToSection('briefing')} />
            <Panel title="다음 주행에서 할 일" icon={Sparkles} id="closed-loop-coach">
              <ClosedLoopCoachPanel coach={analysis.closed_loop_coach} currentFile={selectedFile} memory={analysis.memory} />
            </Panel>
            {/* Long lists of memos sit last and folded, so the summary stays one screen. */}
            <Panel title={`대화로 남긴 코너${analysis.voice_review?.notes?.length ? ` · ${analysis.voice_review?.notes?.length}건` : ''}`} icon={Mic} id="voice-review-panel" collapsible defaultOpen={false}>
            <VoiceReview review={analysis.voice_review} filename={analysis.file?.name || selectedFile} laps={analysis.laps || []}
              corners={analysis.performance?.corners?.length ? analysis.performance.corners : analysis.coaching?.clean_segment_corners || []}
              apiBase={API_BASE} onRefresh={() => runAnalysis({ includeAI: false })} onNavigate={navigateToSection}
              onOpenCorner={async (note) => {
                if (activeTrial || loading) { setError('진행 중 실험의 랩과 기준을 유지합니다. 실험 종료 후 메모 랩을 비교하세요.'); return; }
                if (Number(comparison?.target_lap) !== Number(note.lap_number)) {
                  const sameAsReference = Number(referenceLap) === Number(note.lap_number);
                  const payload = await runAnalysis({ includeAI: false, persistMemory: false, targetLapOverride: Number(note.lap_number), ...(sameAsReference ? { referenceLapOverride: null } : {}) });
                  if (!payload || Number(payload.comparison?.target_lap) !== Number(note.lap_number)) { setError('이 랩은 비교 대상으로 사용할 수 없습니다. 대화 카드의 해당 랩 관측을 확인하세요.'); return; }
                  setTargetLapManual(true);
                  if (sameAsReference) setReferenceLapManual(false);
                }
                navigateToSection('corner-inputs', { distance_pct: note.distance_pct });
              }} />
            </Panel>
            </div>
            <div hidden={activeTab !== 'compare'} role="region" aria-labelledby="tab-compare" className="flex flex-col gap-6">
<div id="corner-inputs">
              <CornerComparison trace={comparisonTrace} corners={comparisonCorners} pathData={comparisonPath} requestedCorner={requestedCorner}
                referencePath={wantsExternalComparison ? externalReferenceAnalysis?.visualizations?.reference_track_path : analysis.visualizations?.reference_track_path}
                referenceLineSource={wantsExternalComparison ? externalReferenceAnalysis?.visualizations?.reference_line_source : analysis.visualizations?.reference_line_source}
                detailReferencePath={detail?.reference_track_path} detailLineSource={detail?.reference_line_source}
                trackIdentity={session} trackLengthM={session?.track_length_m || analysis.visualizations?.track_length_m}
                referenceLabel={hasExternalTrace ? externalReferenceAnalysis.aggregate?.reference_label || (analysisSnapshot?.referenceMode === 'SINGLE' ? '선택한 Garage61 드라이버' : 'Garage61 합성 기준') : comparison?.reference_lap != null ? `내 기준 랩 ${comparison.reference_lap}` : '비교 기준 없음'}
                comparisonKey={JSON.stringify([analysisSnapshot?.filename, analysisSnapshot?.referenceMode, analysisSnapshot?.references, analysis?.file?.modified_at, comparison?.target_lap, comparison?.reference_lap])} onRangeChange={loadComparisonDetail} detailTrace={detail?.trace} detailPath={detail?.track_path} detailQuality={detail?.quality} detailLoading={detailLoading} />
            </div>
            <Panel title="코너 번호 확인" icon={Route} id="corner-names" collapsible defaultOpen={!analysis.performance?.corner_names_confirmed}>
              <CornerNames apiBase={API_BASE} filename={analysis.file?.name} onSaved={() => runAnalysis({ includeAI: false, quiet: true })} />
            </Panel>
            <Panel title="거리 기준 랩 비교" icon={TimerReset} id="comparison">
              <ComparisonSummary comparison={displayComparison} lapSelection={hasExternalTrace ? null : analysis.lap_selection} />
            </Panel>
<Panel title="랩타임 단축 우선순위" icon={Target} id="priorities">
              <PriorityCards priorities={displayPriorities} />
            </Panel>
<Panel title="Garage61 기준 주행" icon={Target} collapsible defaultOpen={false} id="external-reference">
              <ExternalReferencePanel
                referenceStatus={externalReferenceStatus}
                files={externalReferences}
                selected={selectedExternalReferences}
                analysis={externalReferenceAnalysis}
                loading={loading || Boolean(activeTrial)}
                collector={garage61Collector}
                collectorLoading={garage61CollectorLoading}
                apiStatus={garage61ApiStatus}
                onToggle={toggleExternalReference}
                onRefresh={refreshExternalReferences}
                onApply={applyExternalReferences}
                onAutoSelect={() => !activeTrial && setSelectedExternalReferences((externalReferenceStatus?.recommended_selection || []).slice(0, externalReferenceStatus?.display_limit || externalReferenceStatus?.max_selection || 9))}
                onClear={() => !activeTrial && setSelectedExternalReferences([])}
                onCollect={() => startGarage61Collection(selectedFileRef.current, false)}
                onForceCollect={() => startGarage61Collection(selectedFileRef.current, true)}
                onCancelCollect={cancelGarage61Collection}
              />
            </Panel>
<Panel title="랩별 시간 쓰임새" icon={Activity} collapsible defaultOpen={false} id="lap-time-use">
              <LapRangeFilter laps={analysis.laps || []} value={lapRange} onChange={setLapRange} />
              <div className="mt-4"><LapTimeUse laps={(analysis.laps || []).filter((lap) => pickedLaps.has(lap.lap_number))} /></div>
            </Panel>
<Panel title="랩 선택 및 품질" icon={Database} collapsible defaultOpen={false} id="lap-detail">
              <LapSelectionSummary
                selection={analysis.lap_selection}
                onResetAuto={resetAutoLapSelection}
                loading={loading}
              />
              <LapTable
                laps={analysis.laps}
                targetLap={targetLap}
                referenceLap={referenceLap}
                onTargetChange={handleTargetLapChange}
                onReferenceChange={handleReferenceLapChange}
              />
              <div className="mt-4 flex justify-end">
                <Button variant="outline"
                  type="button"
                  onClick={() => runAnalysis({ includeAI: false })}
                  data-help="analyze"
                  disabled={!targetLap || loading}
                  className=" border   px-4 py-2 text-sm   transition  disabled:opacity-30"
                >
                  {targetLapManual || referenceLapManual ? '수동 선택 랩으로 재계산' : '자동 선택 랩으로 재계산'}
                </Button>
              </div>
            </Panel>
<Panel title="코너 자동 분할 및 위상별 비교" icon={Route} collapsible defaultOpen={false} id="corner-table">
              <CornerTable corners={comparisonCorners} trackLength={analysis.visualizations?.track_length_m || analysis.session?.track_length_m} loadWindow={tableCornerLoader}
                referenceLabel={wantsExternalComparison ? 'Garage61 기준' : comparison?.reference_lap != null ? `기준 랩 ${comparison.reference_lap}` : '기준 랩'} />
            </Panel>
            <Panel title="코너별 일관성" icon={Activity} collapsible defaultOpen={false} id="corner-consistency">
              <LapRangeFilter laps={analysis.laps || []} value={lapRange} onChange={setLapRange} />
              <div className="mt-4"><CornerConsistency consistency={analysis.performance?.corner_consistency} allowedLaps={pickedLaps} /></div>
            </Panel>
            <Panel title="전문가 보기 · 여러 랩 겹쳐 보기" icon={LineChart} collapsible defaultOpen={false} lazy id="lap-overlay">
              <LapOverlay laps={analysis.laps || []} corners={analysis.performance?.corners || []} load={analysisSnapshot ? overlayLoader : null}
                preferredLaps={[comparison?.reference_lap, comparison?.target_lap]}
                otherFiles={overlayFiles} references={overlayReferences} loadLaps={loadFileLaps} />
            </Panel>
            </div>
            <div hidden={activeTab !== 'setup'} role="region" aria-labelledby="tab-setup" className="flex flex-col gap-6">
<Panel title="드라이버 피드백과 검증" icon={SlidersHorizontal} id="briefing">
              <StructuredFeedback phase={feedbackPhase} speed={feedbackSpeed} symptom={feedbackSymptom}
                setupMode={setupMode} onSetupMode={setSetupMode}
                onPhase={setFeedbackPhase} onSpeed={setFeedbackSpeed} onSymptom={setFeedbackSymptom}
                value={userFeedback} onChange={setUserFeedback} result={analysis.structured_feedback}
                coach={analysis.closed_loop_coach} onAnalyze={() => runAnalysis({ includeAI: false })}
                onStart={beginTrial} canStart={canStartTrial} goal={goal} onGoal={setGoal} trialActive={Boolean(activeTrial)}
                onNavigate={navigateToSection} loading={loading} />
            </Panel>
            <Panel title="엔지니어 브리핑" icon={Sparkles} collapsible defaultOpen={false} id="engineer-briefing">
              <div className="mb-4 flex justify-end"><Button variant="outline" data-help="ai" type="button" onClick={() => runAnalysis({ includeAI: true })}
                disabled={loading || !status?.ai_enabled} className=" border   px-4 py-3 text-sm   disabled:opacity-40">{status?.ai_enabled ? 'AI 브리핑 생성 · 토큰 사용' : 'AI 연결 필요'}</Button></div>
              <BriefingViewer briefing={analysis.briefing} />
            </Panel>
              <Panel title="셋업 결정" icon={SlidersHorizontal} id="setup-decision">
                <SetupRecommendation recommendation={analysis.setup_recommendation} onStart={beginTrial} canStart={canStartTrial} />
              </Panel>
<Panel title="셋업 A/B 비교" icon={FlaskConical} id="ab-manager">
              <ExperimentManager
                files={files}
                sessionMode={sessionMode}
                preferredBaseline={activeTrial?.baseline_file || selectedFile}
                trial={activeTrial} goal={goal} onComparison={(result) => patchTrial({ comparison: result })}
                onStored={handleExperimentStored}
              />
            </Panel>
              <Panel title="원인 분류" icon={Activity} id="decision" collapsible defaultOpen={false}>
                <ClassificationPanel classification={classification} />
              </Panel>
<Panel title="AI 셋업 실험 계획" icon={Sparkles} collapsible defaultOpen={false} id="ai-planner">
              <AIExperimentPlanner
                selectedFile={selectedFile}
                sessionMode={sessionMode}
                pitRule={pitRule}
                targetLap={targetLap}
                referenceLap={referenceLap}
                userFeedback={userFeedback}
                feedbackPhase={feedbackPhase}
                feedbackSpeed={feedbackSpeed}
                feedbackSymptom={feedbackSymptom}
                setupMode={setupMode}
                aiEnabled={Boolean(status?.ai_enabled)}
                externalReferenceFiles={selectedExternalReferences}
                onStored={loadMemory}
              />
            </Panel>
<Panel title="타이어 종료 스냅샷" icon={Thermometer} collapsible defaultOpen={false} id="tire-snapshot">
              <TirePanel tires={analysis.vehicle?.tires} />
            </Panel>

            <Panel title="차고·서스펜션·에어로" icon={Layers3} collapsible defaultOpen={false} id="ride-aero">
              <RideAeroPanel
                rideHeight={analysis.vehicle?.ride_height}
                aero={analysis.vehicle?.aero}
              />
            </Panel>
            <Panel title="댐퍼 속도 분포" icon={Activity} collapsible defaultOpen={false} id="damper-velocity">
              <DamperHistogram current={analysis.vehicle?.damper_velocity} />
            </Panel>
            <Panel title="G-G 그림 (그립 사용)" icon={Activity} collapsible defaultOpen={false} id="gg-diagram">
              <GGDiagram current={analysis.vehicle?.gg} />
            </Panel>
            </div>
            <div hidden={activeTab !== 'history'} role="region" aria-labelledby="tab-history" className="flex flex-col gap-6">
<Panel title="내 주행 특성과 셋업 학습" icon={Activity} id="personalization">
              <PersonalizationPanel memory={analysis.memory} />
            </Panel>
            <Panel title="종료한 셋업 실험" icon={FlaskConical}><TrialHistory history={trialHistory} /></Panel>
<Panel title="개인 기록 저장" icon={Database}>
              <LongTermMemoryPanel
                memoryStatus={memoryStatus}
                sessions={memorySessions}
                currentMemory={analysis.memory}
              />
            </Panel>
            </div>
          </>
        ) : activeTab === 'history' ? (
          <div className="flex flex-col gap-6"><Panel title="셋업 실험 이력" icon={FlaskConical}><TrialHistory history={trialHistory} /></Panel><Panel title="주행 분석 이력" icon={Database}><HistoryPanel history={memorySessions} /></Panel></div>
        ) : activeTab !== 'settings' && activeTab !== 'guide' && activeTab !== 'live' ? (
          loading || initialLoading ? (
            <Card aria-busy="true" role="status" aria-label="주행 분석 중">
              <CardHeader><CardTitle>선택한 주행을 분석하고 있습니다.</CardTitle><CardDescription>랩과 코너 데이터를 확인하고 있습니다.</CardDescription></CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-3">{[0, 1, 2].map(index => <Skeleton key={index} className="h-24 rounded-xl" />)}</CardContent>
            </Card>
          ) : (
            <Empty className="border">
              <EmptyHeader><EmptyMedia variant="icon"><UploadCloud aria-hidden="true" /></EmptyMedia><EmptyTitle role="heading" aria-level={2}>분석할 주행을 선택하세요.</EmptyTitle><EmptyDescription>IBT 파일을 선택하면 주행 분석을 시작합니다.</EmptyDescription></EmptyHeader>
            </Empty>
          )
        ) : null}
        <div id="settings" hidden={activeTab !== 'settings'} role="region" aria-labelledby="tab-settings" className="flex flex-col gap-6">
          {analysis && <AIUsageBanner usage={analysis.ai_usage} aiEnabled={Boolean(status?.ai_enabled)} />}
          <Panel title="연결과 데이터 상태" icon={Activity}>
            <div className="coach-connection-cards grid gap-3 md:grid-cols-3">
              <MetricCard label="텔레메트리" value={status?.telemetry ? '파일 확인됨' : '주행 파일 대기'} note={status?.telemetry?.file_name} icon={Activity} tone="cyan" />
              <MetricCard label="Garage61 개인 API" value={garage61ApiStatus?.connected ? '연결됨' : garage61ApiStatus?.configured ? '연결 확인 필요' : '설정 필요'} note={garage61ApiStatus?.message} icon={Target} tone="violet" />
              <MetricCard label="AI 브리핑" value={status?.ai_enabled ? '사용 가능' : '연결 필요'} note="실행할 때만 토큰을 사용합니다." icon={Sparkles} tone="slate" />
            </div>
            <div className="coach-storage-row"><span>개인 기록</span><strong>{memoryStatus?.enabled ? `${memoryStatus.counts?.sessions ?? 0} 세션 저장됨` : memoryStatus?.error || '연결 확인 필요'}</strong></div>
            {memoryStatus?.path && <dl className="coach-storage-row"><dt>저장 위치</dt><dd className="min-w-0 break-all text-foreground">{memoryStatus.path}</dd></dl>}
          </Panel>
          <Panel title="설치 설정" icon={Database}><AppSettings apiBase={API_BASE} visible={activeTab === 'settings'} /></Panel>
          <Panel title="세션 로그 보내기" icon={Activity}><SessionLogExport apiBase={API_BASE} visible={activeTab === 'settings'} /></Panel>
          <Panel title="사용 방법" icon={Info}><p className="text-sm text-foreground">사용 가이드에서 첫 주행, Garage61 비교, 셋업 A/B 실험과 문제 해결을 확인하세요.</p><Button variant="outline" type="button" onClick={() => navigateToSection('user-guide')} className="mt-3  border  px-4 py-2  ">전체 매뉴얼 열기</Button></Panel>
        </div>
      </WorkspaceShell>
    </div>
  );
}
