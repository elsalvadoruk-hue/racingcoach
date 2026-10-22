import { readGameItem } from '../lib/gameStorage';
export const CHANNEL_ORDER_KEY = 'iracing.ui-lab.channel-order.v1';
export const DEFAULT_CHANNEL_ORDER = ['delta', 'speed', 'brake', 'throttle', 'pedals', 'steering', 'gear'];

export function normalizeChannelOrder(value) {
  return [...new Set([...(Array.isArray(value) ? value : []), ...DEFAULT_CHANNEL_ORDER])].filter(key => DEFAULT_CHANNEL_ORDER.includes(key));
}
export function readChannelOrder(game = 'iracing') {
  try { return normalizeChannelOrder(JSON.parse(readGameItem(CHANNEL_ORDER_KEY, game))); } catch { return [...DEFAULT_CHANNEL_ORDER]; }
}
export function moveChannel(order, from, to) {
  if (from === to || !order.includes(from) || !order.includes(to)) return order;
  const next = order.filter(key => key !== from);
  next.splice(order.indexOf(to), 0, from);
  return next;
}
export function timelineHeights(corners, maximum = 20) {
  const scale = Math.max(.001, ...corners.map(c => Number.isFinite(c.delta_s) ? Math.abs(c.delta_s) : 0));
  return corners.map(c => Number.isFinite(c.delta_s) ? Math.max(2, Math.min(maximum, Math.abs(c.delta_s) / scale * maximum)) : 0);
}

export const lapStatus = lap => ({ VALID: '유효', INVALID: '무효', OUT_LAP: '아웃랩', IN_LAP: '인랩', PARTIAL: '불완전' }[lap.status] || '확인 필요');
// Why a lap is invalid, in the driver's words; an unknown reason is shown as it came.
const LAP_REASONS = {
  'incident during the lap': '인시던트(1x 이상) 발생',
  'off-track samples present': '트랙 밖 주행',
  'pit road samples present': '피트로드 포함',
  'beyond the measured track edge': '트랙 경계 밖 주행',
  'LMU invalidated the lap': '게임이 무효 처리(트랙 리밋)',
  'LMU recorded no matching lap time': '게임 랩타임 없음',
  'iRacing did not publish a valid completed lap time': '게임 랩타임 없음',
};
export const lapReason = reason => (reason ? LAP_REASONS[reason] || reason : '');
export function isBestLap(lap, laps) {
  const best = Math.min(...laps.filter(l => l.status === 'VALID' && Number.isFinite(l.lap_time_s)).map(l => l.lap_time_s));
  return lap?.status === 'VALID' && Number.isFinite(lap.lap_time_s) && Math.abs(lap.lap_time_s - best) < .0001;
}
export function lapCounts(laps = []) {
  if (laps.some(l => l.measurement_eligible === true)) return { detected_laps: laps.length,
    measurement_laps: laps.filter(l => l.measurement_eligible === true).length,
    complete_laps: laps.filter(l => l.measurement_eligible === true).length };
  return { detected_laps: laps.length, valid_laps: laps.filter(l => l.status === 'VALID').length,
    complete_laps: laps.filter(l => ['VALID', 'INVALID'].includes(l.status)).length };
}
export function fileLapLabel(file) {
  if (Number.isFinite(file.measurement_laps)) return `기록 ${file.detected_laps}랩 · 측정 가능 ${file.measurement_laps} · 유효성 확인 필요`;
  return Number.isFinite(file.detected_laps) && Number.isFinite(file.valid_laps)
    ? `기록 ${file.detected_laps}랩 · 유효 ${file.valid_laps} · 비유효 ${file.detected_laps - file.valid_laps}` : '랩 수 확인 전';
}

export function fileBestLap(laps = []) {
  const times = laps.filter(lap => (lap.status === 'VALID' ||
    (lap.measurement_eligible === true && lap.complete !== false && lap.status !== 'INVALID')) &&
    Number.isFinite(lap.lap_time_s) && lap.lap_time_s > 0).map(lap => lap.lap_time_s);
  return times.length ? Math.min(...times) : null;
}

export function fileRecordedLabel(file) {
  // Recording wall time, never file mtime (copying a recording changes mtime).
  const candidates = [file.recording_time, file.file_name, file.display_name, file.name];
  for (const value of candidates) {
    const raw = String(value || '');
    const match = raw.match(/(20\d{2})[-_/.](\d{2})[-_/.](\d{2})[T _-](\d{2})[:_-](\d{2})(?!\d)/);
    if (!match) continue;
    const [, year, month, day, hour, minute] = match;
    const date = new Date(Date.UTC(+year, +month - 1, +day));
    if (date.getUTCFullYear() === +year && date.getUTCMonth() === +month - 1 &&
        date.getUTCDate() === +day && +hour < 24 && +minute < 60) {
      // LMU writes UTC RecordingTime such as 2026-10-20T10_00_00Z.
      // Explicit zones are shown in the browser's local time, like IBT names.
      const zone = raw.slice(match.index + match[0].length).match(/^(?:[:_-]\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})(?:$|\b)/i);
      if (zone) {
        const local = new Date(`${year}-${month}-${day}T${hour}:${minute}:00${zone[1]}`);
        if (!Number.isNaN(local.getTime())) {
          const two = n => String(n).padStart(2, '0');
          return `${two(local.getFullYear() % 100)}-${two(local.getMonth() + 1)}-${two(local.getDate())} ${two(local.getHours())}:${two(local.getMinutes())}`;
        }
      }
      return `${year.slice(-2)}-${month}-${day} ${hour}:${minute}`;
    }
  }
  return '시간 확인 전';
}

export function fileChoiceLabel(file) {
  const seconds = file.best_lap_s;
  const milliseconds = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : null;
  const best = milliseconds == null ? '베랩 확인 전'
    : `베랩 ${Math.floor(milliseconds / 60000)}:${String(Math.floor(milliseconds / 1000) % 60).padStart(2, '0')}.${String(milliseconds % 1000).padStart(3, '0')}`;
  return [fileRecordedLabel(file), file.track_name || '트랙 확인 전', best,
    fileLapLabel(file), sessionTypeLabel(file), file.car_name].filter(Boolean).join(' · ');
}

export function sessionTypeLabel(value) {
  const metadata = value?.session || value;
  const mode = String(metadata?.suggested_session_mode || '').toLowerCase();
  const raw = typeof metadata === 'object' ? (metadata?.session_type || metadata?.suggested_session_mode) : metadata;
  const kind = String(raw || '').toLowerCase();
  if (kind.includes('endurance') || (kind.includes('race') && mode === 'endurance')) return '인듀런스';
  if (kind.includes('qual')) return '퀄리파잉';
  if (kind.includes('race')) return '레이스';
  if (kind.includes('practice') || kind.includes('test')) return '연습';
  return '세션 유형 확인 필요';
}

export function sessionTypeTone(value) {
  const label = sessionTypeLabel(value);
  return label === '인듀런스' ? 'endurance' : label === '퀄리파잉' ? 'qualifying' : label === '레이스' ? 'race' : label === '연습' ? 'practice' : 'unknown';
}

export function lapIsComplete(lap) {
  return lap?.status === 'VALID' || (lap?.measurement_eligible === true && lap.complete === true) || (Number.isFinite(lap?.start_pct) && Number.isFinite(lap?.end_pct) && lap.end_pct - lap.start_pct >= .98);
}
