import { controlWindow } from './controlEvents.js';

const finite = value => typeof value === 'number' && Number.isFinite(value);
const wrap = value => ((value + .5) % 1 + 1) % 1 - .5;

// Choose up to five measured differences, with room for different driving phases.
// Scores order inspection priorities, not causes or predicted time savings.
export function cornerAnnotations(corner, rows, trackLength, controls = {}) {
  if (!corner) return [];
  const target = corner.target || {}, reference = corner.reference || {};
  const scoped = rows.filter(row => row.distance_pct >= corner.entry_pct && row.distance_pct <= corner.exit_pct);
  const inputs = target.inputs_usable !== false && reference.inputs_usable !== false;
  const measured = target.measurement_usable !== false && reference.measurement_usable !== false;
  const notes = [];
  const extreme = (key, minimum = false, source = scoped) => source.filter(row => finite(row[key]))
    .reduce((best, row) => !best || (minimum ? row[key] < best[key] : Math.abs(row[key]) > Math.abs(best[key])) ? row : best, null)?.distance_pct;
  const minimum = key => extreme(key, true);
  const add = note => { if (finite(note.score) && finite(note.difference)) notes.push(note); };
  const nearCorner = pct => finite(pct) && [pct, pct + 1, pct - 1].some(p => p >= corner.entry_pct - 75 / trackLength && p <= corner.exit_pct + 75 / trackLength);
  const place = (key, label, channel) => {
    const at = target[`${key}_pct`], refAt = reference[`${key}_pct`];
    const difference = finite(at) && finite(refAt) && trackLength > 0 ? wrap(at - refAt) * trackLength : null;
    const threshold = Math.max(3, (target.distance_resolution_m || 0) * 2, (reference.distance_resolution_m || 0) * 2);
    if (!inputs || !nearCorner(at) || !nearCorner(refAt) || !finite(difference) || Math.abs(difference) < threshold) return;
    const action = { brake_start: '밟았습니다.', brake_release: '놓았습니다.', throttle_first: '가속을 시작했습니다.', full_throttle: '열었습니다.' }[key];
    add({ key, label, channel, at, refAt, range: { from: at, to: refAt, type: 'comparison' }, difference, score: Math.abs(difference) / 12,
      value: `${Math.abs(difference).toFixed(0)} m ${difference > 0 ? '늦게' : '일찍'}`,
      text: `기준보다 ${Math.abs(difference).toFixed(0)}m ${difference > 0 ? '늦게' : '일찍'} ${action}`,
      hint: channel === 'brake' ? '제동 위치와 최저·탈출 속도를 함께 비교하세요. 늦은 제동 자체가 개선을 뜻하지는 않습니다.' : '가속 복귀 뒤 다시 페달을 놓는지와 탈출 속도를 함께 확인하세요.' });
  };
  const speed = (key, label, at, refAt) => {
    const a = target[`${key}_kph`], b = reference[`${key}_kph`];
    const difference = finite(a) && finite(b) ? a - b : null;
    if (!measured || !finite(difference) || Math.abs(difference) < 1) return;
    add({ key, label, channel: 'speed', at, refAt, difference, score: Math.abs(difference) / 3,
      value: `${Math.abs(difference).toFixed(1)} km/h ${difference < 0 ? '낮음' : '높음'}`,
      text: `내 랩 ${a.toFixed(1)} / 기준 ${b.toFixed(1)} km/h`,
      hint: '속도 차이가 생기기 전의 페달과 구간 내 시간차를 함께 확인하세요. 속도 차이만으로 원인을 단정하지 않습니다.' });
  };
  place('brake_start', '제동 시작', 'brake');
  speed('min_speed', '최저 속도', minimum('speed'), minimum('referenceSpeed'));
  place('brake_release', '브레이크 놓음', 'brake');
  speed('exit_speed', '탈출 속도', corner.exit_pct, corner.exit_pct);
  place('throttle_first', '가속 재개', 'throttle');
  place('full_throttle', '풀스로틀', 'throttle');
  const scalar = (key, label, channel, threshold, unit, scale, digits, at, refAt, hint) => {
    const a = target[key] * scale, b = reference[key] * scale;
    if (!inputs || !finite(target[key]) || !finite(reference[key]) || Math.abs(a - b) < threshold) return;
    add({ key, label, channel, at, refAt, difference: a - b, score: Math.abs(a - b) / threshold,
      value: `${Math.abs(a - b).toFixed(digits)} ${unit} ${a > b ? '많음' : '적음'}`,
      text: `내 랩 ${a.toFixed(digits)} / 기준 ${b.toFixed(digits)} ${unit === '%p' ? '%' : unit} · 구간 지표`, hint });
  };
  const entryRows = scoped.filter(row => row.distance_pct <= corner.apex_pct);
  scalar('peak_brake', '최대 제동압', 'brake', 5, '%p', 100, 0, extreme('brake', false, entryRows), extreme('referenceBrake', false, entryRows),
    '최대 제동압 위치를 표시합니다. ABS 개입과 제동 시간을 함께 확인하세요.');
  scalar('braking_time_s', '제동 시간', 'brake', .15, '초', 1, 2, target.brake_start_pct, reference.brake_start_pct,
    '제동 시작부터 놓는 지점까지의 시간입니다. 진입 속도가 다르면 제동 시간도 달라집니다.');
  scalar('avg_decel_g', '평균 감속', 'brake', .08, 'g', 1, 2, target.brake_start_pct, reference.brake_start_pct,
    '제동 구간 전체의 평균입니다. 같은 속도대 제동 비교와 ABS를 함께 확인하세요.');
  const firstCoast = isReference => scoped.find(row => row.distance_pct <= corner.apex_pct &&
    finite(row[isReference ? 'referenceThrottle' : 'throttle']) && finite(row[isReference ? 'referenceBrake' : 'brake']) &&
    row[isReference ? 'referenceThrottle' : 'throttle'] < 5 && row[isReference ? 'referenceBrake' : 'brake'] < 5 && row[isReference ? 'referenceSpeed' : 'speed'] > 50)?.distance_pct;
  scalar('coast_time_s', '타력 주행', 'throttle', .15, '초', 1, 2, firstCoast(false), firstCoast(true),
    '진입부터 정점까지 두 페달이 5% 미만인 시간입니다. 첫 관측 위치를 표시하며 코너에 필요한 타력 구간일 수도 있습니다.');
  const reliftAt = key => scoped.find((row, i) => i && row.distance_pct >= corner.apex_pct && finite(scoped[i - 1][key]) && finite(row[key]) && scoped[i - 1][key] > 50 && row[key] < 25)?.distance_pct;
  scalar('throttle_relifts', '가속 후 다시 놓음', 'throttle', 1, '회', 1, 0, reliftAt('throttle'), reliftAt('referenceThrottle'),
    '정점 이후 스로틀을 50% 초과에서 25% 미만으로 다시 놓은 횟수입니다. 같은 위치의 조향과 속도를 함께 확인하세요.');
  scalar('steering_corrections', '조향 수정', 'steering', 1, '회', 1, 0, extreme('steering'), extreme('referenceSteering'),
    '구간 전체의 수정 횟수입니다. 그래프는 최대 조향 위치로 이동합니다. 수정이 모두 주행 오류라는 뜻은 아닙니다.');
  for (const [kind, channel] of [['abs', 'brake']]) {
    const a = controlWindow(controls[kind]?.target, corner.entry_pct, corner.exit_pct);
    const b = controlWindow(controls[kind]?.reference, corner.entry_pct, corner.exit_pct);
    if (!a.runs.length && !b.runs.length) continue;
    // An intervention is useful context even when durations are similar. Missing
    // coverage is never treated as zero, nor compared with an aggregate lap.
    const comparable = a.complete && b.complete;
    add({ key: `${kind}_active`, label: `${kind.toUpperCase()} 개입`, channel, at: a.at, refAt: b.at,
      difference: comparable ? a.duration - b.duration : 0, score: comparable ? 1 + Math.abs(a.duration - b.duration) / .2 : .8,
      value: a.pointOnly ? '개입 관측 · 시간 확인 필요' : a.observed ? `${a.duration.toFixed(2)}초${a.complete ? '' : ' 이상'}` : '내 랩 관측 없음',
      text: `내 랩 ${a.pointOnly ? '순간 개입 (시간 확인 필요)' : a.observed ? `${a.duration.toFixed(2)}초${a.complete ? '' : ' (일부 관측)'}` : '확인 필요'} / 기준 ${b.pointOnly ? '순간 개입 (시간 확인 필요)' : b.observed ? `${b.duration.toFixed(2)}초${b.complete ? '' : ' (일부 관측)'}` : '확인 필요'}`,
      hint: '실제 개입 신호의 첫 위치로 이동합니다. 개입 자체를 실수로 판단하지 않으며 페달·속도와 함께 확인하세요.' });
  }
  const deltaAt = pct => {
    if (!finite(pct)) return null;
    const after = rows.findIndex(row => row.distance_pct >= pct);
    const a = rows[Math.max(0, after - 1)], b = rows[after];
    if (b?.distance_pct === pct && finite(b.delta)) return b.delta;
    if (!a || !b || !finite(a.delta) || !finite(b.delta) || (b.distance_pct - a.distance_pct) * trackLength > 25) return null;
    if (a.distance_pct === b.distance_pct) return a.distance_pct === pct ? a.delta : null;
    if (pct < a.distance_pct || pct > b.distance_pct) return null;
    return a.delta + (b.delta - a.delta) * (pct - a.distance_pct) / (b.distance_pct - a.distance_pct);
  };
  for (const [key, label, from, to] of [['entry_loss', '진입 구간 시간', corner.entry_pct, corner.apex_pct], ['exit_loss', '탈출 구간 시간', corner.apex_pct, corner.exit_pct]]) {
    const a = deltaAt(from), b = deltaAt(to);
    const full = scoped.filter(row => row.distance_pct >= from && row.distance_pct <= to);
    if (!measured || !finite(a) || !finite(b) || Math.abs(b - a) < .08 || full.some((row, i) => !finite(row.delta) || (i && (row.distance_pct - full[i - 1].distance_pct) * trackLength > 25))) continue;
    add({ key, label, channel: 'delta', at: to, refAt: null, range: { from, to, type: 'interval' }, difference: b - a, score: Math.abs(b - a) / .08,
      value: `${Math.abs(b - a).toFixed(3)} s ${b > a ? '손실' : '이득'}`,
      text: `${Math.round(from * trackLength).toLocaleString()}–${Math.round(to * trackLength).toLocaleString()}m에서 생긴 차이`,
      hint: '구간 시작 이전의 누적 차이를 뺀 값입니다. 이 범위의 페달과 속도 변화를 먼저 비교하세요.' });
  }
  const ranked = notes.sort((a, b) => b.score - a.score);
  const limits = { brake: 2, throttle: 2, speed: 1, delta: 1, steering: 1, gear: 1 };
  const counts = {}, chosen = new Set();
  // Avoid five variations on the same input when other measured issues exist.
  for (const note of ranked) {
    if (chosen.size === 5) break;
    if ((counts[note.channel] || 0) >= (limits[note.channel] || 1)) continue;
    chosen.add(note); counts[note.channel] = (counts[note.channel] || 0) + 1;
  }
  for (const note of ranked) { if (chosen.size === 5) break; chosen.add(note); }
  return ranked.filter(note => chosen.has(note)).map((note, i) => ({ ...note, id: i + 1, jumpAt: note.at ?? note.refAt }));
}

// The centre of the visible window drives all corner-specific panels while panning.
// In gaps use the nearest interval; retain the current corner in a tie.
export function cornerAtWindow(corners, range, selected) {
  if (!corners.length || range[1] - range[0] >= .999999) return null;
  const pct = (range[0] + range[1]) / 2;
  const distance = corner => Math.max(corner.entry_pct - pct, pct - corner.exit_pct, 0);
  return corners.reduce((best, corner) => !best || distance(corner) < distance(best) ||
    (distance(corner) === distance(best) && corner.id === selected) ? corner : best, null)?.id;
}
