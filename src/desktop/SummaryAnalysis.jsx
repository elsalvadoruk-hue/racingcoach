import React from 'react';
import CornerPhases from './CornerPhases';
import RacingLine from './RacingLine';
import { HandlingObservation, orderHandling, repeatedHandling } from './HandlingViews';
import FittedSections from './FittedSections';
import { signed, cornerLabel } from './sessionData';

const fields = [
  ['min_speed_kph', '최저 속도', 'km/h', 1, 1, 'measurement'],
  ['exit_speed_kph', '탈출 속도', 'km/h', 1, 1, 'measurement'],
  ['entry_speed_kph', '진입 속도', 'km/h', 1, 1, 'measurement'],
  ['peak_brake', '최대 제동압', '%', 100, 0, 'inputs'],
  ['braking_time_s', '제동 시간', '초', 1, 2, 'inputs'],
  ['coast_time_s', '타력 주행', '초', 1, 2, 'inputs'],
  ['steering_corrections', '조향 수정', '회', 1, 0, 'inputs'],
  ['throttle_relifts', '가속 후 다시 놓음', '회', 1, 0, 'inputs'],
  ['abs_duty_pct', 'ABS 개입 비율', '%', 1, 1, 'inputs'],
];

export function measuredSections(corner) {
  const target = corner.target || {}, reference = corner.reference || {};
  const rows = fields.filter(([key, , , , , type]) => target[`${type}_usable`] !== false
    && reference[`${type}_usable`] !== false && (Number.isFinite(target[key]) || Number.isFinite(reference[key])));
  const groups = [];
  for (let index = 0; index < rows.length; index += 2) groups.push(rows.slice(index, index + 2));
  return groups.map((group, index) => ({
    key: `measured-${index}`, content: <><h4>{index ? '페달·조향 측정값' : '속도·조작 측정값'}</h4>
      <table className="summary-measurements"><thead><tr><th>측정 항목</th><th>내 랩</th><th>기준 랩</th></tr></thead><tbody>{group.map(([key, label, unit, scale, digits]) =>
        <tr key={key}><th>{label}<small>{unit}</small></th>{[target, reference].map((values, i) => <td className="numeric" key={i}>{Number.isFinite(values[key]) ? (values[key] * scale).toFixed(digits) : '—'}</td>)}</tr>)}</tbody></table>
    </>,
  }));
}

export function noteSection(note) {
  return { key: `point-${note.key}`, content: <div className="summary-detail-point"><div><i className="insight-number">{note.id}</i><h4>{note.label}</h4><strong className="numeric">{note.value}</strong></div><p>{note.text}</p><small>{note.hint}</small></div> };
}

export default function SummaryAnalysis({ corner, notes, affected, names, repeats }) {
  const handling = orderHandling(corner.handling || []);
  const coverage = corner.handling_coverage || [];
  const sections = [
    { key: 'phases', content: <><h4>선택한 두 랩 비교</h4><CornerPhases corner={corner} /></> },
    ...notes.slice(0, 2).map(noteSection),
    ...handling.map((item, index) => ({ key: `handling-${index}`, content: <><h4>차 거동 · 내 다른 랩과 비교</h4><HandlingObservation item={item} /></> })),
    ...measuredSections(corner),
    ...(corner.line ? [{ key: 'racing-line', content: <RacingLine line={corner.line} /> }] : []),
    ...notes.slice(2).map(noteSection),
    ...(affected.length > 1 ? [{ key: 'other-corners', content: <><h4>같은 과제의 대상 구간</h4><ul className="summary-other-corners">{affected.map(item => <li key={item.id}><span>{item.id} · {cornerLabel(item, names)}</span><strong className="numeric">{signed(item.delta_s)} s</strong></li>)}</ul><small>구간별 관측 차이이며 원인을 확정하지 않습니다.</small></> }] : []),
    ...repeatedHandling(repeats).map((item, index) => ({ key: `repeat-${index}`, content: <><h4>반복되는 거동</h4><p>{item.label} · 지난 {item.files}개 주행에서 반복</p><HandlingObservation item={item} /></> })),
    ...(coverage.length ? [{ key: 'coverage', content: <><h4>내 다른 랩과 비교한 범위</h4><p>랩별 제동·회전·가속 {coverage.reduce((sum, item) => sum + item.classifiable, 0)}번 비교 · {coverage.reduce((sum, item) => sum + item.unknown, 0)}번 확인 필요</p><small>비슷한 조건의 랩이 부족하면 판정하지 않습니다. 선택한 기준 랩·Garage61 비교와는 별개입니다.</small></> }] : []),
  ];
  return <FittedSections sections={sections} label="공간에 맞춘 비교 분석" />;
}
