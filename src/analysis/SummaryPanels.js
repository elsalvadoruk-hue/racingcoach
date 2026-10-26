import { Button } from "../components/ui/button";
import { TimerReset, Target, TrendingDown, CheckCircle2 } from "lucide-react";
import React, { useId } from "react";
import { FieldSet, FieldLegend } from "../components/ui/field";
import { HelpHint } from "../components/HelpGuide";
import { ToggleGroup, ToggleGroupItem } from "../components/ui/toggle-group";
import { WorkflowSteps } from "../components/WorkspaceShell";
import { GoalSelector } from "../components/TrialWorkflow";
import { Textarea } from "../components/ui/textarea";
import { FEEDBACK_OPTION_DESCRIPTIONS, ISSUE_LABELS, SPEED_LABELS, cx, formatLapSeconds, formatNumber, formatSetupNumber, formatSetupOriginal, formatSigned, hasSetupNumericValue, localizeSetupStep } from './analysisShared';
import { EmptyState, MetricCard } from './WorkspaceUi';

export function RunSummary({ analysis, onNavigate, referenceMode = 'LOCAL' }) {
  if (analysis.data_quality?.analysis_scope === 'CLEAN_SEGMENTS_ONLY') {
    const corners = analysis.coaching?.clean_segment_corners || [];
    return <div className="flex flex-col gap-4"><p className="text-sm text-warning">유효 완주랩 0개 · 깨끗한 코너 구간만 관측합니다. 이 구간을 랩타임이나 A/B 속도 효과로 환산하지 않습니다.</p><div className="grid gap-3 sm:grid-cols-2">{corners.slice(0, 2).map((corner, index) => <Button variant="surface" type="button" key={corner.id || index} onClick={() => onNavigate('corner-inputs', corner.id || corner.corner_id)} className=" border   p-4 text-left"><h3 className="font-bold text-foreground">{corner.label || corner.id} · 랩 {corner.lap_number ?? '--'}</h3><p className="mt-2 text-sm text-muted-foreground">최저속도 {formatNumber(corner.target?.min_speed_kph, 1, ' km/h')} · {corner.speed_class || '속도 구간 미분류'}</p><p className="mt-2 text-sm text-primary">코너 입력 확인하기</p></Button>)}</div><p className="text-sm text-muted-foreground">셋업 실험에서 구간·속도·증상을 선택하면 작은 변경을 탐색할 수 있습니다.</p></div>;
  }
  const external = analysis.external_reference;
  const aggregate = external?.aggregate || {};
  const comparison = referenceMode === 'LOCAL' ? analysis.comparison || {} : {};
  const externalReady = referenceMode !== 'LOCAL' && external?.status === 'READY';
  const gap = externalReady ? aggregate.gap_to_reference_s ?? aggregate.gap_to_reference_median_s : comparison.total_delta_s;
  const candidates = externalReady ? external.top_losses || [] : referenceMode === 'LOCAL' ? analysis.performance?.corners || [] : [];
  const losses = candidates.filter((corner) => corner.delta_s !== null && corner.delta_s !== undefined && Number(corner.delta_s) > 0)
    .sort((left, right) => Number(right.delta_s) - Number(left.delta_s)).slice(0, 2);
  return <div className="flex flex-col gap-5">
    <div className="coach-overview-metrics grid gap-3 sm:grid-cols-3">
      <MetricCard label="내 대상 랩" value={externalReady ? aggregate.target_time : comparison.target_time || formatLapSeconds(analysis.laps?.find((lap) => String(lap.lap_number) === String(analysis.lap_selection?.target_lap))?.lap_time_s)} note={`랩 ${comparison.target_lap ?? analysis.lap_selection?.target_lap ?? '--'}`} icon={TimerReset} />
      <MetricCard label={externalReady ? 'Garage61 대표 기준' : '내 기준 랩'} value={externalReady ? aggregate.reference_time ?? aggregate.reference_median_time : comparison.reference_time || '비교 기준 없음'} note={externalReady ? aggregate.reference_label || '선택한 Garage61 기준' : comparison.reference_lap != null ? `랩 ${comparison.reference_lap}` : '기준랩을 추가하면 코너 손실을 비교합니다.'} icon={Target} tone="orange" />
      <MetricCard label="기준 대비 차이" value={gap == null ? '측정 불가' : formatSigned(gap)} note={gap == null ? '비교 가능한 기준이 필요합니다.' : Number(gap) > 0 ? '내 대상 랩이 느립니다.' : Number(gap) < 0 ? '내 대상 랩이 빠릅니다.' : '동일한 랩타임입니다.'} icon={TrendingDown} tone={gap == null ? 'slate' : Number(gap) > 0 ? 'rose' : 'emerald'} />
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="font-semibold text-foreground">먼저 확인할 코너</h3><p className="mt-1 text-sm text-muted-foreground">측정된 시간 손실이 큰 두 구간부터 입력과 라인을 확인하세요.</p></div>
      <Button variant="outline" type="button" onClick={() => onNavigate('corner-inputs')} className=" border  px-4 py-2 text-sm  ">코너 비교 열기</Button>
    </div>
    {losses.length ? <div className="grid gap-3 md:grid-cols-2">{losses.map((corner, index) => <Button variant="surface" key={corner.id || corner.corner_id || index} type="button" onClick={() => onNavigate('corner-inputs', corner.id || corner.corner_id)} className=" border   p-4 text-left ">
      <div className="flex items-center justify-between gap-3"><span className="font-semibold text-foreground">{corner.label || corner.corner_id || corner.id || `코너 ${index + 1}`}</span><span className="font-mono font-semibold text-destructive">{formatSigned(corner.delta_s)}</span></div>
      <div className="mt-2 text-sm text-muted-foreground">집중 단계: {ISSUE_LABELS[corner.issue_type] || corner.issue_type || '코너 전체'} · {SPEED_LABELS[corner.speed_class] || corner.speed_class || '속도 구간 미분류'}</div>
      <p className="mt-2 text-sm leading-relaxed text-foreground">{corner.coaching_note || corner.coaching || '브레이크 해제, 최저속도, 가속 시작점을 기준 주행과 비교하세요.'}</p>
    </Button>)}</div> : <EmptyState text="측정 가능한 코너 손실이 없습니다. 유효랩과 기준 데이터 상태를 확인하세요." />}
    {externalReady && <p className="text-xs leading-relaxed text-muted-foreground">Garage61 기준 조건 유사도 {aggregate.condition_similarity_median == null ? '측정 불가' : `${Math.round(Number(aggregate.condition_similarity_median) * 100)}%`} · 서로 다른 주행 조건과 자연 편차를 고려해 다음 런에서 검증하세요.</p>}
  </div>;
}

