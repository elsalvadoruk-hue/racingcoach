import { Gauge, Route, FlaskConical, Mic, Database, SlidersHorizontal, Info, Zap, RefreshCw, Activity, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";

export const APP_TABS = [
  { key: 'summary', label: '주행 요약', icon: Gauge },
  { key: 'compare', label: '코너 비교', icon: Route },
  { key: 'setup', label: '셋업 실험', icon: FlaskConical },
  { key: 'live', label: '음성 코치', icon: Mic },
  { key: 'history', label: '기록', icon: Database },
  { key: 'settings', label: '설정', icon: SlidersHorizontal },
  { key: 'guide', label: '사용 가이드', icon: Info },
];

export const SESSION_MODES = [
  { value: 'QUALIFYING', label: '퀄리파잉 · 2랩', icon: Zap },
  { value: 'BASELINE_3LAP', label: '3-Lap 베이스', icon: RefreshCw },
  { value: 'ENDURANCE', label: '인듀런스', icon: Activity },
];

export const SESSION_MODE_META = {
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

export function storedModesForFile(file) {
  const modes = file?.memory?.session_modes;
  return Array.isArray(modes) ? modes.filter(Boolean) : [];
}

export function assignedModeForFile(file) {
  return file?.memory?.assigned_mode || storedModesForFile(file)[0] || null;
}

export function initialModeForFile(file, fallback = 'BASELINE_3LAP') {
  const suggested = SESSION_MODE_META[file?.suggested_session_mode] ? file.suggested_session_mode : null;
  return assignedModeForFile(file) || suggested || fallback;
}

export function sessionModeLabel(mode) {
  return SESSION_MODE_META[mode]?.label || mode || '미지정';
}

export function lapBadgeClass(file, selected = false) {
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

export function normalizeContextValue(value) {
  return String(value || '').trim().toLocaleLowerCase();
}

export function comparisonContext(file) {
  return {
    car: normalizeContextValue(file?.car_path || file?.car_name),
    track: normalizeContextValue(file?.track_path || file?.track_name),
    config: normalizeContextValue(file?.track_config),
  };
}

export function sameVehicleAndTrack(left, right) {
  if (!left || !right) return false;
  const a = comparisonContext(left);
  const b = comparisonContext(right);
  return Boolean(a.car && a.track && a.car === b.car && a.track === b.track && a.config === b.config);
}

export function analysisContextKey(context) {
  return JSON.stringify({ ...context, references: [...(context.references || [])].sort() });
}

export function comparisonContextLabel(file) {
  if (!file) return '기준 파일을 선택하십시오.';
  const track = [file.track_name, file.track_config].filter(Boolean).join(' · ');
  return `${file.car_name || 'Unknown Car'} @ ${track || 'Unknown Track'}`;
}

export const QUALITY_THEME = {
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

export const CLASSIFICATION_LABELS = {
  INSUFFICIENT_DATA: '반복성 근거 부족',
  DRIVER_LIMITED: '드라이빙 영향 우세',
  SETUP_LIMITED: '셋업 영향 우세',
};

export const ISSUE_LABELS = {
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

export const SPEED_LABELS = { LOW: '저속', MEDIUM: '중속', HIGH: '고속' };

export function cx(...classes) {
  return classes.filter(Boolean).join(' ');
}

export const SETTING_NAMES = {
  rear_wing_angle_deg: '리어 윙 각도', rear_arb_blades: '리어 ARB 블레이드', front_arb_blades: '프런트 ARB 블레이드',
  front_ride_height: '앞 차고', rear_ride_height: '뒤 차고', diff_preload_nm: '디프 프리로드',
  brake_bias_pct: '브레이크 바이어스', front_toe_mm: '앞 토',
};

export function settingName(item) {
  return SETTING_NAMES[item?.setting] || item?.label || item?.setting || '';
}

export function formatNumber(value, digits = 1, suffix = '') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  return `${Number(value).toFixed(digits)}${suffix}`;
}

export function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const amount = bytes / (1024 ** index);
  return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatSigned(value, digits = 3, suffix = ' s') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  const numeric = Number(value);
  return `${numeric > 0 ? '+' : ''}${numeric.toFixed(digits)}${suffix}`;
}

export function formatLapSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return '--';
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - minutes * 60;
  return `${minutes}:${remainder.toFixed(3).padStart(6, '0')}`;
}

export function statusColor(status) {
  if (status === 'VALID') return 'text-success bg-success/10 border-success/30';
  if (status === 'INVALID') return 'text-destructive bg-destructive/10 border-destructive/30';
  if (status === 'OUT_LAP') return 'text-blue-300 bg-blue-500/10 border-blue-500/30';
  return 'text-warning bg-warning/10 border-warning/30';
}

export const SECTION_NAV = {
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
    { id: 'tire-snapshot', label: '타이어 종료 스냅샷' },
    { id: 'ride-aero', label: '차고·서스펜션·에어로' },
    { id: 'damper-velocity', label: '댐퍼 속도 분포' },
    { id: 'gg-diagram', label: 'G-G 그림 (그립 사용)' },
  ],
};

export function pitServiceHint(service) {
  const name = (rule) => (rule === 'SEQUENTIAL' ? '순차 교체' : '동시 교체');
  if (service?.rule) return `이 파일의 피트 기록: ${name(service.rule)} (정차 ${service.stops?.length || 0}회)`;
  if (service?.remembered) return `이 시리즈의 지난 경기 기록: ${name(service.remembered)}`;
  return null;
}

export function lapReproducibilityLabel(status) {
  if (status === 'STRONG') return { label: '재현성 강함', cls: 'border-success/25 bg-success/10 text-success' };
  if (status === 'SUPPORTED') return { label: '재현 가능', cls: 'border-primary/25 bg-primary/10 text-primary' };
  if (status === 'ISOLATED') return { label: '단발 / 고립', cls: 'border-warning/25 bg-warning/10 text-warning' };
  return { label: '--', cls: 'border-border bg-muted text-muted-foreground' };
}

export function lapSelectionReasonLabel(reason) {
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

export function hasSetupNumericValue(value) {
  if (value === null || value === undefined || value === '') return false;
  const numeric = Number(value);
  return Number.isFinite(numeric);
}

export function formatSetupNumber(value) {
  if (!hasSetupNumericValue(value)) return '';
  const numeric = Number(value);
  return Number.isInteger(numeric) ? String(numeric) : String(Number(numeric.toFixed(4)));
}

export function localizeSetupStep(stepText, direction) {
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

export function formatSetupOriginal(adjustment) {
  if (!hasSetupNumericValue(adjustment?.original_value)) return '--';
  const number = formatSetupNumber(adjustment.original_value);
  return `${number}${adjustment?.unit ? ` ${adjustment.unit}` : ''}`;
}

export function formatSetupTarget(adjustment) {
  if (adjustment?.target_display) return String(adjustment.target_display);
  if (hasSetupNumericValue(adjustment?.value)) {
    const number = formatSetupNumber(adjustment.value);
    const suffix = adjustment?.numeric_target_estimated ? ' ≈' : '';
    return `${number}${adjustment?.unit ? ` ${adjustment.unit}` : ''}${suffix}`;
  }
  return localizeSetupStep(adjustment?.step_text, adjustment?.direction);
}

export function scrollToCoachSection(id) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent('coach-navigate', { detail: id }));
}

export function workflowTone(status) {
  if (status === 'DONE' || status === 'ACTIVE') return 'border-success/30 bg-success/20 text-success';
  if (status === 'READY') return 'border-primary/30 bg-primary/20 text-primary';
  if (status === 'OPTIONAL') return 'border-accent/30 bg-accent/20 text-foreground';
  if (status === 'LIMITED') return 'border-warning/30 bg-warning/20 text-warning';
  return 'border-border bg-background/50 text-muted-foreground';
}

export const FEEDBACK_OPTION_DESCRIPTIONS = {
  EXPLORE: '적은 랩으로 시작 · 처음 사용 시 권장', EVIDENCE: '반복 측정 근거가 있을 때',
  ENTRY: '감속하며 들어갈 때', MID_CORNER: '꼭짓점 부근', EXIT: '가속하며 나갈 때',
  LOW: '105km/h 미만', MEDIUM: '105 이상 · 165 미만', HIGH: '165km/h 이상',
  UNDERSTEER: '앞이 바깥으로 밀림', OVERSTEER: '뒤가 흐르며 돌아감',
  BRAKING: '감속 중 흔들림', KERB: '연석에서 접지 잃음',
};
