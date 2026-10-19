import { Alert, AlertTitle, AlertDescription } from "../components/ui/alert";
import { Sparkles, AlertTriangle, Gauge } from "lucide-react";
import { Badge } from "../components/ui/badge";
import React, { useState, useEffect } from "react";
import { Button } from "../components/ui/button";
import { EmptyState, ErrorMessage, RuleList } from './WorkspaceUi';
import { requestJson } from './analysisClient';
import { cx, formatSetupOriginal, formatSetupTarget } from './analysisShared';

export function BriefingViewer({ briefing }) {
  if (!briefing) return <EmptyState text="브리핑이 없습니다." />;
  return (
    <div className="flex flex-col gap-5">
      <div className="border border-border bg-surface p-5 text-base leading-relaxed text-foreground">
        {briefing.summary}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {(briefing.sections || []).map((section) => (
          <article key={section.title} className="rounded-2xl border border-border bg-background/55 p-5">
            <h3 className="font-semibold text-foreground">{section.title}</h3>
            <div className="mt-4 flex flex-col gap-3 text-base leading-relaxed text-foreground">
              {(section.items || []).map((item) => (
                <div key={item} className="flex items-start gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>
      <div className="text-right text-xs font-bold uppercase tracking-wider text-muted-foreground">
        Source: {briefing.source === 'GCP_VERTEX_AI_BRIEFING'
          ? 'GCP Vertex AI · 토큰 사용'
          : '로컬 결정론적 엔진 · 토큰 미사용'}
      </div>
    </div>
  );
}

export function AIUsageBanner({ usage, aiEnabled }) {
  const tokens = usage?.tokens || {};
  if (usage?.used) {
    return <Alert><Sparkles aria-hidden="true" /><AlertTitle>이번 결과에 GCP AI가 사용되었습니다.</AlertTitle><AlertDescription>
      <p>{usage.purpose} · {usage.model} · 입력/출력 토큰 과금 대상</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {tokens.input_tokens !== undefined && <Badge variant="secondary">입력 {tokens.input_tokens.toLocaleString()}</Badge>}
        {tokens.output_tokens !== undefined && <Badge variant="secondary">출력 {tokens.output_tokens.toLocaleString()}</Badge>}
        <Badge variant="outline">{tokens.total_tokens !== undefined ? `총 ${tokens.total_tokens.toLocaleString()} tokens` : '토큰 수 메타데이터 미제공'}</Badge>
      </div>
    </AlertDescription></Alert>;
  }
  if (usage?.requested && !usage?.used) {
    return <Alert variant="destructive"><AlertTriangle aria-hidden="true" /><AlertTitle>GCP AI 요청이 완료되지 않아 로컬 결과를 유지했습니다.</AlertTitle><AlertDescription>{usage.error || usage.status}</AlertDescription></Alert>;
  }
  return <Alert><Gauge aria-hidden="true" /><AlertTitle>현재 화면은 로컬 분석 결과입니다.</AlertTitle><AlertDescription>
    <p>IBT 파싱·랩 델타·코너·셋업 판정은 GCP 토큰을 사용하지 않습니다.</p>
    <Badge variant="secondary" className="mt-3">{aiEnabled ? 'GCP AI 사용 가능' : 'GCP AI 미연결'}</Badge>
  </AlertDescription></Alert>;
}

export function AIExperimentPlanner({
  selectedFile,
  sessionMode,
  pitRule,
  targetLap,
  referenceLap,
  userFeedback,
  feedbackPhase,
  feedbackSpeed,
  feedbackSymptom,
  setupMode,
  aiEnabled,
  externalReferenceFiles = [],
  onStored,
}) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setResult(null);
    setError('');
  }, [selectedFile, sessionMode, targetLap, referenceLap, externalReferenceFiles, userFeedback, feedbackPhase, feedbackSpeed, feedbackSymptom, setupMode]);

  const generate = async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await requestJson('/api/ai/experiment-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: selectedFile || null,
          session_mode: sessionMode,
          pit_rule: sessionMode === 'ENDURANCE' ? pitRule : 'NOT_APPLICABLE',
          target_lap: targetLap ? Number(targetLap) : null,
          reference_lap: referenceLap ? Number(referenceLap) : null,
          user_feedback: userFeedback,
          feedback_phase: feedbackPhase || null,
          feedback_speed: feedbackSpeed || null,
          feedback_symptom: feedbackSymptom || null,
          setup_mode: setupMode,
          external_reference_files: externalReferenceFiles,
        }),
      });
      setResult(payload);
      if (payload.persisted && onStored) await onStored();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  };

  const plan = result?.plan;
  const usage = result?.ai_usage;
  const setupChange = plan?.action?.setup_change;

  return (
    <div className="flex flex-col gap-5">
      <div className="border border-border bg-surface p-5">
        <div className="flex items-start gap-3">
          <Sparkles className="mt-0.5 h-6 w-6 shrink-0 text-foreground" />
          <div className="min-w-0">
            <h3 className="text-lg font-semibold text-foreground">GCP AI Setup Experiment Planner</h3>
            <p className="mt-2 max-w-5xl text-base leading-relaxed text-foreground">
              로컬 엔진이 만든 드라이빙·셋업 후보 중 다음 주행에서 검증할 항목 하나를 선택합니다.
              원본 60Hz 데이터와 Garage61 CSV 원문·드라이버 식별자는 전송하지 않고, 로컬에서 만든 익명 컨센서스 요약과 셋업 후보만 사용합니다. 실행할 때 GCP 입력·출력 토큰을 사용합니다.
            </p>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="default"
            type="button"
            onClick={generate}
            disabled={!aiEnabled || !selectedFile || loading}
            className="flex min-h-[50px] w-full items-center justify-center gap-2  coach-primary-action px-5 py-3 text-base   transition  disabled:cursor-not-allowed disabled: disabled: sm:w-auto"
          >
            {loading ? (
              <><span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-white" /> AI 계획 생성 중</>
            ) : (
              <><Sparkles className="h-4 w-4" /> {aiEnabled ? 'GCP AI 계획 생성 · 토큰 사용' : 'GCP AI 미연결'}</>
            )}
          </Button>
        </div>
      </div>

      {error && <ErrorMessage message={error} />}

      {plan && (
        <div className="flex flex-col gap-5 rounded-2xl border border-border bg-background/60 p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-foreground">GCP AI가 선택한 다음 실험</div>
              <h3 className="mt-1 text-xl font-semibold text-foreground">{plan.title}</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                <span className="rounded-lg border border-accent/30 bg-accent/10 px-2.5 py-1 text-sm font-semibold text-foreground">{plan.decision}</span>
                <span className="rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1 text-sm font-semibold text-primary">신뢰도 {Math.round((plan.confidence || 0) * 100)}%</span>
                <span className="rounded-lg border border-border bg-muted px-2.5 py-1 text-sm font-mono text-muted-foreground">{plan.model}</span>
                <span className="rounded-lg border border-success/30 bg-success/10 px-2.5 py-1 text-sm font-semibold text-success">
                  장기 기억 {plan.memory_context?.available_similar_sessions ?? 0}개 조회 · {plan.memory_context?.similar_sessions_used ?? 0}개 인용
                </span>
                <span className={cx(
                  'rounded-lg border px-2.5 py-1 text-sm font-semibold',
                  result?.persisted
                    ? 'border-success/30 bg-success/10 text-success'
                    : 'border-warning/30 bg-warning/10 text-warning'
                )}>
                  {result?.persisted ? 'AI 계획 SQLite 저장됨' : 'AI 계획 SQLite 미저장'}
                </span>
              </div>
            </div>
            <div className="text-right text-xs font-mono text-muted-foreground">Plan {plan.plan_id}</div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-2xl border border-border bg-background p-4">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">가설</div>
              <p className="mt-2 text-base leading-relaxed text-foreground">{plan.hypothesis}</p>
            </div>
            <div className="rounded-2xl border border-border bg-background p-4">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">선택 이유</div>
              <p className="mt-2 text-base leading-relaxed text-foreground">{plan.rationale}</p>
            </div>
          </div>

          <div className="rounded-2xl border border-primary/25 bg-primary/15 p-4">
            <div className="text-sm font-semibold uppercase tracking-wider text-primary">이번 주행에서 할 일</div>
            <p className="mt-2 text-base font-bold leading-relaxed text-foreground">{plan.action?.instruction}</p>
            {setupChange && (
              <div className="mt-3 flex flex-wrap items-baseline gap-2 rounded-xl border border-warning/25 bg-warning/20 p-3 font-mono">
                <span className="text-base text-muted-foreground">{setupChange.label}</span>
                <span className="text-muted-foreground line-through">{formatSetupOriginal(setupChange)}</span>
                <span className="text-muted-foreground">→</span>
                <span className="text-xl font-semibold text-warning">{formatSetupTarget(setupChange)}</span>
              </div>
            )}
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <RuleList title="성공 조건" rules={plan.success_rules} />
            <RuleList title="원복 조건" rules={plan.revert_rules} tone="rose" />
          </div>

          <div className="grid gap-4 lg:grid-cols-[0.7fr_1.3fr]">
            <div className="rounded-2xl border border-border bg-background p-4 text-base text-foreground">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">필요 표본</div>
              <div className="mt-3 flex flex-col gap-1 font-mono text-sm">
                <div>new runs: {plan.required_samples?.new_runs}</div>
                <div>out laps/run: {plan.required_samples?.out_laps_per_run}</div>
                <div>timed laps/run: {plan.required_samples?.timed_laps_per_run}</div>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{plan.required_samples?.note}</p>
            </div>
            <div className="rounded-2xl border border-border bg-background p-4">
              <div className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">사용 근거</div>
              <div className="mt-3 flex flex-col gap-2">
                {(plan.evidence || []).map((item) => (
                  <div key={item.id} className="rounded-xl border border-border bg-muted/50 p-3 text-sm leading-relaxed text-foreground">
                    <span className="mr-2 font-mono font-semibold text-foreground">{item.id}</span>{item.summary}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <AIUsageBanner usage={usage} aiEnabled={aiEnabled} />
        </div>
      )}
    </div>
  );
}
