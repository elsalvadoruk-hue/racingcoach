import { Button } from './ui/button';
import CornerMap from './CornerMap';
import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { HelpHint } from './HelpGuide';
import { COMPARISON_COLORS } from '../chartTheme';
import { findRegisteredTrackMap, projectComparisonPaths } from '../lib/trackMapProjection';
import { findOfficialTrackMap } from '../lib/officialTrackMaps';
import {
  Area, CartesianGrid, Line, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
export { projectComparisonPaths } from '../lib/trackMapProjection';

const TARGET = COMPARISON_COLORS.target;
const REFERENCE = COMPARISON_COLORS.reference;
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const valueOf = (point, field, legacy) => {
  const value = Object.prototype.hasOwnProperty.call(point, field) ? point[field] : point[legacy];
  return finite(value) ? value : null;
};
const nearest = (points, pct) => points.reduce((best, point) => (
  !best || Math.abs(point.distance_pct - pct) < Math.abs(best.distance_pct - pct) ? point : best
), null);

export function prepareComparisonTrace(trace, trackLengthM) {
  const distanceScale = finite(trackLengthM) && trackLengthM > 0 ? trackLengthM : 100;
  return (Array.isArray(trace) ? trace : []).filter((point) => finite(point.distance_pct)).map((point) => ({
    ...point,
    distance: point.distance_pct * distanceScale,
    speed: valueOf(point, 'target_speed_kph'),
    referenceSpeed: valueOf(point, 'reference_speed_kph'),
    speedBand: finite(point.reference_speed_p25_kph) && finite(point.reference_speed_p75_kph) ? [point.reference_speed_p25_kph, point.reference_speed_p75_kph] : null,
    brake: valueOf(point, 'target_brake', 'brake') === null ? null : valueOf(point, 'target_brake', 'brake') * 100,
    referenceBrake: valueOf(point, 'reference_brake') === null ? null : valueOf(point, 'reference_brake') * 100,
    throttle: valueOf(point, 'target_throttle', 'throttle') === null ? null : valueOf(point, 'target_throttle', 'throttle') * 100,
    referenceThrottle: valueOf(point, 'reference_throttle') === null ? null : valueOf(point, 'reference_throttle') * 100,
    steering: valueOf(point, 'target_steering_deg', 'steering_deg'),
    referenceSteering: valueOf(point, 'reference_steering_deg'),
    gear: valueOf(point, 'target_gear', 'gear'),
    referenceGear: valueOf(point, 'reference_gear'),
    delta: valueOf(point, 'delta_s'),
  })).sort((a, b) => a.distance_pct - b.distance_pct);
}

const CHANNELS = [
  { key: 'speed', reference: 'referenceSpeed', label: '속도', unit: 'km/h', digits: 1 },
  { key: 'brake', reference: 'referenceBrake', label: '브레이크', unit: '%', domain: [0, 100], digits: 0 },
  { key: 'throttle', reference: 'referenceThrottle', label: '스로틀', unit: '%', domain: [0, 100], digits: 0 },
  { key: 'steering', reference: 'referenceSteering', label: '조향', unit: '°', digits: 1 },
  { key: 'gear', reference: 'referenceGear', label: '기어', unit: '', digits: 0, stepped: true },
  { key: 'delta', label: '누적 시간 차이', unit: '초', digits: 3 },
];

function mergeDetailPath(overview, detail, range) {
  if (!Array.isArray(detail)) return overview;
  const start = Math.min(range[0], detail[0]?.distance_pct ?? range[0]);
  const end = Math.max(range[1], detail[detail.length - 1]?.distance_pct ?? range[1]);
  const replacement = detail.length ? detail : [{ distance_pct: (range[0] + range[1]) / 2, lat: null, lon: null }];
  return [...overview.filter(point => point.distance_pct < start || point.distance_pct > end), ...replacement].sort((a, b) => a.distance_pct - b.distance_pct);
}

function cornerRange(corner, trackLengthM) {
  const padding = finite(trackLengthM) && trackLengthM > 0 ? 75 / trackLengthM : .02;
  return corner && finite(corner.entry_pct) && finite(corner.exit_pct)
    ? [Math.max(0, corner.entry_pct - padding), Math.min(1, corner.exit_pct + padding)] : [0, 1];
}

function Reading({ value, digits, unit, color }) {
  return <span style={{ color }}>{finite(value) ? `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}` : '측정 불가'}</span>;
}

export default function CornerComparison({ trace = [], corners = [], pathData = [], referencePath = [], referenceLineSource, trackIdentity, trackLengthM, referenceLabel = '기준 주행', onRangeChange, detailTrace, detailPath, detailReferencePath, detailLineSource, detailLoading = false, detailQuality, comparisonKey, requestedCorner }) {
  const [selected, setSelected] = useState(null);
  const [activePct, setActivePct] = useState(null);
  const [showAllCorners, setShowAllCorners] = useState(false);
  const [mapFocusVersion, setMapFocusVersion] = useState(0);
  const mapRegionRef = useRef(null);
  const syncId = useId();
  const appliedRequest = useRef(null);
  useEffect(() => { setSelected(null); setActivePct(null); }, [comparisonKey]);
  const hasDetail = selected !== null && Array.isArray(detailTrace) && !detailLoading;
  const effectiveTrace = hasDetail ? detailTrace : trace;
  const rows = useMemo(() => prepareComparisonTrace(effectiveTrace, trackLengthM), [effectiveTrace, trackLengthM]);
  const registeredMap = useMemo(() => findRegisteredTrackMap(trackIdentity), [trackIdentity]);
  const courseDiagram = useMemo(() => findOfficialTrackMap(trackIdentity), [trackIdentity]);
  const projected = useMemo(() => {
    const detailRange = cornerRange(corners.find(item => item.id === selected), trackLengthM);
    return projectComparisonPaths(
      hasDetail ? mergeDetailPath(pathData, detailPath, detailRange) : pathData,
      hasDetail ? mergeDetailPath(referencePath, detailReferencePath, detailRange) : referencePath,
      pathData,
      registeredMap,
    );
  }, [pathData, referencePath, detailPath, detailReferencePath, selected, hasDetail, corners, trackLengthM, registeredMap]);
  const orderedCorners = useMemo(() => (Array.isArray(corners) ? corners : []).slice().sort((a, b) => a.apex_pct - b.apex_pct), [corners]);
  useEffect(() => {
    if (!requestedCorner || appliedRequest.current === requestedCorner) return;
    const pct = requestedCorner.distance_pct;
    const match = finite(pct)
      ? orderedCorners.find((item) => finite(item.entry_pct) && finite(item.exit_pct) && item.entry_pct <= pct && pct <= item.exit_pct)
      : orderedCorners.find((item) => String(item.id) === String(requestedCorner.id));
    if (match) { appliedRequest.current = requestedCorner; setSelected(match.id); setActivePct(finite(match.apex_pct) ? match.apex_pct : null); }
  }, [requestedCorner, orderedCorners, comparisonKey]);
  const corner = orderedCorners.find((item) => item.id === selected);
  const priorityCorners = useMemo(() => orderedCorners.slice().sort((a, b) => (finite(b.delta_s) ? b.delta_s : -Infinity) - (finite(a.delta_s) ? a.delta_s : -Infinity)).slice(0, 12), [orderedCorners]);
  const displayedCorners = showAllCorners || orderedCorners.length <= 12 ? orderedCorners
    : [...priorityCorners, ...(corner && !priorityCorners.some((item) => item.id === corner.id) ? [corner] : [])].sort((a, b) => a.apex_pct - b.apex_pct);
  const isMeters = finite(trackLengthM) && trackLengthM > 0;
  const distanceScale = isMeters ? trackLengthM : 100;
  const range = cornerRange(corner, trackLengthM);
  useEffect(() => {
    if (onRangeChange) onRangeChange(selected === null ? null : cornerRange(orderedCorners.find((item) => item.id === selected), trackLengthM));
  }, [onRangeChange, selected, orderedCorners, trackLengthM]);
  const visibleRows = rows.filter((point) => point.distance_pct >= range[0] && point.distance_pct <= range[1]);
  const cursorRow = finite(activePct) && activePct >= range[0] && activePct <= range[1] ? nearest(visibleRows, activePct) : null;
  const selectCorner = (id, revealMap = false) => {
    setMapFocusVersion(value => value + 1);
    setSelected(id);
    if (revealMap) window.requestAnimationFrame(() => mapRegionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    const selectedCorner = orderedCorners.find((item) => item.id === id);
    setActivePct(finite(selectedCorner?.apex_pct) ? selectedCorner.apex_pct : null);
  };
  // The whole lap again: graphs, map line and map view, from wherever the driver is looking.
  const showFullLap = () => { setSelected(null); setActivePct(null); setMapFocusVersion(value => value + 1); };
  const handleChartMove = (state) => {
    const distance = Number(state?.activeLabel);
    if (state?.activeLabel !== undefined && state?.activeLabel !== null && Number.isFinite(distance)) setActivePct(distance / distanceScale);
  };
  const metrics = corner?.target || corner?.target_metrics;
  const referenceMetrics = corner?.reference || corner?.ref_metrics;

  return (
    <section aria-label="코너별 조작 비교" className="coach-panel coach-comparison">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div><div className="flex items-center gap-1"><h2 data-help="compare" className="text-lg font-semibold text-foreground">코너별 조작 비교</h2><HelpHint topic="compare" /></div><p className="mt-1 text-sm text-muted-foreground">코너를 고르면 지도와 그래프가 함께 이동합니다.</p></div>
        <Button variant="outline" type="button" data-help="fullLap" onClick={showFullLap} className=" border  px-3 py-2 text-sm  ">전체 랩 보기</Button>
      </div>
      <div className="coach-chart-legend mb-4 flex flex-wrap gap-x-5 gap-y-1 text-sm"><span style={{ color: TARGET }}>━ 내 주행</span><span style={{ color: REFERENCE }}>┄ {referenceLabel || '기준 주행'}</span></div>
      <div ref={mapRegionRef} className="coach-map-region">
        <CornerMap projected={projected.target} referenceProjected={projected.reference} trackMap={projected.map} courseDiagram={courseDiagram} geographic={projected.geographic} geographicMetersPerUnit={projected.metersPerUnit} mapStatus={projected.mapStatus} referenceLineSource={hasDetail ? detailLineSource || referenceLineSource : referenceLineSource} referenceLabel={referenceLabel} detailLoading={detailLoading} corners={displayedCorners} selected={corner} onFullLap={showFullLap} selectedRange={corner ? range : null} activePct={cursorRow?.distance_pct} onSelect={selectCorner} onHover={setActivePct} focusVersion={mapFocusVersion} />
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(230px,0.8fr)_minmax(0,1.8fr)]">
        <div className="min-w-0 flex flex-col gap-4">
          {orderedCorners.length > 12 && <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{showAllCorners ? `전체 ${orderedCorners.length}구간` : '시간 손실 우선 12구간'}</span><Button variant="outline" type="button" aria-pressed={showAllCorners} onClick={() => setShowAllCorners((value) => !value)} className=" border  px-3 py-2  ">{showAllCorners ? '손실 우선 보기' : `전체 ${orderedCorners.length}구간 보기`}</Button></div>}
          <div className="coach-corner-grid" aria-label="비교할 코너">
            <Button variant="outline" type="button" data-help="fullLap" onClick={showFullLap} aria-pressed={!corner} className="border px-3 py-2 text-sm font-semibold">전체 랩</Button>
            {displayedCorners.map((item) => <Button variant="outline" type="button" data-help="corner" key={item.id} onClick={() => selectCorner(item.id, true)} aria-pressed={item.id === corner?.id} className={` border px-3 py-2 text-sm ${item.id === corner?.id ? '  ' : '  '}`}>{item.label || item.id}</Button>)}
          </div>
          <div className="rounded-xl border border-border bg-background p-3 text-sm text-foreground">
            <p className="font-semibold text-foreground" data-testid="comparison-selection">{corner ? `${corner.label || corner.id} 비교` : '전체 랩 비교'}</p>
            <p className="mt-1" data-testid="comparison-range">{(range[0] * distanceScale).toFixed(isMeters ? 0 : 1)}–{(range[1] * distanceScale).toFixed(isMeters ? 0 : 1)} {isMeters ? 'm' : '%'}</p>
            <p className="mt-2 text-xs text-muted-foreground">자동 구간은 검출된 분석 범위이며 공식 트랙 턴 번호가 아닙니다. 지도는 두 실제 주행의 GPS 위치를 같은 좌표계로 비교합니다.</p>
            {detailLoading && <p role="status" className="mt-2 text-primary">정밀 구간 데이터 요청 중…</p>}
            {hasDetail && detailQuality && <p className="mt-2 text-xs text-muted-foreground">분석 간격 약 {finite(detailQuality.analysis_spacing_m) ? `${detailQuality.analysis_spacing_m.toFixed(1)}m` : '측정 불가'} · 유효 관측 {finite(detailQuality.target_coverage_ratio) ? `${Math.round(detailQuality.target_coverage_ratio * 100)}%` : '측정 불가'}</p>}
            {corner && <p className="mt-2">구간 시간 차이: {finite(corner.delta_s) ? `${corner.delta_s > 0 ? '+' : ''}${corner.delta_s.toFixed(3)}초` : '측정 불가'}<span className="mt-1 block text-xs text-muted-foreground">양수는 기준보다 느린 구간입니다.</span></p>}
            {metrics && <p className="mt-2">최저 속도: <Reading value={metrics.min_speed_kph} digits={1} unit="km/h" color={TARGET} /> / <Reading value={referenceMetrics?.min_speed_kph} digits={1} unit="km/h" color={REFERENCE} /></p>}
            {metrics && <p className="mt-1">탈출 속도: <Reading value={metrics.exit_speed_kph} digits={1} unit="km/h" color={TARGET} /> / <Reading value={referenceMetrics?.exit_speed_kph} digits={1} unit="km/h" color={REFERENCE} /></p>}
            {corner?.reference_consensus && <div className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground"><p>개별 랩 손실 IQR: <Reading value={corner.reference_consensus.loss_p25_s} digits={3} unit="초" /> ~ <Reading value={corner.reference_consensus.loss_p75_s} digits={3} unit="초" /></p><p className="mt-1">손실 동의율 {finite(corner.reference_consensus.loss_agreement_ratio) ? `${Math.round(corner.reference_consensus.loss_agreement_ratio * 100)}%` : '측정 불가'} · 관측 기준 {corner.reference_consensus.reference_count ?? 0}랩</p><p className="mt-1">구간 시간 차이는 종합 시간 곡선 기준이며 개별 랩 손실의 중앙값과 다를 수 있습니다.</p></div>}
            {corner?.driver_action && <p className="mt-3 border-t border-border pt-3">다음 주행: {corner.driver_action}</p>}
          </div>
        </div>
        <div className="min-w-0 flex flex-col gap-3">
          {!rows.length ? <p className="rounded-xl bg-background p-6 text-muted-foreground">비교할 거리별 데이터가 없습니다. 주행 파일을 분석하면 그래프가 표시됩니다.</p> : CHANNELS.map((channel) => {
            const hasTarget = visibleRows.some((point) => finite(point[channel.key]));
            const hasReference = channel.reference && visibleRows.some((point) => finite(point[channel.reference]));
            return <div key={channel.key} className="coach-chart-channel">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span data-help={`chart-${channel.key}`} className="flex items-center gap-1 font-semibold text-foreground">{channel.label}{channel.unit ? ` (${channel.unit})` : ''}<HelpHint topic={`chart-${channel.key}`} /></span>
                {cursorRow ? <span><Reading value={cursorRow[channel.key]} digits={channel.digits} unit={channel.unit} color={TARGET} />{channel.reference && <> / <Reading value={cursorRow[channel.reference]} digits={channel.digits} unit={channel.unit} color={REFERENCE} /></>}</span>
                  : <span className="text-muted-foreground">{!hasTarget && '내 주행: 측정 불가'}{!hasTarget && channel.reference && !hasReference && ' · '}{channel.reference && !hasReference && '기준: 측정 불가'}</span>}
              </div>
              {!hasTarget && !hasReference ? <p className="py-3 text-sm text-muted-foreground">{channel.key === 'delta' ? '기준 주행이 없거나 시간 데이터가 없어 비교할 수 없습니다.' : '이 구간의 채널 데이터가 없습니다.'}</p> : <div style={{ width: '100%', height: channel.key === 'speed' ? 155 : 110 }}>
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <ComposedChart data={visibleRows} syncId={syncId} syncMethod="value" onMouseMove={handleChartMove} margin={{ top: 5, right: 12, bottom: 0, left: 2 }}>
                    <CartesianGrid stroke={COMPARISON_COLORS.grid} strokeDasharray="3 3" />
                    <XAxis dataKey="distance" type="number" domain={[range[0] * distanceScale, range[1] * distanceScale]} allowDataOverflow tickFormatter={(value) => `${Math.round(value)}${isMeters ? 'm' : '%'}`} stroke={COMPARISON_COLORS.muted} tick={{ fontSize: 10 }} />
                    <YAxis width={48} domain={channel.domain || ['auto', 'auto']} allowDecimals={!channel.stepped} stroke={COMPARISON_COLORS.muted} tick={{ fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: '#223140', borderColor: '#3a4b60', borderRadius: 10, fontSize: 12, color: '#dde5ef' }} labelFormatter={(value) => `거리 ${Number(value).toFixed(isMeters ? 0 : 1)} ${isMeters ? 'm' : '%'}`} formatter={(value, name) => [finite(value) ? `${value.toFixed(channel.digits)} ${channel.unit}` : '측정 불가', name]} />
                    {channel.key === 'delta' && <ReferenceLine y={0} stroke={COMPARISON_COLORS.muted} />}
                    {corner && finite(corner.apex_pct) && <ReferenceLine x={corner.apex_pct * distanceScale} stroke={COMPARISON_COLORS.muted} strokeDasharray="2 4" />}
                    {cursorRow && <ReferenceLine x={cursorRow.distance} stroke={COMPARISON_COLORS.cursor} strokeDasharray="3 3" />}
                    {channel.key === 'speed' && visibleRows.some((point) => point.speedBand) && <Area dataKey="speedBand" name="기준 속도 IQR" stroke="none" fill={REFERENCE} fillOpacity={.12} connectNulls={false} isAnimationActive={false} tooltipType="none" />}
                    {hasReference && <Line dataKey={channel.reference} name={referenceLabel || '기준 주행'} stroke={REFERENCE} strokeDasharray="5 3" strokeWidth={2} type={channel.stepped ? 'stepAfter' : 'linear'} dot={false} connectNulls={false} isAnimationActive={false} />}
                    {hasTarget && <Line dataKey={channel.key} name={channel.key === 'delta' ? '내 주행 − 기준' : '내 주행'} stroke={TARGET} strokeWidth={2} type={channel.stepped ? 'stepAfter' : 'linear'} dot={false} connectNulls={false} isAnimationActive={false} />}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>}
            </div>;
          })}
          {!!rows.length && <p className="text-xs text-muted-foreground">같은 거리에서 비교합니다. 누적 시간 차이는 랩 시작 기준이며, 데이터가 없는 구간은 선을 연결하지 않습니다.</p>}
        </div>
      </div>
    </section>
  );
}
