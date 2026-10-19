import React, { useState, useEffect } from "react";
import { HelpHint } from "../components/HelpGuide";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import { AlertTriangle, FlaskConical } from "lucide-react";
import { SETTING_NAMES, cx, formatSetupOriginal, formatSetupTarget, hasSetupNumericValue, settingName } from './analysisShared';
import { EmptyState } from './WorkspaceUi';

export function SetupAdjustmentCard({ adjustment, experimental = false, onStart, canStart }) {
  const [actualOriginal, setActualOriginal] = useState(adjustment.original_value ?? '');
  const [actualTarget, setActualTarget] = useState(adjustment.value ?? '');
  useEffect(() => { setActualOriginal(adjustment.original_value ?? ''); setActualTarget(adjustment.value ?? ''); }, [adjustment.original_value, adjustment.value]);
  const confidence = Number(adjustment?.confidence);
  const priorityScore = Number(adjustment?.priority_score);
  const sourceLabels = {
    GARAGE61_STRAIGHT_CONSENSUS: 'Garage61 직선 컨센서스',
    GARAGE61_CORNER_CONSENSUS: 'Garage61 코너 컨센서스',
    GARAGE61_HIGH_SPEED_STABILITY: 'Garage61 고속 안정성',
    GARAGE61_REFERENCE_CONSENSUS: 'Garage61 컨센서스',
    CURRENT_SESSION_PARTIAL_PATTERN: '현재 세션 부분 반복',
    CURRENT_SESSION_SINGLE_OBJECTIVE_EVENT: '현재 세션 단일 객관 신호',
    CURRENT_SESSION_SINGLE_BOTTOMING: '현재 세션 단일 신호',
    CURRENT_SESSION_REPEATABILITY: '현재 세션 반복성',
    DRIVER_FEEDBACK_EXPLORATION: '선택한 증상 · 탐색 가설',
  };
  const categoryLabels = {
    AERO_DRAG: '공기저항 · 최고속',
    AERO_DRAG_SECONDARY: '공기저항 (보조)',
    TOP_SPEED_OPPORTUNITY: '최고속',
    EXIT_TRACTION: '탈출 트랙션',
    ROTATION_DEFICIT: '회전 부족',
    REAR_STABILITY: '리어 안정성',
    DIFF_ROTATION: '디퍼렌셜 회전',
    HIGH_SPEED_STABILITY: '고속 에어로 안정성',
    BRAKE_BALANCE: '브레이크 밸런스',
  };
  const sourceLabel = sourceLabels[adjustment?.source] || adjustment?.source || '현재 세션 근거';
  const originalText = formatSetupOriginal(adjustment);
  const targetText = formatSetupTarget(adjustment);

  return (
    <article className={cx(
      'rounded-2xl border bg-background/70 p-5',
      experimental ? 'border-warning/30' : 'border-primary/30'
    )}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-base font-bold text-foreground">{settingName(adjustment)}</div>
            {SETTING_NAMES[adjustment.setting] && adjustment.label && <span className="text-xs text-muted-foreground">Garage: {adjustment.label}</span>}
            {adjustment.category && (
              <span className="rounded-md border border-sky-500/25 bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-sky-300">
                {categoryLabels[adjustment.category] || adjustment.category}
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-baseline gap-2 font-mono">
            <span className="text-lg font-bold text-muted-foreground line-through">{originalText}</span>
            <span className="text-muted-foreground">→</span>
            <span className={cx('text-2xl font-semibold', experimental ? 'text-warning' : 'text-primary')}>
              {targetText}
            </span>
          </div>
          {adjustment?.numeric_target_estimated && (
            <div className="mt-1 text-xs font-bold text-warning/80">
              수치는 로컬 SQLite의 동일 차량 셋업 이력에서 click 간격을 추정했습니다. Garage에서 1 click인지 확인 후 적용하십시오.
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <span className={cx(
            'rounded-lg border px-2 py-1 text-xs font-semibold',
            experimental
              ? 'border-warning/30 bg-warning/10 text-warning'
              : 'border-primary/30 bg-primary/10 text-primary'
          )}>
            {adjustment.tier === 'EXPLORATORY' ? '탐색 후보 · 근거 약함' : experimental ? 'A/B 시험' : '근거 확인 · 시험'}
          </span>
          {Number.isFinite(confidence) && (
            <span className="font-mono text-xs font-bold text-muted-foreground">
              근거 점수 {Math.round(confidence * 100)}%
            </span>
          )}
          {Number.isFinite(priorityScore) && (
            <span className="font-mono text-xs font-bold text-muted-foreground">
              우선도 {Math.round(priorityScore * 100)}
            </span>
          )}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 text-xs font-semibold">
        <span className="rounded-lg border border-border bg-muted px-2 py-1 text-muted-foreground">
          {sourceLabel}
        </span>
        {adjustment.reference_support?.reference_count > 0 && (
          <span className="rounded-lg border border-accent/25 bg-accent/10 px-2 py-1 text-foreground">
            빠른 기준 {adjustment.reference_support.reference_count}개 · 드라이버 {adjustment.reference_support.unique_driver_count ?? '--'}명
          </span>
        )}
        {adjustment.status === 'TEST_ONLY_DIRECTIONAL' && (
          <span className="rounded-lg border border-border bg-muted px-2 py-1 text-muted-foreground">
            수치 미추정 · Garage 1-click
          </span>
        )}
        {adjustment.memory_support?.matching_outcomes > 0 && (
          <span className="rounded-lg border border-success/25 bg-success/10 px-2 py-1 text-success">
            개인 A/B 이력 {adjustment.memory_support.matching_outcomes}건 반영
          </span>
        )}
        {adjustment.personal_sensitivity_support?.decisive_count > 0 && (
          <span className="rounded-lg border border-fuchsia-500/25 bg-fuchsia-500/10 px-2 py-1 text-fuchsia-300">
            차량 민감도 {adjustment.personal_sensitivity_support.keep_count}/{adjustment.personal_sensitivity_support.decisive_count} KEEP
          </span>
        )}
      </div>
      <div className="mt-4 flex flex-col gap-2 text-sm leading-relaxed">
        {adjustment.expected_effect && (
          <p className="font-semibold text-sky-200">목표: {adjustment.expected_effect}</p>
        )}
        <p className="text-foreground">근거: {adjustment.evidence}</p>
        <p className="text-muted-foreground">트레이드오프: {adjustment.tradeoff}</p>
        <p className={experimental ? 'text-warning' : 'text-primary'}>검증: {adjustment.validation}</p>
        {adjustment.validator?.messages?.length > 0 && (
          <div className="rounded-lg border border-border bg-muted/70 p-2 text-xs text-muted-foreground">
            {adjustment.validator.messages.join(' · ')}
          </div>
        )}
      </div>
      {onStart && <div className="mt-4"><p className="mb-2 flex items-center gap-1 text-xs text-muted-foreground">실제 차고 값으로 시험 준비<HelpHint topic="actualValue" /></p><div className="grid gap-2 sm:grid-cols-2"><label className="text-xs text-muted-foreground">Garage 현재값<Input data-help="actualValue" aria-label={`${adjustment.label || adjustment.setting} 실제 현재값`} type="number" step="any" value={actualOriginal} onChange={(event) => setActualOriginal(event.target.value)} className="mt-1 w-full  border   p-2 text-sm" /></label><label className="text-xs text-muted-foreground">Garage 목표값<Input data-help="actualValue" aria-label={`${adjustment.label || adjustment.setting} 실제 목표값`} type="number" step="any" value={actualTarget} onChange={(event) => setActualTarget(event.target.value)} className="mt-1 w-full  border   p-2 text-sm" /></label></div><Button variant="default" data-help="startTrial" type="button" onClick={() => onStart({ ...adjustment, original_value: Number(actualOriginal), value: Number(actualTarget) })} disabled={!canStart || !hasSetupNumericValue(actualOriginal) || !hasSetupNumericValue(actualTarget) || Number(actualOriginal) === Number(actualTarget)} className="mt-3  coach-primary-action px-4 py-2 text-sm   disabled:opacity-40">이 변경 시험하기</Button></div>}
    </article>
  );
}

export function SetupRecommendation({ recommendation, onStart, canStart }) {
  if (!recommendation) return <EmptyState text="셋업 판정 결과가 없습니다." />;
  const confirmed = recommendation.confirmed_adjustments || recommendation.adjustments || [];
  const experimental = recommendation.experimental_candidates || [];
  const hasCandidate = confirmed.length > 0 || experimental.length > 0;
  const isHold = !hasCandidate || recommendation.decision === 'HOLD';
  const basisLabel = recommendation.decision_basis === 'SETUP_ASSIST_EXTERNAL_CONSENSUS'
    ? '외부 기준 컨센서스'
    : recommendation.decision_basis === 'CONFIRMED_REPEATABILITY'
    ? '현재 세션 반복성'
    : recommendation.decision_basis === 'EXPERIMENTAL_CURRENT_SESSION'
    ? '현재 세션 실험 후보'
    : recommendation.decision_basis === 'DRIVER_FEEDBACK_EXPLORATION'
    ? '선택한 증상으로 탐색'
    : recommendation.decision_basis || '근거 부족';
  const straight = recommendation.performance_diagnosis?.straight_line || {};
  const straightDiagnosisLabels = {
    AERO_DRAG_SUSPECT: '직선 후반 Drag / 최고속 병목',
    PERSISTENT_TOP_SPEED_DEFICIT: '반복적인 최고속 부족 · 셋업 보조 가치 있음',
    EXIT_SPEED_LIMITED: '직선 진입속도 병목',
    MIXED: '탈출 + 직선 성능 혼합 병목',
    NO_STRAIGHT_BOTTLENECK: '측정 완료 · 반복적인 직선 병목 없음',
    NO_REPEATED_STRAIGHT_DEFICIT: '측정 완료 · 반복적인 직선 병목 없음',
    INSUFFICIENT_STRAIGHT_DATA: '직선 분석 불충분 · 병목 판정 보류',
  };
  const straightInsufficient = ['INSUFFICIENT_STRAIGHT_DATA', 'NO_STRAIGHTS', 'MISSING_CHANNELS'].includes(straight.status)
    || straight.diagnosis === 'INSUFFICIENT_STRAIGHT_DATA';
  const straightNoBottleneck = ['NO_STRAIGHT_BOTTLENECK', 'NO_REPEATED_STRAIGHT_DEFICIT'].includes(straight.diagnosis);
  const straightHasResult = Boolean(straight.status || straight.diagnosis);
  const eligibleStraights = Number(straight.eligible_straights || 0);
  const requiredStraights = Number(straight.required_straights || 2);
  const formatStraightKph = (value, signed = false) => {
    if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '측정 불가';
    const numeric = Number(value);
    const prefix = signed && numeric > 0 ? '+' : '';
    return `${prefix}${numeric.toFixed(1)} km/h`;
  };

  return (
    <div className="flex flex-col gap-5">
      <div className={cx(
        'rounded-2xl border p-5',
        isHold ? 'border-warning/40 bg-warning/20' : 'border-primary/40 bg-primary/20'
      )}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            {isHold ? (
              <AlertTriangle className="h-6 w-6 text-warning" />
            ) : (
              <FlaskConical className="h-6 w-6 text-primary" />
            )}
            <div>
              <div className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Decision</div>
              <div className={cx('mt-1 text-xl font-semibold', isHold ? 'text-warning' : 'text-primary')}>
                {recommendation.decision_basis === 'DRIVER_FEEDBACK_EXPLORATION' ? '작은 변경부터 탐색' : hasCandidate ? 'A/B 시험' : '추천 후보 없음'}
              </div>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-background/65 px-4 py-3 text-right">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">판정 근거</div>
            <div className="mt-1 text-sm font-semibold text-foreground">{basisLabel}</div>
          </div>
        </div>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          드라이빙 제한으로 분류되어도 셋업으로 외부 기준 격차를 줄일 가능성이 있으면 Setup Assist A/B 후보를 별도로 제시합니다.
          실험 후보는 정답 판정이 아니라 한 단계 변경 후 검증할 항목입니다.
        </p>
      </div>

      {recommendation.setup_mode === 'EXPLORE' && <div className="rounded-xl border border-primary/25 bg-primary/15 p-4 text-sm leading-relaxed text-foreground">
        <p className="font-bold text-primary">탐색 추천 · 일정한 랩타임은 필요하지 않습니다</p>
        <p className="mt-2">{recommendation.exploration?.reason}</p>
        <p className="mt-2 text-muted-foreground">체감은 1~2랩으로 먼저 확인하고, 속도 효과는 자연 편차를 고려한 A/B로 따로 확인합니다.</p>
      </div>}

      {straightHasResult && (
        <div className={cx(
          'rounded-2xl border p-5',
          straightInsufficient
            ? 'border-warning/35 bg-warning/15'
            : straight.aero_drag_suspect || straight.persistent_top_speed_opportunity
            ? 'border-fuchsia-500/30 bg-fuchsia-950/15'
            : straightNoBottleneck
            ? 'border-success/25 bg-success/10'
            : 'border-border bg-background/55'
        )}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Performance setup diagnosis</div>
              <div className={cx(
                'mt-1 text-lg font-semibold',
                straightInsufficient
                  ? 'text-warning'
                  : straight.aero_drag_suspect || straight.persistent_top_speed_opportunity
                  ? 'text-fuchsia-300'
                  : straightNoBottleneck
                  ? 'text-success'
                  : 'text-foreground'
              )}>
                {straightDiagnosisLabels[straight.diagnosis] || straightDiagnosisLabels[straight.status] || straight.diagnosis || '직선 성능 분석'}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">분석 직선</div>
                <div className={cx('mt-1 font-mono font-semibold', straightInsufficient ? 'text-warning' : 'text-foreground')}>
                  {eligibleStraights}개
                </div>
                <div className="mt-0.5 text-[10px] text-muted-foreground">반복 판정 최소 {requiredStraights}개</div>
              </div>
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">Drag 병목</div>
                <div className="mt-1 font-mono font-semibold text-foreground">{straight.drag_support_count ?? 0}/{eligibleStraights}</div>
              </div>
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">후반 속도 부족</div>
                <div className={cx('mt-1 font-mono font-semibold', straightInsufficient ? 'text-warning' : 'text-destructive')}>
                  {formatStraightKph(straight.median_late_speed_deficit_kph)}
                </div>
              </div>
              <div className="rounded-lg border border-border bg-background/70 px-3 py-2">
                <div className="text-muted-foreground">Gap 증가</div>
                <div className={cx('mt-1 font-mono font-semibold', straightInsufficient ? 'text-warning' : 'text-destructive')}>
                  {formatStraightKph(straight.median_gap_growth_kph, true)}
                </div>
              </div>
            </div>
          </div>

          {straightInsufficient ? (
            <p className="mt-3 text-sm leading-relaxed text-warning/80">
              {straight.status === 'MISSING_CHANNELS'
                ? '필수 속도·스로틀·브레이크·조향 채널이 부족해 직선 병목을 판정할 수 없습니다.'
                : `유효 장거리 풀스로틀 직선이 ${eligibleStraights}개로 반복 판정 최소 ${requiredStraights}개에 못 미칩니다.`}
              {' '}이 상태는 "병목 없음"이 아니라 "측정 불충분"이며, 셋업의 Rear wing/drag 방향을 부재 근거로 판단하지 않습니다.
            </p>
          ) : straightNoBottleneck ? (
            <p className="mt-3 text-sm leading-relaxed text-success/75">
              유효 직선 {eligibleStraights}개를 실제 비교했지만 반복적인 Drag/최고속 병목 기준을 넘지 않았습니다.
              위 속도 수치는 문제 구간이 아니라 전체 유효 직선의 중앙값이므로, 이제 "-- km/h"와 병목 없음이 혼동되지 않습니다.
            </p>
          ) : (
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              직선 진입부터 느린 경우와 풀스로틀 직선 후반에서 속도차가 계속 커지는 경우를 분리합니다.
              후자만 반복될 때 Rear wing/drag 계열 셋업 후보의 우선도를 올립니다.
            </p>
          )}

          {straight.data_quality === 'LIMITED' && !straightInsufficient && (
            <div className="mt-3 rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs text-warning/75">
              직선 표본은 반복 판정 최소치는 충족하지만 강한 진단 권장치({straight.recommended_straights || 3}개)보다 적습니다. 셋업 후보 신뢰도는 보수적으로 해석하십시오.
            </div>
          )}
        </div>
      )}

      {confirmed.length > 0 && (
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-primary">확정 근거 A/B 후보</h3>
            <span className="text-xs text-muted-foreground">복수 랩의 객관적 반복 신호</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {confirmed.map((adjustment, index) => (
              <SetupAdjustmentCard key={`${adjustment.setting}-${adjustment.value}-${index}`} adjustment={adjustment} onStart={onStart} canStart={canStart} />
            ))}
          </div>
        </div>
      )}

      {experimental.length > 0 && (
        <div>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-warning">Setup Assist · 실험 A/B 후보</h3>
            <span className="text-xs text-muted-foreground">후보마다 측정 근거와 탐색 가설을 표시합니다.</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {experimental.map((adjustment, index) => (
              <SetupAdjustmentCard
                key={`${adjustment.setting}-${adjustment.value}-${adjustment.source}-${index}`}
                adjustment={adjustment}
                onStart={onStart}
                canStart={canStart}
                experimental
              />
            ))}
          </div>
        </div>
      )}

      {!hasCandidate && (
        <div className="rounded-2xl border border-border bg-background/60 p-5 text-base leading-relaxed text-foreground">
          {recommendation.setup_mode === 'EXPLORE' ? '위 피드백에서 구간·속도·증상을 선택하고 분석을 갱신하세요. 선택한 코너 측정과 현재 셋업 값으로 작은 변경 후보를 찾습니다.' : '현재 측정 근거로 만들 수 있는 셋업 후보가 없습니다. 탐색 추천으로 전환하면 선택한 증상으로 작은 변경을 시험할 수 있습니다.'}
        </div>
      )}

      <div>
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Validation plan
        </h3>
        <div className="flex flex-col gap-2">
          {(recommendation.validation_plan || []).map((item, index) => (
            <div key={`${item}-${index}`} className="flex items-start gap-3 rounded-xl border border-border bg-background/50 p-3 text-base text-foreground">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 font-mono text-sm font-semibold text-primary">
                {index + 1}
              </span>
              <span className="pt-0.5 leading-relaxed">{item}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
