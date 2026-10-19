import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "../components/ui/table";
import { Info, ShieldAlert, CheckCircle2, Layers3 } from "lucide-react";
import React from "react";
import { EmptyState, MetricMini } from './WorkspaceUi';
import { CLASSIFICATION_LABELS, cx, formatNumber, formatSigned } from './analysisShared';

export function TirePanel({ tires }) {
  if (!tires || !tires.wheels) return <EmptyState text="타이어 채널이 없습니다." />;
  const trend = tires.lap_trend || [];
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm leading-relaxed text-muted-foreground">
        대상 랩이 결승선을 지나기 직전 1초의 값입니다. iRacing 주행 파일에서 <b className="text-foreground">표면 온도와 열간 압력은 주행 중에 계속 기록</b>되고,
        카커스(내부) 온도와 마모는 피트에 들어갔을 때만 바뀌므로 여기서는 쓰지 않습니다. 표면 온도는 코너마다 크게 오르내리므로(한 랩에 30~40°C 차이),
        같은 지점(결승선 직전 직선)끼리 비교하는 값으로 보세요.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Object.entries(tires.wheels).map(([wheel, data]) => (
          <div key={wheel} className="rounded-2xl border border-border bg-background/60 p-4">
            <div className="flex items-center justify-between">
              <span className="font-mono text-lg font-semibold text-foreground">{wheel}</span>
              <span className="text-xs text-muted-foreground">표면 온도 (안·가운데·바깥)</span>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              {(data.surface_lmr_c || []).map((value, index) => (
                <div key={`${wheel}-${index}`} className="rounded-lg bg-muted px-2 py-2">
                  <div className="text-xs text-muted-foreground">{['L', 'M', 'R'][index]}</div>
                  <div className="mt-1 font-mono text-base font-semibold text-warning">
                    {formatNumber(value, 1, '°')}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-col gap-2 text-sm">
              <div className="flex justify-between text-muted-foreground">
                <span>열간 압력</span>
                <span className="font-mono font-bold text-foreground">
                  {formatNumber(data.hot_pressure_kpa, 1, ' kPa')}
                </span>
              </div>
              <div className="flex justify-between text-muted-foreground">
                <span>압력 상승</span>
                <span className="font-mono font-bold text-foreground">
                  {formatNumber(data.pressure_gain_kpa, 1, ' kPa')}
                </span>
              </div>
              <div className="flex justify-between text-muted-foreground">
                <span>냉간 압력(셋업)</span>
                <span className="font-mono text-foreground">{formatNumber(data.cold_pressure_kpa, 1, ' kPa')}</span>
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>카커스 온도·마모</span>
                <span>{data.carcass_status === 'DYNAMIC' && data.wear_status === 'DYNAMIC' ? '주행 중 기록됨' : '피트에서만 갱신 (사용 안 함)'}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <MetricMini
          label="랩당 표면 온도 변화"
          value={formatNumber(tires.surface_temp_slope_c_per_lap, 2, ' °C/lap')}
        />
        <MetricMini
          label="랩당 열간 압력 변화"
          value={formatNumber(tires.hot_pressure_slope_kpa_per_lap, 2, ' kPa/lap')}
        />
      </div>
      {trend.length > 0 && (
        <div className="overflow-x-auto rounded-2xl border border-border bg-background/50">
          <Table className="min-w-full text-left text-sm">
            <TableHeader className="border-b border-border bg-muted/70 text-xs uppercase tracking-wider text-muted-foreground">
              <TableRow>
                <TableHead className="px-4 py-3">랩</TableHead>
                <TableHead className="px-4 py-3">랩타임</TableHead>
                <TableHead className="px-4 py-3">평균 표면 온도</TableHead>
                <TableHead className="px-4 py-3">평균 노면 온도</TableHead>
                <TableHead className="px-4 py-3">평균 열간 압력</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody className="divide-y divide-slate-800">
              {trend.map((row) => (
                <TableRow key={row.lap_number}>
                  <TableCell className="px-4 py-3 font-mono font-semibold text-foreground">{row.lap_number}</TableCell>
                  <TableCell className="px-4 py-3 font-mono text-primary">
                    {row.lap_time_s ? `${Math.floor(row.lap_time_s / 60)}:${(row.lap_time_s % 60).toFixed(3).padStart(6, '0')}` : '--'}
                  </TableCell>
                  <TableCell className="px-4 py-3 font-mono text-warning">
                    {formatNumber(row.mean_surface_temp_c, 1, ' °C')}
                  </TableCell>
                  <TableCell className="px-4 py-3 font-mono text-muted-foreground" data-testid={`track-temp-${row.lap_number}`}>
                    {formatNumber(row.mean_track_temp_c, 1, ' °C')}
                  </TableCell>
                  <TableCell className="px-4 py-3 font-mono text-foreground">
                    {formatNumber(row.mean_hot_pressure_kpa, 1, ' kPa')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="rounded-xl border border-border bg-background/50 p-4 text-base leading-relaxed text-foreground">
        <Info className="mr-2 inline h-4 w-4 text-primary" />
        {tires.reason}
      </div>
    </div>
  );
}

export function RideAeroPanel({ rideHeight, aero }) {
  const splitter = rideHeight?.channels?.CFSRrideHeight;
  const measurable = rideHeight?.bottoming_detected !== null && rideHeight?.bottoming_detected !== undefined && rideHeight?.bottoming_available !== false;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div
        className={cx(
          'rounded-2xl border p-5',
          rideHeight?.bottoming_detected
            ? 'border-destructive/40 bg-destructive/20'
            : measurable ? 'border-success/30 bg-success/10' : 'border-border bg-background/40'
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Bottoming</div>
            <div
              className={cx(
                'mt-2 text-xl font-semibold',
                rideHeight?.bottoming_detected ? 'text-destructive' : measurable ? 'text-success' : 'text-muted-foreground'
              )}
            >
              {!measurable ? '측정 불가' : rideHeight?.bottoming_detected ? '검출됨' : '미검출'}
            </div>
          </div>
          {rideHeight?.bottoming_detected ? (
            <ShieldAlert className="h-8 w-8 text-destructive" />
          ) : measurable ? (
            <CheckCircle2 className="h-8 w-8 text-success" />
          ) : <Info className="h-8 w-8 text-muted-foreground" />}
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 text-sm">
          <MetricMini label="Splitter minimum" value={formatNumber(splitter?.minimum_mm, 1, ' mm')} />
          <MetricMini label="Splitter p0.1" value={formatNumber(splitter?.p0_1_mm, 1, ' mm')} />
          <MetricMini label="Events" value={String(rideHeight?.bottoming_events ?? '--')} />
          <MetricMini label="Median" value={formatNumber(splitter?.median_mm, 1, ' mm')} />
        </div>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">{rideHeight?.threshold_note}</p>
      </div>

      <div className="rounded-2xl border border-border bg-background/60 p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">High-speed aero</div>
            <div className="mt-2 text-xl font-semibold text-primary">
              Pitch {formatNumber(aero?.aero_pitch_median_mm, 1, ' mm')}
            </div>
          </div>
          <Layers3 className="h-8 w-8 text-primary" />
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 text-sm">
          <MetricMini label="Front median" value={formatNumber(aero?.front_ride_height_median_mm, 1, ' mm')} />
          <MetricMini label="Rear median" value={formatNumber(aero?.rear_ride_height_median_mm, 1, ' mm')} />
          <MetricMini label="High-speed splitter min" value={formatNumber(aero?.minimum_splitter_height_high_speed_mm, 1, ' mm')} />
          <MetricMini label="Rear wing" value={formatNumber(aero?.rear_wing_angle_deg, 1, '°')} />
        </div>
      </div>
    </div>
  );
}

export function ClassificationPanel({ classification }) {
  if (!classification) return null;
  const label = classification.label;
  const parallel = classification.parallel_assessment || {};
  const tone =
    label === 'SETUP_LIMITED'
      ? 'border-destructive/40 bg-destructive/20 text-destructive'
      : label === 'DRIVER_LIMITED'
      ? 'border-primary/40 bg-primary/20 text-primary'
      : 'border-warning/40 bg-warning/20 text-warning';
  return (
    <div className={cx('rounded-2xl border p-5', tone)}>
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
            Limitation classification
          </div>
          <div className="mt-2 text-2xl font-semibold">{CLASSIFICATION_LABELS[label] || label}</div>
        </div>
        <div className="rounded-xl border border-current/20 bg-background/50 px-4 py-3 text-center">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">Confidence</div>
          <div className="mt-1 font-mono text-xl font-semibold">
            {formatNumber((classification.confidence || 0) * 100, 0, '%')}
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-col gap-2 text-base leading-relaxed text-foreground">
        {(classification.reasons || []).map((reason) => (
          <div key={reason} className="flex items-start gap-2">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
            <span>{reason}</span>
          </div>
        ))}
      </div>

      {(parallel.external_gap_s !== undefined || parallel.note) && (
        <div className="mt-5 rounded-2xl border border-border/80 bg-background/65 p-4 text-foreground">
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Performance optimization
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            <MetricMini
              label="외부 기준 격차"
              value={parallel.external_gap_s === null || parallel.external_gap_s === undefined
                ? '--'
                : formatSigned(parallel.external_gap_s)}
            />
            <MetricMini
              label="드라이빙 개선"
              value={parallel.driver_improvement_required ? '필요' : '낮음'}
            />
            <MetricMini
              label="Setup Assist"
              value={parallel.setup_assist_opportunity ? 'A/B 후보 있음' : '후보 없음'}
            />
          </div>
          {parallel.note && (
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{parallel.note}</p>
          )}
        </div>
      )}
    </div>
  );
}
