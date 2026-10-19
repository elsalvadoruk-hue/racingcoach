import React from 'react';
import { number, signed, distanceDifference } from './sessionData';

export default function Measurements({ corner, quality, warnings }) {
  if (!corner) return null;
  const items = [
    ['최저 속도', 'min_speed_kph', 'km/h'], ['탈출 속도', 'exit_speed_kph', 'km/h'],
    ['제동 거리', 'braking_distance_m', 'm'], ['제동 시간', 'braking_time_s', 's', 3],
    ['평균 감속', 'avg_decel_g', 'g', 2], ['최대 감속', 'peak_decel_g', 'g', 2],
    ['최대 브레이크', 'peak_brake', '%', 1, 100], ['ABS 개입', 'abs_duty_pct', '%'],
    ['코스팅', 'coast_time_s', 's', 3], ['평균 조향', 'avg_abs_steer_deg', '°'],
    ['최대 횡가속', 'peak_lat_g', 'g', 2], ['조향 수정', 'steering_corrections', '회', 0],
  ];
  return <><div className="measurement-grid">{items.map(([label, key, unit, digits = 1, scale = 1]) => <div key={key}><span>{label}</span><strong>{number(Number.isFinite(corner.target[key]) ? corner.target[key] * scale : null, digits)} <small>{unit}</small></strong><span>기준 {number(Number.isFinite(corner.reference[key]) ? corner.reference[key] * scale : null, digits)} {unit}</span></div>)}</div>
    <div className="measurement-bands">{[['같은 속도대 제동', corner.braking_band], ['같은 속도대 가속', corner.drive_band]].map(([title, band]) => <p key={title}>{title}: {band ? `${band.from_kph} → ${band.to_kph} km/h · 내 랩 ${number(band.target_s, 3)} s / 기준 ${number(band.reference_s, 3)} s · ${signed(band.delta_s)} s` : '비교할 측정값 없음'}</p>)}<p>제동 시작 {signed(distanceDifference(corner.target.brake_start_pct, corner.reference.brake_start_pct), 0)} m · + 기준보다 뒤 / − 앞</p></div>
    {quality && <p className="measurement-quality">분석 간격 {number(quality.analysis_spacing_m)} m · 유효 관측 {number(quality.target_coverage_ratio * 100, 1)}% · 관측이 없는 구간은 연결하지 않습니다.</p>}
    {corner.reference_consensus && <p className="measurement-quality">이 구간 기준 {corner.reference_consensus.reference_count}랩 · 서로 다른 드라이버 {corner.reference_consensus.unique_driver_count}명 · 손실 일치 {number(corner.reference_consensus.loss_agreement_ratio * 100, 0)}%</p>}
    {!!warnings?.length && <p className="measurement-quality">{warnings.join(' ')}</p>}
  </>;
}
