import { Badge } from "../components/ui/badge";
import { Alert, AlertDescription } from "../components/ui/alert";
import { CheckCircle2, Database } from "lucide-react";
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from "../components/ui/empty";
import React from "react";
import { EmptyState, ErrorMessage, MetricMini } from './WorkspaceUi';
import { CLASSIFICATION_LABELS, ISSUE_LABELS, SPEED_LABELS, formatBytes, formatLapSeconds, formatNumber, formatSigned, sessionModeLabel } from './analysisShared';

export function LongTermMemoryPanel({ memoryStatus, sessions = [], currentMemory }) {
  const enabled = Boolean(memoryStatus?.enabled);
  const counts = memoryStatus?.counts || {};
  const similar = currentMemory?.similar_sessions || [];
  const outLapAggregate = currentMemory?.current_out_lap?.aggregate || {};
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h3 className="font-semibold">개인 주행 기록</h3><p className="mt-1 text-sm text-muted-foreground">주행 지표와 셋업 실험을 로컬에 저장합니다. 저장·검색에는 AI 토큰을 사용하지 않습니다.</p></div>
        <Badge variant={enabled ? 'secondary' : 'destructive'}>{enabled ? '저장 활성화' : '저장 비활성화'}</Badge>
      </div>
      {!enabled && memoryStatus?.error && <ErrorMessage message={memoryStatus.error} />}
      <div className="grid grid-cols-3 gap-3">
        <MetricMini label="저장 세션" value={counts.sessions ?? 0} />
        <MetricMini label="AI 계획" value={counts.experiment_plans ?? 0} />
        <MetricMini label="DB 용량" value={memoryStatus?.size_bytes == null ? '--' : formatBytes(memoryStatus.size_bytes)} />
      </div>
      {currentMemory?.saved && (
        <Alert><CheckCircle2 aria-hidden="true" /><AlertDescription>현재 분석이 저장되었습니다. 유사한 과거 주행을 아래에서 확인할 수 있습니다.<span className="mt-1 block text-xs text-muted-foreground">기록 ID · {String(currentMemory.session_id || '').slice(0, 16)}</span></AlertDescription></Alert>
      )}
      {currentMemory?.current_out_lap?.available && (
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">현재 아웃랩 준비 패턴</h4>
            <span className="text-sm font-mono text-muted-foreground">로컬 요약 · 토큰 미사용</span>
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <MetricMini label="Brake events" value={formatNumber(outLapAggregate.brake_events_median, 1)} />
            <MetricMini label="Steering work" value={formatNumber(outLapAggregate.steering_work_deg_median, 0, '°')} />
            <MetricMini label="Surface temp Δ" value={formatSigned(outLapAggregate.surface_temp_delta_c_median, 2, ' °C')} />
            <MetricMini label="Pressure Δ" value={formatSigned(outLapAggregate.pressure_delta_kpa_median, 2, ' kPa')} />
          </div>
        </div>
      )}

      <div>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h4 className="text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            현재 조건과 유사한 과거 세션
          </h4>
          <span className="text-sm font-mono text-muted-foreground">{similar.length}개 검색</span>
        </div>
        {similar.length ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {similar.map((item) => {
              const topIssue = item.memory_summary?.top_issues?.[0];
              const outcome = item.experiment_outcomes?.[0];
              return (
                <div key={item.session_id} className="rounded-2xl border border-border bg-background/60 p-4 text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-bold text-foreground">{item.car_name} @ {item.track_name}</div>
                      <div className="mt-1 text-muted-foreground">{item.track_config || '기본 레이아웃'} · {sessionModeLabel(item.session_mode)}</div>
                    </div>
                    <span className="rounded-lg border border-accent/25 bg-accent/10 px-2 py-1 font-mono font-semibold text-foreground">
                      {Math.round(Number(item.similarity_score || 0) * 100)}%
                    </span>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 font-mono text-sm">
                    <div><span className="text-muted-foreground">최고 랩</span><div className="mt-1 text-primary">{formatLapSeconds(item.best_lap_s)}</div></div>
                    <div><span className="text-muted-foreground">노면 온도</span><div className="mt-1 text-warning">{formatNumber(item.track_temp_c, 1, ' °C')}</div></div>
                    <div><span className="text-muted-foreground">셋업 판단</span><div className="mt-1 text-foreground">{{ TEST: '시험 권장', KEEP: '유지', REVERT: '원복', HOLD: '보류' }[item.setup_decision] || item.setup_decision || '--'}</div></div>
                  </div>
                  {topIssue && (
                    <p className="mt-3 leading-relaxed text-muted-foreground">
                      {topIssue.corner_id} · {SPEED_LABELS[topIssue.speed_class] || topIssue.speed_class} · {ISSUE_LABELS[topIssue.issue_type] || topIssue.issue_type} · {formatSigned(topIssue.delta_s)}
                    </p>
                  )}
                  {outcome && (
                    <div className="mt-3 rounded-xl border border-warning/20 bg-warning/15 p-2 text-warning">
                      과거 A/B: <b>{outcome.verdict}</b> · {outcome.reason}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState text="동일 차량·트랙의 과거 세션이 아직 없습니다. 파일을 분석할수록 개인화 근거가 누적됩니다." />
        )}
      </div>

      <details>
        <summary className="cursor-pointer text-sm font-semibold text-muted-foreground">
          최근 DB 저장 세션 <span className="ml-3 font-mono font-normal">최근 {sessions.length}개</span>
        </summary>
        <div className="mt-3"><HistoryPanel history={sessions} /></div>
      </details>
    </div>
  );
}

export function HistoryPanel({ history = [] }) {
  if (!history.length) return <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Database aria-hidden="true" /></EmptyMedia><EmptyTitle>저장된 주행 분석이 없습니다.</EmptyTitle><EmptyDescription>주행 유형을 지정해 개인 기록으로 저장하세요.</EmptyDescription></EmptyHeader></Empty>;
  return (
    <div className="flex flex-col gap-2">
      {history.map((item) => (
        <div key={item.session_id || item.id} className="coach-history-entry flex flex-col gap-3 text-sm xl:flex-row xl:items-center xl:justify-between">
          <div className="min-w-0">
            <div className="truncate font-bold text-foreground">
              {item.car_name || item.car} · {item.track_name || item.track} {item.track_config || ''}
            </div>
            <div className="mt-1 truncate text-muted-foreground">{item.filename || item.file}</div>
          </div>
          <div className="flex flex-wrap items-center gap-3 font-mono">
            <span className="text-muted-foreground">{sessionModeLabel(item.session_mode)}</span>
            <span className="text-primary">{formatLapSeconds(item.best_lap_s) || item.targetTime || '--'}</span>
            <span className="text-foreground">{CLASSIFICATION_LABELS[item.classification] || item.classification || '--'}</span>
            <span className="text-muted-foreground">
              {new Date((item.analyzed_at || item.createdAt || 0) * (item.analyzed_at ? 1000 : 1)).toLocaleString()}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
