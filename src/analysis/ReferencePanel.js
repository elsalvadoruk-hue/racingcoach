import { Database, RefreshCw, CheckCircle2, TimerReset, Target, TrendingUp, Activity, Thermometer } from "lucide-react";
import { Button } from "../components/ui/button";
import { NativeSelect, NativeSelectOption } from "../components/ui/native-select";
import { Field, FieldLabel } from "../components/ui/field";
import React from "react";
import { ISSUE_LABELS, cx, formatLapSeconds, formatNumber, formatSigned } from './analysisShared';
import { EmptyState, ErrorMessage, MetricCard, MetricMini } from './WorkspaceUi';
import { RacingLineComparisonPanel } from './RacingLinePanels';
import { TraceChart } from './LapPanels';

export function ExternalReferencePanel({
  referenceStatus,
  files = [],
  selected = [],
  analysis,
  loading,
  collector,
  collectorLoading,
  apiStatus,
  onToggle,
  onPrimarySelect,
  onRefresh,
  onApply,
  onAutoSelect,
  onClear,
  onCollect,
  onForceCollect,
  onCancelCollect,
}) {
  const maxSelection = Number(referenceStatus?.display_limit || referenceStatus?.max_selection || 9);
  const minimumForSetup = Number(referenceStatus?.minimum_for_setup_conclusion || 2);
  const strongConsensusCount = Number(referenceStatus?.strong_consensus_count || 3);
  const recommendedCohort = Number(referenceStatus?.recommended_cohort_size || 5);
  const aggregate = analysis?.aggregate || {};
  const primaryGap = aggregate.gap_to_reference_s ?? aggregate.gap_to_reference_median_s;
  const warnings = analysis?.warnings || [];
  const errors = analysis?.errors || [];
  const selectedSet = new Set(selected);
  const selectedFiles = files.filter((file) => selectedSet.has(file.name));
  const health = collector?.health || referenceStatus?.collector_health || {};
  const capabilities = collector?.collector_capabilities || {
    api_token_configured: health?.collection_policy?.api_token_configured,
    preferred_mode: health?.collection_policy?.preferred_mode,
  };
  const job = collector?.job || null;
  const collectorBusy = Boolean(job?.busy);
  const jobState = job?.state || 'IDLE';
  const progress = Math.max(0, Math.min(100, Math.round(Number(job?.progress || 0) * 100)));
  const cacheTone = health?.enough_data && health?.cache_fresh
    ? 'border-success/30 bg-success/15'
    : health?.enough_data
    ? 'border-warning/30 bg-warning/15'
    : 'border-accent/30 bg-accent/15';

  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-2xl border border-accent/25 bg-accent/15 p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-foreground" />
              <h3 className="font-semibold text-foreground">Garage61 · IBT 조건 기반 Reference Library</h3>
            </div>
            <p className="mt-2 break-all font-mono text-sm text-foreground">
              {referenceStatus?.directory || '데이터 폴더의 Garage61_References'}
            </p>
            <p className="mt-2 max-w-5xl text-sm leading-relaxed text-muted-foreground">
              IBT에서 차량·트랙·레이아웃·노면온도·기온·연료·습도·바람·강수·가능한 BOP/타이어 조건을 읽습니다.
              Garage61 API로 최대 1,000개 후보를 조건별 단계 검색하고, 공개 telemetry만 내려받습니다. 토큰이 없으면 설정 → 설치 설정 → Garage61 개인 API 토큰에서 등록하세요. Playwright 설치 없이 이용할 수 있습니다.
              CSV를 검증한 뒤 대표 실주행 한 랩으로 속도·페달·라인·시간차를 비교합니다. 조건이 맞는 다른 드라이버의 기록은 구간별 손실과 개선 방향을 확인하는 보조 자료로 사용합니다. 아래 요약은 속도 손실과 GPS 차이가 큰 상위 2개 구간을 표시합니다.
            </p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className={cx(
                'rounded-lg border px-2.5 py-1 font-semibold',
                apiStatus?.connected
                  ? 'border-success/30 bg-success/10 text-success'
                  : capabilities?.api_token_configured
                  ? 'border-warning/30 bg-warning/10 text-warning'
                  : 'border-border bg-muted text-muted-foreground'
              )}>
                {apiStatus?.connected ? 'API CONNECTED' : capabilities?.api_token_configured ? 'API TOKEN SET' : 'API TOKEN 없음'}
              </span>
              <span className="rounded-lg border border-primary/25 bg-primary/10 px-2.5 py-1 font-semibold text-primary">
                API 수집
              </span>
              {apiStatus?.user_slug && <span className="rounded-lg border border-border bg-muted px-2.5 py-1 text-muted-foreground">{apiStatus.user_slug}</span>}
              {apiStatus?.subscription_plan && <span className="rounded-lg border border-border bg-muted px-2.5 py-1 text-muted-foreground">Plan {apiStatus.subscription_plan}</span>}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button variant="default"
              type="button"
              onClick={onCollect}
              disabled={collectorBusy || collectorLoading || !health?.enabled}
              className=" coach-primary-action px-4 py-2 text-sm   transition  disabled:cursor-not-allowed disabled: disabled:"
            >
              {collectorBusy ? 'Garage61 수집 중' : '조건 데이터 보충'}
            </Button>
            <Button variant="outline"
              type="button"
              onClick={onForceCollect}
              disabled={collectorBusy || collectorLoading || !health?.enabled}
              className=" border   px-4 py-2 text-sm   transition   disabled:opacity-40"
            >
              강제 새로 수집
            </Button>
            {collectorBusy && (
              <Button variant="outline"
                type="button"
                onClick={onCancelCollect}
                className=" border   px-4 py-2 text-sm   transition  "
              >
                수집 중지
              </Button>
            )}
            <Button variant="outline"
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className=" border   px-4 py-2 text-sm   transition   disabled:opacity-40"
            >
              캐시 새로고침
            </Button>
          </div>
        </div>
      </div>

      <div className={cx('rounded-2xl border p-5', cacheTone)}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={cx(
                'rounded-lg border px-2.5 py-1 text-xs font-semibold',
                health?.enough_data && health?.cache_fresh
                  ? 'border-success/30 bg-success/10 text-success'
                  : health?.enough_data
                  ? 'border-warning/30 bg-warning/10 text-warning'
                  : 'border-accent/30 bg-accent/10 text-foreground'
              )}>
                {health?.enough_data && health?.cache_fresh ? 'CACHE READY' : health?.enough_data ? 'CACHE STALE' : 'REFERENCE 부족'}
              </span>
              <span className="font-mono text-xs text-muted-foreground">{health?.recommendation || '--'}</span>
            </div>
            <p className="mt-3 text-sm leading-relaxed text-foreground">
              조건 유사 + 현재보다 빠른 기준 {health?.usable_reference_count ?? 0}/{health?.minimum_usable_references ?? 5}개,
              서로 다른 드라이버 {health?.usable_distinct_drivers ?? 0}/{health?.minimum_distinct_drivers ?? 3}명.
              {health?.last_context_scan_age_hours !== null && health?.last_context_scan_age_hours !== undefined
                ? ` 마지막 동일 조건 수집 ${Number(health.last_context_scan_age_hours).toFixed(1)}시간 전.`
                : ' 동일 조건의 수집 이력이 없습니다.'}
            </p>
            {(health?.reasons || []).length > 0 && (
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{health.reasons.join(' · ')}</p>
            )}
          </div>
          <div className="grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-4 xl:min-w-[470px]">
            <MetricMini label="적합 빠른 CSV" value={`${health?.usable_reference_count ?? 0}개`} />
            <MetricMini label="드라이버" value={`${health?.usable_distinct_drivers ?? 0}명`} />
            <MetricMini label="수집 목표" value={`${health?.target_reference_count ?? 8}개`} />
            <MetricMini label="캐시 유효" value={health?.cache_fresh ? 'YES' : 'NO'} />
          </div>
        </div>

        {(collectorBusy || (job && jobState !== 'IDLE')) && (
          <div className="mt-4 rounded-xl border border-border bg-background/70 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  {collectorBusy ? <RefreshCw className="h-4 w-4 animate-spin text-foreground" /> : <CheckCircle2 className="h-4 w-4 text-muted-foreground" />}
                  <span className="font-semibold text-foreground">{jobState}</span>
                  {job?.collector_mode && <span className="rounded-md border border-primary/25 bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">{job.collector_mode}</span>}
                </div>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{job?.message || 'Garage61 수집 상태 확인 중'}</p>
              </div>
              <div className="text-right font-mono text-xs text-muted-foreground">
                {job?.elapsed_s !== undefined ? `${Number(job.elapsed_s).toFixed(0)}s` : ''}
                {job?.scanned_rows !== undefined ? ` · ${job.scanned_rows} laps` : ''}
                {job?.context_csv_count !== undefined
                  ? ` · REF ${job.context_csv_count}/${job.target_count || health?.target_reference_count || 8}`
                  : job?.cached_csv_count !== undefined
                  ? ` · CSV ${job.cached_csv_count}/${job.target_count || health?.target_reference_count || 8}`
                  : ''}
              </div>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-accent">
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${progress}%` }} />
            </div>
            {jobState === 'WAITING_FOR_LOGIN' && (
              <p className="mt-3 text-sm text-warning">Garage61 전용 Chromium 창에서 한 번 로그인하면 브라우저 프로필에 세션이 유지됩니다.</p>
            )}
            {['AUTHENTICATING', 'RESOLVING_IDS', 'QUERYING_LAPS', 'RANKING', 'DOWNLOADING', 'VALIDATING', 'RETRY_WAIT', 'WAITING_RESULTS', 'SCANNING_ROWS'].includes(jobState) && (
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                API 모드에서는 조건 범위를 단계적으로 완화해 후보를 확보하고 CSV를 검증합니다. 브라우저 fallback은 기존 긴 타임아웃/백오프를 유지합니다. 분석 앱은 수집을 기다리지 않고 현재 캐시 결과를 계속 표시합니다.
              </p>
            )}
          </div>
        )}
      </div>

      {referenceStatus?.error && (
        <ErrorMessage message={`외부 기준 목록 조회 실패: ${referenceStatus.error}`} />
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <MetricMini label="호환 CSV" value={`${referenceStatus?.total_compatible ?? files.length}개`} />
        <MetricMini label="화면 표시" value={`${files.length}/${referenceStatus?.display_limit || 9}`} />
        <MetricMini label="현재 선택" value={`${selected.length}/${maxSelection}`} />
        <MetricMini label="자동 추천 상한" value={`${recommendedCohort}개`} />
        <MetricMini label="Setup Assist 최소" value={`${minimumForSetup}개`} />
        <MetricMini label="강한 컨센서스" value={`${strongConsensusCount}개+`} />
      </div>

      {Number(referenceStatus?.scanned_reference_count || 0) > 0 && Number(referenceStatus?.total_compatible || 0) === 0 && (
        <div className="rounded-xl border border-warning/30 bg-warning/20 px-4 py-3 text-sm leading-relaxed text-warning">
          Garage61 CSV는 디스크에서 발견됐지만 현재 IBT와 호환 판정된 파일이 없습니다.
          v2.9.3은 Top 2 코너 라인 비교에 차이 강조 x3·영역 채우기·최대 차이 확대 인셋을 추가해, 거의 겹치는 라인도 더 쉽게 읽도록 개선했습니다.
          {referenceStatus?.identity_rejections && Object.keys(referenceStatus.identity_rejections).length > 0 && (
            <span className="ml-2 font-mono text-xs text-warning">
              {Object.entries(referenceStatus.identity_rejections).map(([key, value]) => `${key}:${value}`).join(' · ')}
            </span>
          )}
        </div>
      )}

      {referenceStatus?.current_conditions && (
        <div className="rounded-2xl border border-border bg-background/55 p-4">
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">현재 IBT 조건 → Garage61 검색 기준</div>
          <div className="flex flex-wrap gap-2 text-sm">
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Track {formatNumber(referenceStatus.current_conditions.track_temp_c, 1, '°C')}</span>
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Air {formatNumber(referenceStatus.current_conditions.air_temp_c, 1, '°C')}</span>
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Fuel {formatNumber(referenceStatus.current_conditions.fuel_l, 1, ' L')}</span>
            {referenceStatus.current_conditions.fuel_used_l !== undefined && referenceStatus.current_conditions.fuel_used_l !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Fuel/Lap {formatNumber(referenceStatus.current_conditions.fuel_used_l, 2, ' L')}</span>}
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Humidity {formatNumber(referenceStatus.current_conditions.relative_humidity_pct, 0, '%')}</span>
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Wind {formatNumber(referenceStatus.current_conditions.wind_speed_kph, 1, ' km/h')}</span>
            {referenceStatus.current_conditions.fog_level_pct !== undefined && referenceStatus.current_conditions.fog_level_pct !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Fog {formatNumber(referenceStatus.current_conditions.fog_level_pct, 0, '%')}</span>}
            {referenceStatus.current_conditions.weight_penalty_kg !== undefined && referenceStatus.current_conditions.weight_penalty_kg !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">BOP {formatNumber(referenceStatus.current_conditions.weight_penalty_kg, 1, ' kg')}</span>}
            {referenceStatus.current_conditions.power_adjust_pct !== undefined && referenceStatus.current_conditions.power_adjust_pct !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Power {formatNumber(referenceStatus.current_conditions.power_adjust_pct, 2, '%')}</span>}
            {referenceStatus.current_conditions.tire_compound !== undefined && referenceStatus.current_conditions.tire_compound !== null && <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">Tire #{referenceStatus.current_conditions.tire_compound}</span>}
            <span className="rounded-lg border border-border bg-muted px-2 py-1 text-foreground">{referenceStatus.current_conditions.weather || 'Weather --'}</span>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <p className="text-xs leading-relaxed text-muted-foreground">
          같은 차량·트랙에서 조건과 자료 품질이 적합한 빠른 기록을 우선 추천합니다. 상위 {referenceStatus?.display_limit || 9}개와 현재 선택한 기록을 표시하며, 조건이 부족하면 추천 개수를 채우지 않습니다.
        </p>
        <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline"
          type="button"
          onClick={onAutoSelect}
          disabled={loading || files.length === 0}
          className=" border   px-4 py-2 text-sm   transition   disabled:opacity-40"
        >
          적합한 기록 자동 추천
        </Button>
        <Button variant="outline"
          type="button"
          onClick={onClear}
          disabled={loading || selected.length === 0}
          className=" border   px-4 py-2 text-sm   transition  disabled:opacity-40"
        >
          선택 해제
        </Button>
        <Button variant="default"
          type="button"
          onClick={onApply}
          disabled={loading || selected.length === 0}
          className=" coach-primary-action px-4 py-2 text-sm   transition  disabled:cursor-not-allowed disabled: disabled:"
        >
          선택 코호트로 재분석
        </Button>
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-background/25 p-3">
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {files.length ? files.map((file) => {
            const checked = selectedSet.has(file.name);
            const selectionFull = file.usable === false || (!checked && selected.length >= maxSelection);
            const similarity = Number(file.condition_similarity?.score ?? 0.5);
            const similarityPct = Math.round(similarity * 100);
            return (
              <label
                key={file.name}
                className={cx(
                  'block cursor-pointer rounded-2xl border p-4 transition',
                  checked
                    ? 'border-accent/45 bg-accent/25'
                    : selectionFull
                    ? 'cursor-not-allowed border-border bg-background/30 opacity-50'
                    : 'border-border bg-background/55 hover:border-accent/35'
                )}
              >
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={selectionFull}
                    onChange={() => onToggle(file.name)}
                    className="mt-1 h-4 w-4 accent-violet-500"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-foreground">{file.driver_name || 'Unknown Driver'}</span>
                      <div className="flex items-center gap-2">
                        <span className={cx(
                          'rounded-lg border px-2 py-1 text-xs font-semibold',
                          similarityPct >= 75
                            ? 'border-success/30 bg-success/10 text-success'
                            : similarityPct >= 55
                            ? 'border-warning/30 bg-warning/10 text-warning'
                            : 'border-border bg-muted text-muted-foreground'
                        )}>
                          조건 {similarityPct}%
                        </span>
                        <span className="rounded-lg border border-primary/25 bg-primary/10 px-2 py-1 font-mono text-sm font-semibold text-primary">
                          {file.lap_time || formatLapSeconds(file.lap_time_s)}
                        </span>
                      </div>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">{file.car_name}</p>
                    {file.usable === false && <p className="mt-2 text-sm leading-relaxed text-warning">비교 불가: {file.validation_error || '기준 텔레메트리의 시간·거리 데이터가 부족합니다.'}</p>}
                    <p className="mt-1 text-sm text-muted-foreground">
                      {file.track_name}{file.track_config ? ` · ${file.track_config}` : ''}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                      {file.metadata_enriched ? (
                        <span className="rounded-md border border-success/20 bg-success/5 px-2 py-1 text-success">{file.metadata_source === 'GARAGE61_API' ? 'API META' : 'WEB META'}</span>
                      ) : (
                        <span className="rounded-md border border-border bg-muted px-2 py-1 text-muted-foreground">CSV ONLY</span>
                      )}
                      {file.faster_than_current_best && <span className="rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-primary">FASTER</span>}
                      {file.condition_similarity?.setup_compatible === false && <span className="rounded-md border border-destructive/20 bg-destructive/5 px-2 py-1 text-destructive">SETUP 비교 제외</span>}
                      {file.setup_available && <span className="rounded-md border border-warning/20 bg-warning/5 px-2 py-1 text-warning">SETUP 공개</span>}
                      {!file.setup_available && file.setup_access === 'LOCKED' && <span className="rounded-md border border-border bg-muted px-2 py-1 text-muted-foreground">SETUP 잠김</span>}
                      {file.track_temp_c !== undefined && file.track_temp_c !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Track {formatNumber(file.track_temp_c, 1, '°C')}</span>}
                      {file.air_temp_c !== undefined && file.air_temp_c !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Air {formatNumber(file.air_temp_c, 1, '°C')}</span>}
                      {file.fuel_l !== undefined && file.fuel_l !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Fuel {formatNumber(file.fuel_l, 1, 'L')}</span>}
                      {file.fuel_used_l !== undefined && file.fuel_used_l !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Fuel/Lap {formatNumber(file.fuel_used_l, 2, 'L')}</span>}
                      {file.fog_level_pct !== undefined && file.fog_level_pct !== null && Number(file.fog_level_pct) > 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Fog {formatNumber(file.fog_level_pct, 0, '%')}</span>}
                      {file.track_wetness_pct !== undefined && file.track_wetness_pct !== null && Number(file.track_wetness_pct) > 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Wet {formatNumber(file.track_wetness_pct, 0, '%')}</span>}
                      {file.weather && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">{file.weather}</span>}
                      {file.track_usage_pct !== undefined && file.track_usage_pct !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Usage {formatNumber(file.track_usage_pct, 0, '%')}</span>}
                      {file.driver_rating !== undefined && file.driver_rating !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">iR {Math.round(Number(file.driver_rating))}</span>}
                      {file.weight_penalty_kg !== undefined && file.weight_penalty_kg !== null && Number(file.weight_penalty_kg) !== 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">BOP {formatNumber(file.weight_penalty_kg, 1, 'kg')}</span>}
                      {file.power_adjust_pct !== undefined && file.power_adjust_pct !== null && Number(file.power_adjust_pct) !== 0 && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Power {formatNumber(file.power_adjust_pct, 2, '%')}</span>}
                      {file.tire_compound !== undefined && file.tire_compound !== null && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">Tire #{file.tire_compound}</span>}
                      {file.track_usage && <span className="rounded-md bg-muted px-2 py-1 text-muted-foreground">{file.track_usage}</span>}
                    </div>
                    {(file.condition_similarity?.reasons || []).length > 0 && (
                      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                        {(file.condition_similarity.reasons || []).slice(0, 4).join(' · ')}
                      </p>
                    )}
                    <p className="mt-2 truncate text-xs text-muted-foreground" title={file.name}>{file.name}</p>
                  </div>
                </div>
              </label>
            );
          }) : (
            <div className="lg:col-span-2 2xl:col-span-3">
              <EmptyState text={collectorBusy
                ? '현재 IBT 조건에 맞는 Garage61 기준을 백그라운드에서 수집 중입니다. 분석 앱은 기존 IBT 결과를 계속 사용할 수 있습니다.'
                : '현재 IBT와 같은 차량·트랙·레이아웃의 Garage61 CSV가 없습니다. 조건 데이터 보충을 누르면 IBT 정보를 기준으로 Garage61 수집을 시작합니다.'} />
            </div>
          )}
        </div>
      </div>

      {selected.length > 0 && onPrimarySelect && <Field><FieldLabel htmlFor="library-primary-reference">Garage61 대표 기준</FieldLabel><NativeSelect id="library-primary-reference" value={selected[0]} disabled={loading} onChange={event => onPrimarySelect(event.target.value)}>{selected.map(name => {
        const file = files.find(item => item.name === name);
        return <NativeSelectOption key={name} value={name}>{file?.driver_name || name} · {file?.lap_time || formatLapSeconds(file?.lap_time_s)}</NativeSelectOption>;
      })}</NativeSelect><p className="text-sm text-muted-foreground">대표 랩의 실제 입력과 라인을 함께 비교합니다. 다른 선택 기록은 보조 근거이며, 적용 버튼을 누르면 기준이 바뀝니다.</p></Field>}

      <div className={cx(
        'rounded-2xl border p-4 text-sm leading-relaxed',
        selected.length >= minimumForSetup
          ? 'border-success/25 bg-success/15 text-success'
          : 'border-warning/30 bg-warning/20 text-warning'
      )}>
        선택 {selected.length}/{maxSelection}개. 단일 CSV는 셋업 결론에 사용하지 않습니다.
        {selected.length === 1
          ? ' 현재는 비교·코칭 전용입니다.'
          : selected.length >= strongConsensusCount
          ? ' 여러 드라이버·조건 유사도·코너 동의율을 함께 사용해 강한 컨센서스를 시도합니다.'
          : selected.length >= minimumForSetup
          ? ' 2개 기준은 PROVISIONAL Setup Assist로 사용하며 신뢰도 상한을 낮춥니다.'
          : ` 최소 ${minimumForSetup}개가 있어야 Setup Assist 후보를 만들 수 있습니다.`}
      </div>

      {selectedFiles.length > 0 && (
        <div className="text-xs text-muted-foreground">
          선택 코호트: {selectedFiles.map((file) => `${file.driver_name || 'Unknown'} ${file.lap_time || ''}`).join(' · ')}
        </div>
      )}

      {analysis?.status === 'READY' && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 2xl:grid-cols-6">
            <MetricCard label="내 대상 랩" value={aggregate.target_time || '--'} icon={TimerReset} tone="cyan" />
            <MetricCard label={aggregate.reference_label || "대표 실주행 기준"} value={aggregate.reference_time || aggregate.reference_median_time || '--'} icon={Target} tone="violet" />
            <MetricCard
              label="외부 대비 격차"
              value={formatSigned(primaryGap)}
              note={primaryGap == null ? '측정 불가' : Number(primaryGap) > 0 ? '내 랩이 느림' : Number(primaryGap) < 0 ? '내 랩이 빠름' : '동일한 랩타임'}
              icon={TrendingUp}
              tone={primaryGap == null ? 'slate' : Number(primaryGap) > 0 ? 'rose' : 'emerald'}
            />
            <MetricCard
              label="컨센서스"
              value={analysis.consensus_tier || '--'}
              note={`빠른 기준 ${analysis.decision_reference_count || 0}개`}
              icon={Database}
              tone={analysis.strong_consensus ? 'emerald' : analysis.decision_ready ? 'amber' : 'slate'}
            />
            <MetricCard
              label="서로 다른 드라이버"
              value={`${analysis.unique_driver_count || 0}명`}
              note={`선택 ${analysis.selected_reference_count || 0}개`}
              icon={Activity}
              tone="slate"
            />
            <MetricCard
              label="조건 유사도"
              value={aggregate.condition_similarity_median !== null && aggregate.condition_similarity_median !== undefined
                ? `${Math.round(Number(aggregate.condition_similarity_median) * 100)}%`
                : '--'}
              note={`메타 보강 ${aggregate.metadata_enriched_count || 0}개`}
              icon={Thermometer}
              tone="slate"
            />
          </div>

          {(warnings.length > 0 || errors.length > 0) && (
            <div className="flex flex-col gap-2">
              {warnings.map((message, index) => (
                <div key={`warning-${index}`} className="rounded-xl border border-warning/25 bg-warning/15 p-3 text-sm text-warning">
                  {message}
                </div>
              ))}
              {errors.map((message, index) => (
                <div key={`error-${index}`} className="rounded-xl border border-destructive/25 bg-destructive/15 p-3 text-sm text-destructive">
                  {message}
                </div>
              ))}
            </div>
          )}

          <RacingLineComparisonPanel data={analysis.line_comparison} />

          {(analysis.top_losses || []).length > 0 && (
            <div>
              <div className="mb-3 flex items-center justify-between gap-3">
                <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">대표 실주행 대비 주요 손실</h4>
                <span className="text-xs text-muted-foreground">대표 랩 대비 손실 · 다른 드라이버의 관측 범위</span>
              </div>
              <div className="grid gap-3 lg:grid-cols-3">
                {(analysis.top_losses || []).slice(0, 6).map((corner) => (
                  <div key={corner.id} className="rounded-2xl border border-border bg-background/60 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="font-semibold text-foreground">{corner.label}</div>
                        <div className="mt-1 text-xs text-muted-foreground">{ISSUE_LABELS[corner.issue_type] || corner.issue_type}</div>
                      </div>
                      <span className="font-mono font-semibold text-destructive">{formatSigned(corner.delta_s)}</span>
                    </div>
                    <div className="mt-3 text-sm text-muted-foreground">
                      손실 동의율 {formatNumber((corner.reference_consensus?.loss_agreement_ratio || 0) * 100, 0, '%')}
                      {' · '}기준 {corner.reference_consensus?.reference_count || 0}개
                      {' · '}드라이버 {corner.reference_consensus?.unique_driver_count || 0}명
                    </div>
                    <div className="mt-1 font-mono text-xs text-muted-foreground">
                      IQR {formatSigned(corner.reference_consensus?.loss_p25_s)} ~ {formatSigned(corner.reference_consensus?.loss_p75_s)}
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-foreground">{corner.driver_action}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(analysis.visualizations?.trace || []).length > 0 && (
            <TraceChart
              trace={analysis.visualizations.trace}
              targetLap={aggregate.target_lap}
              referenceLap={aggregate.reference_label || 'Garage61 기준'}
            />
          )}
        </>
      )}
    </div>
  );
}
