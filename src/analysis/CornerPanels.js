import React, { useState, useMemo } from "react";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "../components/ui/table";
import { LineChart, ChevronDown } from "lucide-react";
import CornerTrace from "../components/CornerTrace";
import { EmptyState } from './WorkspaceUi';
import { ISSUE_LABELS, cx, formatNumber, formatSigned } from './analysisShared';

export function PriorityCards({ priorities = [] }) {
  if (!priorities.length) return <EmptyState text="우선순위가 생성되지 않았습니다." />;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {priorities.map((priority) => (
        <article
          key={`${priority.rank}-${priority.corner}`}
          className="rounded-2xl border border-border bg-background/60 p-5"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                Priority {priority.rank}
              </div>
              <h3 className="mt-2 text-lg font-semibold text-foreground">{priority.corner}</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {priority.distance_m ? `${priority.distance_m} m 지점` : '전체 랩'} ·{' '}
                {ISSUE_LABELS[priority.issue_type] || priority.issue_type}
              </p>
            </div>
            {priority.loss_s !== null && priority.loss_s !== undefined && (
              <span className="rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 font-mono text-lg font-semibold text-destructive">
                +{Number(priority.loss_s).toFixed(3)}
              </span>
            )}
          </div>
          <p className="mt-4 text-base font-semibold leading-relaxed text-foreground">
            {priority.action}
          </p>
          <div className="mt-4 flex flex-col gap-2">
            {(priority.evidence || []).map((evidence) => (
              <div key={evidence} className="flex items-start gap-2 text-sm text-muted-foreground">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                <span>{evidence}</span>
              </div>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

export function CornerTable({ corners = [], trackLength, loadWindow, referenceLabel }) {
  const [graphFor, setGraphFor] = useState(null);
  const ordered = useMemo(
    () => [...corners].sort((a, b) => a.apex_pct - b.apex_pct),
    [corners]
  );
  if (!ordered.length) return <EmptyState text="코너 자동 분할 결과가 없습니다." />;
  // One value per line, never broken inside: "164 m · 1.45 g" and "+0.059 s" stay whole.
  const Value = ({ children, className = '' }) => <div className={cx('whitespace-nowrap font-mono tabular-nums', className)}>{children}</div>;
  const Sub = ({ children }) => <div className="mt-1 whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground">{children}</div>;
  const braking = (metrics) => {
    if (metrics?.braking_distance_m == null) return null;
    const decel = metrics.avg_decel_g != null && metrics.avg_decel_g >= 0.1 ? `${metrics.avg_decel_g.toFixed(2)} g` : '가벼운 제동';
    return `${Math.round(metrics.braking_distance_m)} m · ${decel}`;
  };
  const Band = ({ band }) => (band ? (
    <>
      <Value className={cx('font-semibold', band.delta_s > 0 ? 'text-destructive' : 'text-success')}>{formatSigned(band.delta_s)}</Value>
      <Sub>{Math.round(band.from_kph)}→{Math.round(band.to_kph)} km/h</Sub>
    </>
  ) : <span className="text-muted-foreground">--</span>);
  return (
    <div className="overflow-x-auto">
      <p className="mb-4 max-w-4xl text-sm leading-relaxed text-muted-foreground">
        <b className="text-foreground">제동</b>은 브레이크를 밟기 시작해 놓을 때까지(트레일 브레이킹 포함)의 거리와 평균 감속입니다.
        {' '}<b className="text-foreground">감속·가속</b>은 두 랩이 모두 지난 같은 속도 범위를 지나는 데 걸린 시간 차이입니다. 감속은 코너 진입 제동, 가속은 코너를 나와 풀스로틀로 달린 구간이고, +는 기준보다 느린 것입니다.
        {' '}각 칸의 아래 줄 회색 글씨는 기준 랩 값입니다.
        {loadWindow && <>{' '}<b className="text-primary">그래프 보기</b>나 줄을 누르면 그 코너를 이번 랩과 기준으로 겹친 그래프와, 다르게 한 곳의 설명이 펼쳐집니다.</>}
      </p>
      <Table className="w-full min-w-[1320px] border-collapse text-left text-sm">
        <TableHeader>
          <TableRow className="border-b border-border text-xs tracking-wider text-muted-foreground">
            {loadWindow && <TableHead className="w-[1%] px-3 py-3">그래프</TableHead>}
            <TableHead className="px-3 py-3">구간</TableHead>
            <TableHead className="px-3 py-3 text-right">델타</TableHead>
            <TableHead className="px-3 py-3">유형</TableHead>
            <TableHead className="px-3 py-3 text-right">최저 속도</TableHead>
            <TableHead className="px-3 py-3 text-right">풀스로틀 지점</TableHead>
            <TableHead className="px-3 py-3 text-right">ABS</TableHead>
            <TableHead className="px-3 py-3 text-right">제동</TableHead>
            <TableHead className="px-3 py-3 text-right">감속 시간</TableHead>
            <TableHead className="px-3 py-3 text-right">가속 시간</TableHead>
            <TableHead className="min-w-[280px] px-3 py-3">코칭</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ordered.map((corner) => {
            const target = corner.target || {};
            const reference = corner.reference || {};
            const fullThrottleDelta =
              target.full_throttle_pct != null && reference.full_throttle_pct != null
                ? (target.full_throttle_pct - reference.full_throttle_pct) * 100
                : null;
            return (
              <React.Fragment key={corner.id}>
              <TableRow
                className={cx('border-b border-border/60 align-top last:border-0 hover:bg-accent/30', loadWindow && 'cursor-pointer', graphFor === corner.id && 'bg-primary/5')}
                onClick={loadWindow ? (event) => { if (!event.target.closest('button')) setGraphFor(graphFor === corner.id ? null : corner.id); } : undefined}
              >
                {loadWindow && (
                  <TableCell className="px-3 py-4">
                    <button type="button" aria-expanded={graphFor === corner.id} aria-label={`${corner.label} 그래프 ${graphFor === corner.id ? '닫기' : '보기'}`}
                      onClick={() => setGraphFor(graphFor === corner.id ? null : corner.id)}
                      className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border px-3 py-2 text-sm font-semibold transition',
                        graphFor === corner.id ? 'border-primary bg-primary text-primary-foreground' : 'border-primary/50 bg-primary/10 text-primary hover:bg-primary/20')}>
                      <LineChart className="h-4 w-4" aria-hidden="true" />
                      {graphFor === corner.id ? '닫기' : '보기'}
                      <ChevronDown className={cx('h-4 w-4 transition-transform', graphFor === corner.id && 'rotate-180')} aria-hidden="true" />
                    </button>
                  </TableCell>
                )}
                <TableCell className="px-3 py-4">
                  <div className="whitespace-nowrap text-base font-semibold text-foreground">{corner.label}</div>
                  <Sub>{(corner.apex_pct * 100).toFixed(1)}% · {corner.distance_m} m · {corner.speed_class}</Sub>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className={cx('text-base font-semibold', corner.delta_s > 0 ? 'text-destructive' : 'text-success')}>{formatSigned(corner.delta_s)}</Value>
                </TableCell>
                <TableCell className="px-3 py-4">
                  <span className="whitespace-nowrap rounded-lg border border-border bg-background/70 px-2 py-1 text-xs font-semibold text-foreground">
                    {ISSUE_LABELS[corner.issue_type] || corner.issue_type}
                  </span>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className="text-foreground">{formatNumber(target.min_speed_kph, 1, ' km/h')}</Value>
                  <Sub>{formatNumber(reference.min_speed_kph, 1, ' km/h')}</Sub>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className="text-foreground">{target.full_throttle_pct != null ? `${(target.full_throttle_pct * 100).toFixed(1)}%` : '--'}</Value>
                  <Sub>차이 {fullThrottleDelta === null ? '--' : `${fullThrottleDelta > 0 ? '+' : ''}${fullThrottleDelta.toFixed(2)}%`}</Sub>
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Value className="text-foreground">{formatNumber(target.abs_duty_pct, 1, '%')}</Value>
                </TableCell>
                <TableCell className="px-3 py-4 text-right" data-testid={`braking-${corner.id}`}>
                  <Value className="text-foreground">{braking(target) || '--'}</Value>
                  <Sub>{braking(reference) || '--'}</Sub>
                  {target.brake_entry_speed_kph != null && <Sub>{Math.round(target.brake_entry_speed_kph)}→{Math.round(target.brake_end_speed_kph)} km/h</Sub>}
                </TableCell>
                <TableCell className="px-3 py-4 text-right" data-testid={`bands-${corner.id}`}>
                  <Band band={corner.braking_band} />
                </TableCell>
                <TableCell className="px-3 py-4 text-right">
                  <Band band={corner.drive_band} />
                </TableCell>
                <TableCell className="px-3 py-4 text-sm leading-relaxed text-foreground">
                  {corner.driver_action}
                </TableCell>
              </TableRow>
              {graphFor === corner.id && (
                <TableRow className="border-b border-border/60">
                  <TableCell colSpan={11} className="px-3 py-4">
                    <div className="sticky left-0 max-w-[900px]">
                      <CornerTrace corner={corner} trackLength={trackLength} load={loadWindow} referenceLabel={referenceLabel} />
                    </div>
                  </TableCell>
                </TableRow>
              )}
              </React.Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
