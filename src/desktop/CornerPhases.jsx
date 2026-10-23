import React from 'react';
import { Alert, AlertDescription } from '../components/ui/alert';
import { cn } from '../lib/utils';

const eventLabels = { brake_start: '제동 시작', brake_release: '브레이크 놓음', min_speed: '최저 속도 지점', throttle_first: '가속 시작', full_throttle: '풀스로틀' };
const phaseLabels = { approach: '진입 전 제동', braking: '제동', rotation: '회전', drive: '가속', straight_after: '탈출 뒤 직선' };

function eventText(item) {
  const direction = item.event === 'min_speed' ? (item.delta_m >= 0 ? '뒤' : '앞') : (item.delta_m >= 0 ? '늦음' : '이름');
  return `${eventLabels[item.event]} ${Math.abs(item.delta_m).toFixed(0)} m ${direction}`;
}

export function DeviationLine({ deviation }) {
  if (!deviation || !eventLabels[deviation.event] || !Number.isFinite(deviation.delta_m)) return null;
  const later = (deviation.then || []).filter(item => eventLabels[item.event] && Number.isFinite(item.delta_m)
    && Math.abs(item.delta_m) >= (item.threshold_m ?? 3));
  return <span className="first-deviation">처음 달라진 조작: {eventText(deviation)}{later.length > 0 && ` · 그 뒤 ${later.map(eventText).join(' · ')}`}</span>;
}

export default function CornerPhases({ corner }) {
  const phases = corner?.phases;
  if (!phases) return <DeviationLine deviation={corner?.first_deviation} />;
  const straightLoss = phases.straight_after?.delta_s;
  const keys = [...(phases.approach ? ['approach'] : []), 'braking', 'rotation', 'drive', 'straight_after'];
  return <section className="corner-phases" aria-label="코너 단계별 시간 차이">
    {Number.isFinite(straightLoss) && Number.isFinite(corner.delta_s) && straightLoss > 0 && straightLoss > corner.delta_s &&
      <Alert><AlertDescription>탈출 속도 차이가 직선에서 더 크게 이어졌습니다.</AlertDescription></Alert>}
    <dl className="phase-bar">{keys.map(key => {
      const value = phases[key]?.delta_s;
      return <div key={key} className={cn('phase-cell', ['approach', 'straight_after'].includes(key) && 'phase-outside')} data-phase={key}>
        <dt>{phaseLabels[key]}</dt><dd className={cn('numeric', Number.isFinite(value) && value > 0 && 'loss', Number.isFinite(value) && value < 0 && 'gain')}>
          {Number.isFinite(value) ? `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(2)}초` : '—'}</dd>
      </div>;
    })}</dl>
    <small className="phase-note">+ 느림 / − 빠름 · 진입 전 제동·탈출 뒤 직선은 코너 합계에 미포함</small>
    <DeviationLine deviation={corner.first_deviation} />
  </section>;
}
