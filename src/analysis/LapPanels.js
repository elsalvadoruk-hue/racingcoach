import React, { useState, useCallback } from "react";
import { comparisonColors, chartDecoration } from "../chartTheme";
import { Button } from "../components/ui/button";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "../components/ui/table";
import { Input } from "../components/ui/input";
import { TimerReset, Route, TrendingDown, TrendingUp, Target, Gauge } from "lucide-react";
import { QUALITY_THEME, SESSION_MODE_META, cx, formatNumber, formatSigned, lapReproducibilityLabel, lapSelectionReasonLabel, sessionModeLabel, statusColor } from './analysisShared';
import { useCanvasRenderer } from './useCanvasRenderer';
import { EmptyState, MetricCard } from './WorkspaceUi';

export function SessionModeHint({ file, stored, current }) {
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

export function TraceChart({ trace = [], targetLap, referenceLap }) {
  const [mode, setMode] = useState('speed');
  const colors = comparisonColors();
  const lmu = document.documentElement.dataset.game === 'lmu';

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

      ctx.strokeStyle = chartDecoration('--trace-grid');
      ctx.lineWidth = 1;
      ctx.font = '13px ui-monospace, SFMono-Regular, Menlo, monospace';
      ctx.fillStyle = colors.muted;
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
          ctx.fillStyle = `${colors.reference}1f`;
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
        drawLine('reference_speed_kph', colors.reference, 2, true);
        drawLine('target_speed_kph', colors.target, 2.4);

        // LMU-only contrast increase approved by the user; preserve iRacing alpha.
        ctx.fillStyle = lmu ? 'rgba(248, 113, 113, 0.65)' : 'rgba(248, 113, 113, 0.18)';
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
          ctx.strokeStyle = chartDecoration('--trace-zero');
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
    [mode, trace, colors, lmu]
  );

  const canvasRef = useCanvasRenderer(draw, [draw]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-sm">
          {mode === 'speed' ? (
            <>
              <span className="flex items-center gap-1.5" style={{ color: colors.target }}>
                <span className="h-0.5 w-5" style={{ background: colors.target }} /> 대상 랩 {targetLap ?? '--'}
              </span>
              <span className="flex items-center gap-1.5" style={{ color: colors.reference }}>
                <span className="w-5 border-t-2 border-dashed" style={{ borderColor: colors.reference }} /> 기준 랩 {referenceLap ?? '--'}
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

export function QualityBanner({ quality }) {
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

export function LapSelectionSummary({ selection, onResetAuto, loading }) {
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

export function LapTable({ laps = [], targetLap, referenceLap, onTargetChange, onReferenceChange }) {
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

export function ComparisonSummary({ comparison, lapSelection }) {
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
