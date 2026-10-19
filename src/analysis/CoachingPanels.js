import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "../components/ui/table";
import React from "react";
import { EmptyState, MetricMini } from './WorkspaceUi';
import { SPEED_LABELS, cx, formatSetupNumber, formatSetupOriginal, formatSigned, hasSetupNumericValue, localizeSetupStep, scrollToCoachSection, settingName, workflowTone } from './analysisShared';

export function ClosedLoopCoachPanel({ coach, currentFile, memory }) {
  if (!coach) return <EmptyState text="Closed-loop Coach 계산을 기다리고 있습니다." />;
  const workflow = coach.workflow || [];
  const focus = coach.top_focus;
  const nextRun = coach.next_run || {};
  const personalization = coach.personalization || {};
  const setup = coach.top_setup_candidate;
  const recovery = focus?.estimated_recovery_s;
  const changeTarget = setup
    ? (hasSetupNumericValue(setup.value)
      ? `${formatSetupNumber(setup.value)} ${setup.unit || ''}`.trim()
      : localizeSetupStep(setup.step_text, setup.direction))
    : null;

  return (
    <div className="flex flex-col gap-5">
      <details className="rounded-xl border border-border p-3">
        <summary data-help="quality" className="cursor-pointer text-sm font-bold text-muted-foreground">코칭 근거와 저장 상태</summary>
      <div className="grid gap-2 md:grid-cols-5">
        {workflow.map((item, index) => (
          <div key={item.key} className={cx('rounded-2xl border p-3', workflowTone(item.status))}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold uppercase tracking-[0.16em] opacity-70">STEP {item.step}</span>
              <span className="rounded-md border border-current/20 px-1.5 py-0.5 text-[11px] font-semibold">{item.status}</span>
            </div>
            <div className="mt-2 font-semibold text-foreground">{item.label}</div>
            <div className="mt-1 text-sm leading-snug opacity-80">{item.detail}</div>
            {index < workflow.length - 1 && <div className="mt-2 hidden text-center text-muted-foreground md:block">→</div>}
          </div>
        ))}
      </div>

      <div className={cx('rounded-xl border px-4 py-3 text-sm leading-relaxed', memory?.saved ? 'border-success/25 bg-success/10 text-success' : 'border-warning/25 bg-warning/10 text-warning')}>
        {memory?.saved
          ? '현재 IBT는 장기 기억 DB에 포함되어 있습니다. 이후 분석과 A/B 결과가 이 차량의 개인화 모델에 누적됩니다.'
          : '현재 IBT는 아직 개인화 DB에 포함되지 않았습니다. 상단에서 세션 유형을 확정하면 이 주행의 랩/코너 특성이 Driver Fingerprint 학습에 사용됩니다.'}
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-border bg-background/45 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">현재 자연 편차</div>
          <div className="mt-1 font-mono text-lg font-semibold text-foreground">±{Number(coach.natural_variability?.natural_variability_s || 0).toFixed(3)} s</div>
          <div className="mt-1 text-xs text-muted-foreground">표본 {coach.natural_variability?.valid_laps || 0}랩 · {coach.natural_variability?.confidence_label || 'NO_DATA'} 신뢰도</div>
        </div>
        <div className="rounded-xl border border-primary/20 bg-primary/10 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-primary">재현성 사용 방식</div>
          <div className="mt-1 text-sm font-bold text-foreground">분석 통과조건 아님</div>
          <div className="mt-1 text-xs text-muted-foreground">효과가 자연 편차보다 큰지 판단하는 보정값으로만 사용합니다.</div>
        </div>
        <div className="rounded-xl border border-accent/20 bg-accent/10 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-foreground">셋업 Assist</div>
          <div className="mt-1 text-sm font-bold text-foreground">낮은 재현성에서도 계속 사용</div>
          <div className="mt-1 text-xs text-muted-foreground">차를 더 쉽게 반복할 수 있게 만드는 셋업도 A/B 후보로 평가합니다.</div>
        </div>
      </div>

      </details>
      <div className="grid gap-4 xl:grid-cols-12">
        <div className="rounded-2xl border border-primary/30 bg-primary/15 p-5 xl:col-span-7">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs font-medium text-muted-foreground">다음 주행 목표</div>
              <h3 className="mt-2 text-xl font-semibold text-foreground">{nextRun.mode_label || '다음 런 준비'}</h3>
              <p className="mt-2 text-lg font-bold text-primary">{nextRun.title || '--'}</p>
            </div>
            <div className="rounded-xl border border-border bg-background/60 px-4 py-3 text-right">
              <div className="text-xs font-bold uppercase text-muted-foreground">최소 / 권장 유효랩</div>
              <div className="mt-1 font-mono text-2xl font-semibold text-foreground">
                {nextRun.required_valid_laps ?? '--'} / {nextRun.recommended_valid_laps ?? nextRun.required_valid_laps ?? '--'}
              </div>
            </div>
          </div>
          <p className="mt-4 text-base font-semibold leading-relaxed text-foreground">{nextRun.primary_instruction}</p>

          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="rounded-xl border border-border bg-background/50 p-4">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">이번 런에서 고정</div>
              <div className="mt-2 flex flex-col gap-2 text-sm text-foreground">
                {(nextRun.lock || []).map((item) => <div key={item}>• {item}</div>)}
              </div>
            </div>
            <div className="rounded-xl border border-border bg-background/50 p-4">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">성공 조건</div>
              <div className="mt-2 flex flex-col gap-2 text-sm text-foreground">
                {(nextRun.success_criteria || []).map((item) => <div key={item}>• {item}</div>)}
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-accent/20 bg-accent/15 p-3 text-sm leading-relaxed text-foreground">
            완료 후: {nextRun.after_run}
          </div>
          {currentFile && nextRun.mode === 'SETUP_AB' && (
            <div className="mt-3 text-sm text-muted-foreground">A/B의 A(Baseline)는 현재 파일 <b className="text-foreground">{currentFile}</b>을 사용하면 됩니다.</div>
          )}
        </div>

        <div className="flex flex-col gap-3 xl:col-span-5">
          <div className="rounded-2xl border border-border bg-background/55 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">가장 먼저 고칠 것</div>
            {focus ? (
              <>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <div className="text-lg font-semibold text-foreground">{focus.root_cause_label}</div>
                  <div className="font-mono font-semibold text-primary">{Math.round(Number(focus.priority_score || 0) * 100)}</div>
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  {(focus.affected_corners || [focus.corner]).filter(Boolean).join(' · ')}
                  {recovery?.length === 2 && ` · 예상 회수 ${Number(recovery[0]).toFixed(2)}~${Number(recovery[1]).toFixed(2)} s`}
                </div>
                <p className="mt-3 text-sm leading-relaxed text-foreground">{focus.action}</p>
                {focus.root_cause === 'DRIVER_VARIANCE' && (
                  <div className="mt-3 rounded-lg border border-primary/20 bg-primary/10 px-3 py-2 text-xs leading-relaxed text-primary">
                    이 항목은 “실력을 먼저 올려야 한다”는 판정이 아닙니다. 현재 자연 편차를 추정하는 낮은 우선도의 관찰 항목이며 셋업 Assist를 차단하지 않습니다.
                  </div>
                )}
              </>
            ) : <div className="mt-2 text-sm text-muted-foreground">명확한 단일 드라이빙 병목이 없습니다.</div>}
          </div>

          <div className="rounded-2xl border border-border bg-background/55 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">셋업 A/B 후보</div>
            {setup ? (
              <>
                <div className="mt-2 font-semibold text-foreground">{setup.label}</div>
                <div className="mt-1 font-mono text-warning">
                  {formatSetupOriginal(setup)} → {changeTarget}
                </div>
                <div className="mt-2 text-sm text-muted-foreground">근거 {Math.round(Number(setup.confidence || 0) * 100)}% · 우선도 {Math.round(Number(setup.priority_score || 0) * 100)}%</div>
              </>
            ) : <div className="mt-2 text-sm text-muted-foreground">현재 검증 가능한 셋업 후보가 없습니다.</div>}
          </div>

          <div className="rounded-2xl border border-success/20 bg-success/10 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-success/80">개인화 학습</div>
            <div className="mt-2 flex items-end justify-between gap-3">
              <div className="text-lg font-semibold text-foreground">{personalization.learning_stage || 'COLD_START'}</div>
              <div className="font-mono text-xl font-semibold text-success">{Math.round(Number(personalization.learning_progress || 0) * 100)}%</div>
            </div>
            <div className="mt-2 text-sm text-muted-foreground">A/B 근거 {personalization.decisive_sensitivity_count ?? 0}건 · Fingerprint {personalization.fingerprint_confidence_label || 'LOW'}</div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('priorities')} className=" border   px-4 py-2 text-sm   ">원인 상세</Button>
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('setup-decision')} className=" border   px-4 py-2 text-sm   ">셋업 후보</Button>
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('ab-manager')} className=" border   px-4 py-2 text-sm   ">A/B 비교</Button>
        <Button variant="outline" type="button" onClick={() => scrollToCoachSection('personalization')} className=" border   px-4 py-2 text-sm   ">개인화 결과</Button>
      </div>
    </div>
  );
}

