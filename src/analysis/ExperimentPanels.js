import React, { useState, useRef, useEffect, useMemo } from "react";
import { Button } from "../components/ui/button";
import LapDistribution from "../components/LapDistribution";
import GGDiagram from "../components/GGDiagram";
import DamperHistogram from "../components/DamperHistogram";
import { HelpHint } from "../components/HelpGuide";
import { ChevronDown, CheckCircle2 } from "lucide-react";
import { Input } from "../components/ui/input";
import { ErrorMessage, MetricMini } from './WorkspaceUi';
import { assignedModeForFile, comparisonContextLabel, cx, formatNumber, formatSigned, lapBadgeClass, sameVehicleAndTrack, sessionModeLabel } from './analysisShared';
import { requestJson } from './analysisClient';

export function LearningEffectSummary({ result, memoryRecord }) {
  const effects = result?.effect_profile?.overall_corner_deltas || {};
  const learning = memoryRecord?.learning_update;
  if (!result) return null;
  return (
    <div className="mt-4 flex flex-col gap-3">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <MetricMini label="최저속 변화" value={formatSigned(effects.min_speed_kph, 2, ' km/h')} />
        <MetricMini label="탈출속도 변화" value={formatSigned(effects.exit_speed_kph, 2, ' km/h')} />
        <MetricMini label="ABS 변화" value={formatSigned(effects.abs_duty_pct, 2, '%p')} />
        <MetricMini label="코스팅 변화" value={formatSigned(effects.coast_time_s, 3, ' s')} />
        <MetricMini label="풀스로틀 위치 (−=앞당김)" value={formatSigned(effects.full_throttle_shift_m, 1, ' m')} />
      </div>
      {learning && (
        <div className={cx('rounded-xl border p-3 text-sm leading-relaxed', learning.stored_events > 0 ? 'border-success/25 bg-success/15 text-success' : 'border-border bg-background/40 text-muted-foreground')}>
          {learning.stored_events > 0
            ? `개인화 학습 ${learning.stored_events}건 갱신됨${learning.confounded ? ' · 여러 셋업 항목이 함께 바뀌어 낮은 가중치로 저장' : ' · 단일 변경 A/B로 강한 학습 근거 저장'}`
            : (learning.reason || '셋업 민감도 학습 항목 없음')}
        </div>
      )}
    </div>
  );
}

export function ABAxisCard({ label, axis, detail }) {
  const status = axis?.status || 'UNAVAILABLE';
  const tone = status === 'IMPROVED'
    ? 'border-success/25 bg-success/15 text-success'
    : status === 'WORSENED'
    ? 'border-destructive/25 bg-destructive/15 text-destructive'
    : status === 'NEUTRAL'
    ? 'border-border bg-background/45 text-foreground'
    : 'border-border bg-background/35 text-muted-foreground';
  return (
    <div className={cx('rounded-xl border p-3', tone)}>
      <div className="text-xs font-semibold uppercase tracking-wider opacity-70">{label}</div>
      <div className="mt-1 text-lg font-semibold">{status}</div>
      {detail && <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{detail}</div>}
    </div>
  );
}

