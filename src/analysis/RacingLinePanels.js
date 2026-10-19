import { COMPARISON_COLORS } from "../chartTheme";
import React, { useState, useMemo } from "react";
import { Button } from "../components/ui/button";
import { ISSUE_LABELS, cx, formatNumber, formatSigned } from './analysisShared';
import { EmptyState, MetricMini } from './WorkspaceUi';

export function buildRacingLineGeometry(target, reference, connector, options = {}) {
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

export function RacingLineSvg({ geometry, labelPrefix }) {
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

export function RacingLineMiniMap({ corner }) {
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

export function RacingLineComparisonPanel({ data }) {
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