export function FeedbackSelector({ label, value, onChange, options, allowClear = true, number, tone = 'violet' }) {
  const id = useId();
  return <FieldSet className="coach-input-group" data-tone={tone}>
    <FieldLegend data-help={label} className="coach-feedback-legend">{number && <span className="coach-group-number" aria-hidden="true">{number}</span>}<span className="coach-group-label">{label}</span><HelpHint topic={label} /></FieldLegend>
    <ToggleGroup type="multiple" variant="outline" value={value ? [value] : []} onValueChange={(next) => onChange(next.find(key => key !== value) || (allowClear ? next[0] || '' : value))} aria-label={label}
      className={cx('coach-feedback-options', options.length === 3 && 'three-options', options.length === 4 && 'four-options')}>
      {options.map(([key, text]) => <ToggleGroupItem key={key} value={key} aria-label={text} aria-describedby={`${id}-${key}`} data-help={key} className="coach-option">
        {value === key && <CheckCircle2 aria-hidden="true" className="coach-option-check" />}<span className="coach-option-label">{text}</span><span id={`${id}-${key}`} className="coach-option-description">{FEEDBACK_OPTION_DESCRIPTIONS[key]}</span>
      </ToggleGroupItem>)}
    </ToggleGroup>
  </FieldSet>;
}