export function ExperimentManager({ files = [], sessionMode, preferredBaseline = '', onStored, trial, goal = 'COMFORT', onComparison }) {
  const [baseline, setBaseline] = useState('');
  const [candidate, setCandidate] = useState('');
  const [result, setResult] = useState(null);
  const [memoryRecord, setMemoryRecord] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const preferredBaselineRef = useRef('');
  const requestRef = useRef({ id: 0, controller: null });
  const contextKey = JSON.stringify([baseline, candidate, trial?.session_mode || sessionMode, trial?.id || null, trial?.driver_feedback || null, trial?.goal || goal]);
  const contextRef = useRef(contextKey);
  contextRef.current = contextKey;

  useEffect(() => {
    if (!trial) return;
    setBaseline(trial.baseline_file);
    setCandidate(trial.candidate_file || '');
    setResult(trial.comparison || null);
  }, [trial?.id, trial?.baseline_file, trial?.candidate_file, trial?.comparison, trial]);

  useEffect(() => {
    requestRef.current.controller?.abort();
    setLoading(false);
    return () => requestRef.current.controller?.abort();
  }, [contextKey]);

  useEffect(() => {
    if (trial || !preferredBaseline || preferredBaselineRef.current === preferredBaseline) return;
    if (files.some((file) => file.name === preferredBaseline)) {
      preferredBaselineRef.current = preferredBaseline;
      setBaseline(preferredBaseline);
      setCandidate('');
      setResult(null);
      setMemoryRecord(null);
      setError('');
    }
  }, [files, preferredBaseline, trial]);

  const baselineFile = useMemo(
    () => files.find((file) => file.name === baseline) || (trial?.baseline_file === baseline ? trial.context : null),
    [baseline, files, trial]
  );

  const candidateFiles = useMemo(() => {
    if (!baselineFile) return [];
    return files.filter(
      (file) => file.analyzable !== false && file.name !== baselineFile.name && sameVehicleAndTrack(file, baselineFile)
    );
  }, [baselineFile, files]);

  useEffect(() => {
    if (trial) return;
    if (!files.length) {
      setBaseline('');
      setCandidate('');
      return;
    }
    if (!files.some((file) => file.name === baseline)) {
      const preferred = files.some((file) => file.name === preferredBaseline)
        ? preferredBaseline
        : files.find((file) => file.analyzable !== false)?.name || '';
      setBaseline(preferred);
    }
  }, [baseline, files, preferredBaseline, trial]);

  useEffect(() => {
    if (!trial && !candidateFiles.some((file) => file.name === candidate)) {
      setCandidate('');
    }
  }, [candidate, candidateFiles, trial]);

  const resetComparison = () => {
    setResult(null);
    setMemoryRecord(null);
    setError('');
  };

  const handleBaselineChange = (filename) => {
    resetComparison();
    setBaseline(filename);
  };

  const handleCandidateChange = (filename) => {
    resetComparison();
    setCandidate(filename);
  };

  const compare = async () => {
    requestRef.current.controller?.abort();
    const requestId = requestRef.current.id + 1;
    const controller = new AbortController();
    requestRef.current = { id: requestId, controller };
    const requestedContext = contextKey;
    const isCurrent = () => !controller.signal.aborted && requestRef.current.id === requestId && contextRef.current === requestedContext;
    setError('');
    setLoading(true);
    try {
      const payload = await requestJson('/api/experiments/compare', {
        signal: controller.signal,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          baseline_file: baseline,
          candidate_file: candidate,
          session_mode: trial?.session_mode || sessionMode,
          goal: trial?.goal || goal,
          driver_feedback: ['IMPROVED', 'SAME', 'WORSENED'].includes(trial?.driver_feedback?.change) ? trial.driver_feedback : null,
          trial_id: trial?.id || null,
        }),
      });
      if (!isCurrent()) return;
      setResult(payload.comparison);
      if (onComparison && trial) onComparison(payload.comparison);
      setMemoryRecord(payload.memory_record || null);
      if (payload.memory_record?.saved && onStored) await onStored(payload.memory_record || null);
    } catch (requestError) {
      if (isCurrent() && requestError.name !== 'AbortError') setError(requestError.message);
    } finally {
      if (isCurrent()) setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-primary/20 bg-primary/10 p-3 text-sm leading-relaxed text-muted-foreground">
        두 IBT를 PACE · CONSISTENCY · DRIVEABILITY로 나눠 비교합니다. 재현성은 실패 조건이 아니라 Baseline의 자연 편차로 계산되어 변화가 실제 효과인지 판단하는 기준이 됩니다. 일반 A/B는 최소 3랩, 권장 5랩이며 같은 차량·트랙·레이아웃만 비교합니다.
        {sessionMode === 'QUALIFYING' && ' 퀄리파잉 모드는 기존 규칙상 각 셋업 유효 계측랩 2개를 사용합니다.'}
      </div>

      {baselineFile && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-background/55 px-4 py-3 text-sm">
          <span className="font-bold text-foreground">{comparisonContextLabel(baselineFile)}</span>
          <span className="font-mono text-primary">
            비교 가능한 다른 IBT {candidateFiles.length}개
          </span>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
        <FileSelect
          label="A · 변경 전 주행"
          value={baseline}
          onChange={handleBaselineChange}
          files={files}
          disabled={Boolean(trial)}
        />
        <FileSelect
          label="B · 변경 후 주행 (동일 차량/트랙)"
          value={candidate}
          onChange={handleCandidateChange}
          files={candidateFiles}
          disabled={Boolean(trial)}
        />
        <Button variant="default"
          type="button"
          onClick={compare}
          data-help="abResult"
          disabled={loading || !baseline || !candidate || baseline === candidate || baselineFile?.analyzable === false}
          className="self-end  coach-primary-action px-5 py-3 text-base   transition  disabled:cursor-not-allowed disabled: disabled:"
        >
          {loading ? '비교 중' : 'A/B 결과 확인'}
        </Button>
      </div>

      {baselineFile && candidateFiles.length === 0 && (
        <div className="rounded-xl border border-warning/30 bg-warning/20 p-4 text-base text-warning">
          선택한 기준 파일과 같은 차량·트랙·레이아웃의 다른 IBT가 없습니다.
        </div>
      )}
      {error && <ErrorMessage message={error} />}
      {result && (
        <div className="rounded-2xl border border-border bg-background/60 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Verdict</div>
              <div
                className={cx(
                  'mt-1 text-2xl font-semibold',
                  result.verdict === 'KEEP'
                    ? 'text-success'
                    : result.verdict === 'REVERT'
                    ? 'text-destructive'
                    : 'text-warning'
                )}
              >
                {result.verdict}
              </div>
              <div className="mt-1 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Objective: {result.objective}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <MetricMini label="Median delta" value={formatSigned(result.median_delta_s)} />
              <MetricMini label="Best delta" value={formatSigned(result.best_delta_s)} />
              <MetricMini label="Stdev delta" value={formatSigned(result.stdev_delta_s)} />
              <MetricMini
                label="Pace slope delta"
                value={formatSigned(result.pace_slope_delta_s_per_lap, 4, ' s/lap')}
              />
            </div>
          </div>
          <p className="mt-4 text-base leading-relaxed text-foreground">{result.reason}</p>
          {result.subjective_feedback && <p className="mt-3 rounded-xl border border-accent/25 p-3 text-sm text-foreground">체감 기록: {({ IMPROVED: '개선', SAME: '동일', WORSENED: '악화' })[result.subjective_feedback.change] || '미입력'} · 운전 편의 {result.subjective_feedback.rating || '--'}/5 · 속도 효과와 별도 평가</p>}
          {result.conditions_quality?.reasons?.length > 0 && <p className="mt-3 text-sm text-warning">비교 조건: {result.conditions_quality.reasons.join(' · ')}</p>}
          {result.learning_eligibility && <p className="mt-2 text-xs text-muted-foreground">학습 상태: {result.learning_eligibility.eligible ? '사용 가능' : '보류'} · {(result.learning_eligibility.reasons || []).join(' · ')}</p>}
          <div className="mt-4 rounded-xl border border-accent/25 bg-accent/10 p-4">
            <h4 className="text-sm font-semibold text-foreground">실제로 바뀐 셋업</h4>
            {(result.setup_changes || []).length > 0 ? <div className="mt-3 flex flex-col gap-2">{result.setup_changes.map((change) => <div key={change.setting} className="flex flex-wrap justify-between gap-2 text-sm"><span className="font-bold text-foreground">{change.label || change.setting}</span><span className="font-mono text-warning">{formatNumber(change.baseline_value, 2)} → {formatNumber(change.candidate_value, 2)} {change.unit}</span></div>)}</div> : <p className="mt-2 text-sm text-muted-foreground">{result.setup_data_available === false ? '셋업 원본이 없어 변경점을 확인할 수 없습니다.' : '수치로 비교 가능한 셋업 변경이 없습니다.'}</p>}
            {(result.setup_changes || []).length > 1 && <p className="mt-3 text-sm text-warning">여러 항목이 바뀌어 어느 변경의 효과인지 분리하기 어렵습니다. 다음에는 한 항목만 변경하세요.</p>}
            {Object.keys(result.conditions?.differences || {}).length > 0 && <details className="mt-3 text-sm text-muted-foreground"><summary data-help="quality" className="cursor-pointer font-bold">서로 다른 주행 조건</summary><div className="mt-2 flex flex-col gap-1">{Object.entries(result.conditions.differences).map(([key, values]) => <p key={key}>{({ track_temp_c: '노면 온도', air_temp_c: '기온', fuel_level_l: '연료량', weather_type: '날씨', track_wetness: '노면 젖음', session_type: '세션 유형' })[key] || key}: {String(values.baseline ?? '측정 불가')} → {String(values.candidate ?? '측정 불가')}</p>)}</div></details>}
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <ABAxisCard
              label="PACE"
              axis={result.performance_axes?.pace}
              detail={`중앙값 ${formatSigned(result.median_delta_s)} · 효과 기준 ${Number(result.ab_quality?.effect_threshold_s || 0).toFixed(3)} s`}
            />
            <ABAxisCard
              label="CONSISTENCY"
              axis={result.performance_axes?.consistency}
              detail={`자연 편차 ${Number(result.ab_quality?.natural_variability_s || 0).toFixed(3)} s · robust Δ ${formatSigned(result.robust_variability_delta_s)}`}
            />
            <ABAxisCard
              label="DRIVEABILITY"
              axis={result.performance_axes?.driveability}
              detail={`코너 지표 ${result.performance_axes?.driveability?.evidence_count ?? 0}개 · score ${result.performance_axes?.driveability?.score ?? '--'}`}
            />
            {result.performance_axes?.stability && <ABAxisCard label="완주와 안정성" axis={result.performance_axes.stability} detail={`성공률 A ${formatNumber(result.performance_axes.stability.baseline_success_rate == null ? null : Number(result.performance_axes.stability.baseline_success_rate) * 100, 0, '%')} → B ${formatNumber(result.performance_axes.stability.candidate_success_rate == null ? null : Number(result.performance_axes.stability.candidate_success_rate) * 100, 0, '%')} · 시도 A ${result.performance_axes.stability.baseline_attempted_laps ?? '--'} / B ${result.performance_axes.stability.candidate_attempted_laps ?? '--'}`} />}
          </div>
          <div className="mt-3 rounded-xl border border-primary/20 bg-primary/10 px-3 py-2 text-sm text-foreground">
            A/B 표본 신뢰도 <b className="text-primary">{result.ab_quality?.sample_confidence || '--'}</b> · 최소 {result.minimum_valid_laps_per_file ?? '--'}랩 / 권장 {result.recommended_valid_laps_per_file ?? '--'}랩 · 효과/자연편차 {result.ab_quality?.effect_vs_noise_ratio ?? '--'}×
          </div>
          {result.lap_distribution && (
            <details className="mt-4 rounded-xl border border-border p-3" open>
              <summary className="cursor-pointer text-sm font-semibold text-foreground">랩타임 분포 A/B</summary>
              <div className="mt-3"><LapDistribution distribution={result.lap_distribution} /></div>
            </details>
          )}
          {(result.gg?.baseline || result.gg?.candidate) && (
            <details className="mt-4 rounded-xl border border-border p-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">G-G 그림 A/B</summary>
              <div className="mt-3"><GGDiagram baseline={result.gg.baseline} candidate={result.gg.candidate} /></div>
            </details>
          )}
          {(result.damper_velocity?.baseline || result.damper_velocity?.candidate) && (
            <details className="mt-4 rounded-xl border border-border p-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">댐퍼 속도 분포 A/B</summary>
              <div className="mt-3"><DamperHistogram baseline={result.damper_velocity.baseline} candidate={result.damper_velocity.candidate} /></div>
            </details>
          )}
          <LearningEffectSummary result={result} memoryRecord={memoryRecord} />
          {memoryRecord && (
            <div className={cx(
              'mt-4 rounded-xl border p-3 text-sm',
              memoryRecord.saved
                ? 'border-success/25 bg-success/15 text-success'
                : 'border-warning/25 bg-warning/15 text-warning'
            )}>
              {memoryRecord.saved
                ? `A/B 판정과 실제 셋업 차이가 장기 기억 DB에 저장되었습니다.${memoryRecord.linked_plan_id ? ` AI 계획 ${memoryRecord.linked_plan_id}과 연결되었습니다.` : ''}`
                : `A/B 결과 DB 저장 실패: ${memoryRecord.reason || '원인 미상'}`}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function FileSelect({ label, value, onChange, files, disabled = false, hideLabel = false }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef(null);
  const selectedOptionRef = useRef(null);
  const selectedFile = files.find((file) => file.name === value);
  const selectedLapCount = Number(selectedFile?.lap_count || 0);

  useEffect(() => {
    const handlePointerDown = (event) => {
      if (!containerRef.current?.contains(event.target)) setOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const frame = window.requestAnimationFrame(() => {
      selectedOptionRef.current?.scrollIntoView({ block: 'nearest' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  const chooseFile = (filename) => {
    setOpen(false);
    onChange(filename);
  };

  return (
    <div ref={containerRef} className={cx('relative block min-w-0', open && 'z-[120]')}>
      <span hidden={hideLabel} className={hideLabel ? 'coach-file-label' : 'mb-2 block text-xs font-semibold uppercase tracking-wider text-muted-foreground'}>
        {label} <HelpHint topic={label} />
      </span>
      <Button variant="outline"
        type="button"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="listbox"
        data-help={label}
        aria-expanded={open}
        className={cx(
          'flex min-h-[50px] w-full items-center gap-3  border  px-4 py-3 text-base outline-none transition',
          open
            ? ' ring-2 ring-cyan-500/15'
            : ' '
        )}
      >
        {value && (
          <span
            title={`DB 분류: ${sessionModeLabel(assignedModeForFile(selectedFile))}`}
            className={cx(
              'shrink-0 rounded-lg border px-2.5 py-1 text-sm font-semibold',
              lapBadgeClass(selectedFile, false)
            )}
          >
            {selectedFile?.retryable ? '읽기 대기' : selectedFile?.metadata_pending ? '확인 중' : selectedFile?.status === 'COACHING_ONLY' ? '부분 코너' : `유효 ${selectedFile?.valid_laps ?? '--'} / ${selectedLapCount}랩`}
          </span>
        )}
        <span className="min-w-0 flex-1 truncate text-left text-foreground">
          {selectedFile?.car_name && selectedFile?.track_name ? <><span className="block truncate text-sm font-bold">{selectedFile.car_name} · {selectedFile.track_name}</span><span className="mt-1 block truncate text-xs text-muted-foreground">{value}</span></> : value || '파일 선택'}
        </span>
        <ChevronDown
          className={cx(
            'h-5 w-5 shrink-0 text-muted-foreground transition-transform',
            open && 'rotate-180 text-primary'
          )}
        />
      </Button>

      {open && (
        <div className="absolute left-0 top-full z-[130] mt-2 w-full overflow-hidden rounded-xl border border-border bg-background shadow-2xl shadow-black/60">
          <div className="border-b border-border p-2"><Input aria-label={`${label} 검색`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="파일·차량·트랙 검색" className="w-full  border   px-3 py-2 text-sm  outline-none " /></div>
          <div role="listbox" aria-label={label} className="max-h-[420px] overflow-y-auto overscroll-contain py-1">
            {!files.length && (
              <div className="px-4 py-4 text-sm text-muted-foreground">표시할 파일이 없습니다.</div>
            )}
            {files.filter((file) => [file.name, file.car_name, file.track_name, file.track_config].join(' ').toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).map((file) => {
              const isSelected = file.name === value;
              const lapCount = Number(file.lap_count || 0);
              return (
                <Button variant="surface"
                  key={file.name}
                  ref={isSelected ? selectedOptionRef : undefined}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  disabled={file.analyzable === false}
                  aria-disabled={file.analyzable === false}
                  onClick={() => chooseFile(file.name)}
                  className={cx(
                    'flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-55 sm:text-base',
                    isSelected
                      ? '  ring-1 ring-inset ring-cyan-500/55'
                      : ' '
                  )}
                >
                  <span
                    title={`DB 분류: ${sessionModeLabel(assignedModeForFile(file))}`}
                    className={cx(
                      'shrink-0 rounded-lg border px-2.5 py-1 text-sm font-semibold',
                      lapBadgeClass(file, isSelected)
                    )}
                  >
                    {file.retryable ? '읽기 대기' : file.metadata_pending ? '확인 중' : file.status === 'COACHING_ONLY' ? '부분 코너' : `유효 ${file.valid_laps ?? '--'} / ${lapCount}랩`}
                  </span>
                  <span className="min-w-0 flex-1 font-medium">
                    {file.car_name && file.track_name && <span className="mb-1 block truncate text-sm font-bold">{file.car_name} · {file.track_name}</span>}
                    <span className="block truncate">{file.name}</span>
                    {(file.eligibility_reason || file.reason) && <span className={cx('mt-1 block text-xs font-normal leading-relaxed', file.comparison_ready === false ? 'text-warning' : 'text-muted-foreground')}>{file.eligibility_reason || file.reason}</span>}
                  </span>
                  {isSelected && <CheckCircle2 className="h-5 w-5 shrink-0" />}
                </Button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
