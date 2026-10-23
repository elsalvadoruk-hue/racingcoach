import React from 'react';

const places = { entry: '제동 시작', apex: '최저 속도 지점', exit: '탈출' };

export default function RacingLine({ line }) {
  if (line?.quality !== 'ok') return null;
  const parts = Object.entries(places).flatMap(([key, label]) => {
    const value = line[`${key}_m`];
    if (!Number.isFinite(value)) return [];
    if (Math.abs(value) < .5) return [`${label}에서 기준과 비슷합니다.`];
    const observedSide = line.side_words?.[key];
    const side = observedSide === '바깥' ? '바깥쪽' : observedSide;
    if (!['안쪽', '바깥쪽'].includes(side)) return [];
    return [`${label}에서 기준보다 ${Math.abs(value).toFixed(1)} m ${side}입니다.`];
  });
  const distance = line.path_length_delta_m;
  if (Number.isFinite(distance)) parts.push(Math.abs(distance) < .5
    ? '코너 안 주행 거리는 기준과 비슷합니다.'
    : `코너 안 주행 거리가 기준보다 ${Math.abs(distance).toFixed(1)} m ${distance > 0 ? '깁니다' : '짧습니다'}.`);
  if (!parts.length) return null;
  return <section className="measurement-quality" aria-label="주행선 차이"><h4>주행선</h4>
    <p>{parts.join(' ')}</p>
    <small>선택한 기준 랩과의 차이입니다. 트랙 경계까지의 거리는 측정하지 않습니다.</small>
  </section>;
}