export function StructuredFeedback({ phase, speed, symptom, setupMode, onSetupMode, onPhase, onSpeed, onSymptom, value, onChange, result, coach, onAnalyze, onNavigate, loading, goal, onGoal, trialActive }) {
  const setup = coach?.top_setup_candidate;
  const next = coach?.next_run || {};
  const complete = Boolean(phase && speed && symptom);
  const selectedMatchesResult = complete && result && (result.phase || '') === phase && (result.speed || '') === speed && (result.symptom || '') === symptom && (result.setup_mode || 'EXPLORE') === setupMode;
  const target = setup ? hasSetupNumericValue(setup.value) ? `${formatSetupNumber(setup.value)} ${setup.unit || ''}`.trim() : localizeSetupStep(setup.step_text, setup.direction) : null;
  const missing = [!phase && '구간', !speed && '속도', !symptom && '증상'].filter(Boolean);
  return <div className="flex flex-col gap-5">
    <WorkflowSteps steps={['증상 선택', '추천 확인', '한 항목 변경', '결과 기록']} current={trialActive ? 2 : selectedMatchesResult ? 1 : 0} />
    <div className="coach-feedback-settings">
      <div className="coach-input-group coach-goal-group" data-tone="mint"><GoalSelector value={goal} onChange={onGoal} disabled={trialActive} /><p className="coach-goal-note">추천과 A/B 검증에서 가장 먼저 볼 기준입니다.</p></div>
      <FeedbackSelector label="셋업 추천 방식" value={setupMode} onChange={onSetupMode} allowClear={false} options={[["EXPLORE", "탐색 추천"], ["EVIDENCE", "근거 우선 추천"]]} />
    </div>
    <p className="coach-feedback-note">{setupMode === 'EXPLORE' ? '유효랩 1개 또는 깨끗한 코너로 시작해, 작은 변경의 체감을 확인합니다.' : '반복 측정과 외부 기준에서 확인한 근거로 추천합니다.'}</p>
    <div className="coach-feedback-grid">
      <FeedbackSelector number="01" tone="cyan" label="어느 구간인가요?" value={phase} onChange={onPhase} options={[["ENTRY", "진입"], ["MID_CORNER", "코너 중간"], ["EXIT", "탈출"]]} />
      <FeedbackSelector number="02" tone="amber" label="코너 속도" value={speed} onChange={onSpeed} options={[["LOW", "저속"], ["MEDIUM", "중속"], ["HIGH", "고속"]]} />
      <FeedbackSelector number="03" tone="rose" label="느낀 증상" value={symptom} onChange={onSymptom} options={[["UNDERSTEER", "언더스티어"], ["OVERSTEER", "오버스티어"], ["BRAKING", "제동 불안"], ["KERB", "연석 불안"]]} />
    </div>
    <div className="coach-feedback-detail"><label htmlFor="driver-feedback" className="mb-2 block text-xs text-muted-foreground">코너 위치와 느낌을 더 적어주세요 · 선택 사항</label><Textarea id="driver-feedback" data-help="feedback" aria-label="드라이버 추가 피드백" value={value} onChange={(event) => onChange(event.target.value)} placeholder="예: T1 진입에서 앞이 밀려 브레이크를 놓기 어렵습니다." className="min-h-[88px] w-full  border   p-3 text-sm " /></div>
    <div className="coach-feedback-submit"><Button variant="default" type="button" data-help="feedbackApply" onClick={onAnalyze} disabled={loading || !complete} className="coach-primary-action px-5 py-3 text-sm">{loading ? '분석 중' : '피드백으로 분석 갱신'}</Button><p aria-live="polite">{missing.length ? `추천을 보려면 ${missing.join(' · ')}를 선택하세요.` : selectedMatchesResult ? '선택한 증상이 분석에 반영되었습니다.' : '세 항목 선택 완료. 분석을 갱신해 추천을 확인하세요.'}</p></div>
    {!selectedMatchesResult && (phase || speed || symptom || (result?.setup_mode && result.setup_mode !== setupMode)) && <p className="text-sm text-warning">현재 추천은 이전 입력 기준입니다. 분석을 갱신하면 선택한 증상과 추천 방식을 적용합니다.</p>}
    {selectedMatchesResult && result.status !== 'NOT_SELECTED' && <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-2xl border border-primary/25 bg-primary/10 p-4"><h3 className="font-semibold text-primary">측정된 근거</h3><p className="mt-1 text-xs text-muted-foreground">{result.source === 'GARAGE61' ? 'Garage61 기준 비교' : '내 주행 데이터'}</p>{result.observations?.length ? result.observations.map((item, index) => <div key={item.corner_id || index} className="mt-3 border-t border-border pt-3 text-sm"><div className="font-bold text-foreground">{item.label || item.corner || item.corner_id} · {formatSigned(item.delta_s)}</div>{(item.evidence || []).map((evidence, i) => <p key={i} className="mt-1 leading-relaxed text-foreground">{evidence}</p>)}</div>) : <p className="mt-3 text-sm text-muted-foreground">선택한 조건에서 비교 가능한 근거가 부족합니다. 증상만으로 셋업 원인을 확정하지 않습니다.</p>}</div>
      <div className="rounded-2xl border border-warning/25 bg-warning/10 p-4"><h3 className="font-semibold text-warning">검증할 가설</h3><p className="mt-3 text-sm leading-relaxed text-foreground">{result.hypothesis || '관측과 드라이버 느낌을 확인할 추가 주행이 필요합니다.'}</p><p className="mt-3 text-sm leading-relaxed text-foreground">다음 런: {result.next_run || next.primary_instruction || '동일 조건에서 증상을 다시 확인하세요.'}</p></div>
    </div>}
    <div className="rounded-2xl border border-accent/25 bg-accent/10 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold text-foreground">{next.mode === 'SETUP_EXPLORATION' ? '한 가지 변경으로 체감 확인' : '한 가지 변경으로 A/B 검증'}</h3>
        <Button variant="outline" type="button" onClick={() => onNavigate('ab-manager')} className=" border  px-3 py-2 text-sm  ">A/B 비교 열기</Button>
      </div>
      {setup ? <><p className="mt-3 font-bold text-foreground">{setup.label}</p><p className="mt-1 font-mono text-warning">{formatSetupOriginal(setup)} → {target}</p><p className="mt-2 text-sm text-muted-foreground">이 변경만 적용하고 나머지 셋업·연료·주행 조건은 고정하세요.</p></> : <p className="mt-3 text-sm text-muted-foreground">검증 가능한 수치 셋업 후보가 아직 없습니다. 증상과 코너 입력부터 확인하세요.</p>}
      {setup && <Button variant="default" type="button" onClick={() => onNavigate('setup-decision')} className="coach-primary-action mt-3 px-4 py-3 text-sm">추천 후보 자세히 보기</Button>}
      <p className="mt-3 text-sm text-foreground">{next.required_clean_corners > 0 ? `관측할 깨끗한 코너 최소 ${next.required_clean_corners}개 · 체감은 1~2랩으로 확인` : `${next.mode === 'SETUP_EXPLORATION' ? '체감 관찰' : '최소'} 유효랩 ${next.required_valid_laps ?? '--'} / 권장 ${next.recommended_valid_laps ?? next.required_valid_laps ?? '--'}`} 
        {next.mode === 'SETUP_EXPLORATION' && <span className="mt-1 block text-muted-foreground">A/B 효과 확인은 각 셋업 유효랩 {next.effect_confirmation_minimum_laps}개부터입니다.</span>}
      </p>
      {next.success_criteria?.length > 0 && <div className="mt-2 flex flex-col gap-1 text-sm text-muted-foreground">{next.success_criteria.map((rule) => <p key={rule}>• {rule}</p>)}</div>}
    </div>

  </div>;
}