export function PersonalizationPanel({ memory }) {
  const fingerprint = memory?.driver_fingerprint || {};
  const sensitivity = memory?.setup_sensitivity || {};
  const settings = sensitivity.settings || [];
  const weaknesses = fingerprint.weaknesses || [];
  const strengths = fingerprint.strengths || [];
  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm leading-relaxed text-muted-foreground">저장한 주행에서 반복되는 특성을 찾습니다. 셋업은 한 항목씩 변경하고 A/B 결과를 저장하면 변경 효과도 함께 학습합니다.</p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricMini label="개인 세션" value={`${fingerprint.session_count || 0}개`} />
        <MetricMini label="학습 트랙" value={`${fingerprint.track_count || 0}개`} />
        <MetricMini label="결정형 A/B" value={`${sensitivity.decisive_event_count || 0}건`} />
        <MetricMini label="주행 특성 신뢰도" value={`${Math.round(Number(fingerprint.confidence || 0) * 100)}%`} />
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="coach-learning-list">
          <h3 className="font-semibold text-foreground">반복되는 개선 영역</h3>
          <div className="mt-3 flex flex-col gap-2">
            {weaknesses.length ? weaknesses.slice(0, 5).map((item) => (
              <div key={`${item.issue_type}-${item.speed_class}`} className="rounded-xl border border-border bg-muted/50 p-3">
                <div className="flex justify-between gap-3 text-sm">
                  <span className="font-bold text-foreground">{SPEED_LABELS[item.speed_class] || item.speed_class} · {item.issue_label}</span>
                  <span className="font-mono text-destructive">{Math.round(Number(item.session_support_ratio || 0) * 100)}%</span>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{item.session_support} 세션 · 평균 손실 {formatSigned(item.mean_loss_s)}</div>
              </div>
            )) : <div className="text-sm text-muted-foreground">같은 차량의 반복 데이터가 더 필요합니다.</div>}
          </div>
        </div>

        <div className="coach-learning-list">
          <h3 className="font-semibold text-foreground">상대적 강점 / 재현성</h3>
          <div className="mt-3 flex flex-col gap-2">
            {strengths.length ? strengths.slice(0, 5).map((item, index) => (
              <div key={`${item.type}-${item.speed_class || index}`} className="rounded-xl border border-border bg-muted/50 p-3">
                <div className="flex justify-between gap-3 text-sm">
                  <span className="font-bold text-foreground">{item.label}</span>
                  <span className="font-mono text-success">{Math.round(Number(item.score || 0) * 100)}</span>
                </div>
                {item.median_stdev_s !== undefined && item.median_stdev_s !== null && <div className="mt-1 text-xs text-muted-foreground">중앙 랩 편차 σ {Number(item.median_stdev_s).toFixed(3)} s</div>}
              </div>
            )) : <div className="text-sm text-muted-foreground">강점을 판정할 표본이 아직 부족합니다.</div>}
          </div>
        </div>
      </div>

      <div className="coach-learning-list">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold text-foreground">셋업 민감도 학습</h3>
          <Badge variant="secondary">{{ COLD_START: '표본 수집 중', LEARNING: '학습 중', MATURE: '학습 누적' }[sensitivity.learning_stage] || sensitivity.learning_stage || '표본 수집 중'}</Badge>
        </div>
        {settings.length ? (
          <div className="mt-3 overflow-x-auto">
            <Table className="w-full min-w-[760px] text-left text-sm">
              <TableHeader className="text-xs uppercase tracking-wider text-muted-foreground">
                <TableRow><TableHead className="py-2">항목</TableHead><TableHead>방향</TableHead><TableHead>결정 표본</TableHead><TableHead>KEEP</TableHead><TableHead>REVERT</TableHead><TableHead>평균 효과</TableHead><TableHead>단계</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {settings.slice(0, 8).map((item) => (
                  <TableRow key={`${item.setting}-${item.direction}`} className="border-t border-border">
                    <TableCell className="py-3 font-bold text-foreground">{settingName(item)}</TableCell>
                    <TableCell className="text-foreground">{item.direction_label}</TableCell>
                    <TableCell className="font-mono text-foreground">{item.decisive_count}</TableCell>
                    <TableCell className="font-mono text-success">{item.keep_count}</TableCell>
                    <TableCell className="font-mono text-destructive">{item.revert_count}</TableCell>
                    <TableCell className={cx('font-mono', Number(item.weighted_median_delta_s || 0) < 0 ? 'text-success' : 'text-foreground')}>{item.weighted_median_delta_s === null || item.weighted_median_delta_s === undefined ? '--' : formatSigned(item.weighted_median_delta_s)}</TableCell>
                    <TableCell className="text-muted-foreground">{item.tier}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : <p className="mt-3 text-sm leading-relaxed text-muted-foreground">아직 저장된 단일 변경 A/B 결과가 없습니다. 셋업 후보 하나만 바꾸고 A/B 비교를 저장하면 자동으로 학습됩니다.</p>}
      </div>
    </div>
  );
}
