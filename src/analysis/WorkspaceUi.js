import React, { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "../components/ui/card";
import { HelpHint } from "../components/HelpGuide";
import { Button } from "../components/ui/button";
import { ChevronDown, Database, XCircle } from "lucide-react";
import { Empty, EmptyHeader, EmptyMedia, EmptyDescription } from "../components/ui/empty";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "../components/ui/dialog";
import { cx, sessionModeLabel } from './analysisShared';

export function Panel({
  title,
  icon: Icon,
  children,
  className = '',
  action = null,
  collapsible = false,
  defaultOpen = true,
  id = undefined,
  lazy = false,
}) {
  const [open, setOpen] = useState(defaultOpen);
  // lazy: build the content the first time the panel opens (for content that fetches data)
  const [opened, setOpened] = useState(defaultOpen);
  useEffect(() => { if (open) setOpened(true); }, [open]);
  useEffect(() => {
    if (!id) return undefined;
    // A folded panel also opens for the section it wraps ("voice-review-panel" holds "voice-review").
    const reveal = (event) => { if (event.detail === id || `${event.detail}-panel` === id) setOpen(true); };
    window.addEventListener('coach-reveal-section', reveal);
    return () => window.removeEventListener('coach-reveal-section', reveal);
  }, [id]);

  return (
    <Card asChild><section
      id={id}
      className={cx(
        'coach-panel scroll-mt-24',
        className
      )}
    >
      <CardHeader className={cx('coach-panel-heading flex flex-row flex-wrap items-center justify-between gap-3', !open && 'mb-0')}>
        <CardTitle asChild><h2 data-help={title} className="flex items-center gap-2.5">
          {Icon && <Icon className="h-5 w-5 text-muted-foreground" />}
          {title}
        </h2></CardTitle>
        <div className="flex items-center gap-2">
          <HelpHint topic={title} />
          {action}
          {collapsible && (
            <Button variant="outline"
              type="button"
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
              className="flex min-h-[42px] items-center gap-2  border   px-3 py-2 text-sm   transition  "
            >
              {open ? '접기' : '펼치기'}
              <ChevronDown className={cx('h-5 w-5 transition-transform', open && 'rotate-180')} />
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="coach-panel-content" hidden={!open}>{!lazy || opened ? children : null}</CardContent>
    </section></Card>
  );
}

export function MetricCard({ label, value, note, icon: Icon, tone = 'cyan' }) {
  const readableLabel = ({ 'Median delta': '중앙 랩타임 변화', 'Best delta': '최고 랩타임 변화', 'Stdev delta': '랩타임 흔들림 변화', 'Pace slope delta': '장기 페이스 변화', 'Steering work': '조향 수정량', Median: '중앙 랩타임', PACE: '랩 페이스', CONSISTENCY: '주행 일관성', DRIVEABILITY: '운전 편의', 'Front median': '앞 차고 중앙값', 'Rear median': '뒤 차고 중앙값', 'Rear wing': '리어 윙', 'Hot pressure slope': '온간 압력 추세', 'Surface temp slope': '표면 온도 추세' })[label] || label;
  return (
    <Card className="coach-stat" data-tone={tone} data-placeholder={value == null || ['--', '—', '비교 기준 없음', '측정 불가', '수집 중', '연결 필요', '설정 필요', '연결 확인 필요', '주행 파일 대기'].includes(value)}>
      <CardHeader className="coach-stat-label">
        <span data-help={label}>{readableLabel}<HelpHint topic={label} /></span>
        {Icon && <Icon className="h-4 w-4 opacity-70" />}
      </CardHeader>
      <CardContent className="p-0"><div className="coach-stat-value font-mono">{value ?? '--'}</div>
      {note && <CardDescription className="coach-stat-note">{note}</CardDescription>}</CardContent>
    </Card>
  );
}

export function MetricMini({ label, value }) {
  return (
    <div className="rounded-xl border border-border bg-muted/70 p-3">
      <div data-help={label} className="flex items-center gap-1 text-xs uppercase tracking-wider text-muted-foreground">{label}<HelpHint topic={label} /></div>
      <div className="mt-1.5 font-mono text-base font-semibold text-foreground">{value}</div>
    </div>
  );
}

export function RuleList({ title, rules = [], tone = 'emerald' }) {
  const toneClass = tone === 'rose'
    ? 'border-destructive/20 bg-destructive/15 text-destructive'
    : 'border-success/20 bg-success/15 text-success';
  return (
    <div>
      <h4 className="mb-2 text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">{title}</h4>
      <div className="flex flex-col gap-2">
        {rules.length ? rules.map((rule) => (
          <div key={`${rule.metric}-${rule.operator}-${rule.target}`} className={cx('rounded-xl border p-3 text-base', toneClass)}>
            <div className="font-mono text-sm font-semibold">
              {rule.metric} {rule.operator} {rule.target} {rule.unit}
            </div>
            <div className="mt-1 text-sm leading-relaxed text-foreground">{rule.description}</div>
          </div>
        )) : <div className="text-sm text-muted-foreground">해당 조건이 없습니다.</div>}
      </div>
    </div>
  );
}

export function EmptyState({ text }) {
  return <Empty className="min-h-[150px] border"><EmptyHeader><EmptyMedia variant="icon"><Database aria-hidden="true" /></EmptyMedia><EmptyDescription>{text}</EmptyDescription></EmptyHeader></Empty>;
}

export function ErrorMessage({ message }) {
  return <Alert variant="destructive"><XCircle aria-hidden="true" /><AlertDescription>{message}</AlertDescription></Alert>;
}

export function SessionModeChangeDialog({ prompt, loading, onCancel, onConfirm }) {
  if (!prompt) return null;
  const currentLabels = (prompt.currentModes || []).map(sessionModeLabel).join(', ');
  return <Dialog open onOpenChange={(open) => { if (!open && !loading) onCancel(); }}>
    <DialogContent onCloseAutoFocus={(event) => { event.preventDefault(); prompt.returnFocus?.focus(); }} onEscapeKeyDown={(event) => { if (loading) event.preventDefault(); }} onInteractOutside={(event) => { if (loading) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>DB 세션 유형을 변경하시겠습니까?</DialogTitle><DialogDescription className="break-all">{prompt.filename}</DialogDescription></DialogHeader>
      <div className="grid gap-3 sm:grid-cols-2">
        <Card><CardHeader><CardDescription>현재 DB 분류</CardDescription><CardTitle>{currentLabels || '미지정'}</CardTitle></CardHeader></Card>
        <Card><CardHeader><CardDescription>변경 후</CardDescription><CardTitle>{sessionModeLabel(prompt.nextMode)}</CardTitle></CardHeader></Card>
      </div>
      <p className="text-sm leading-relaxed text-muted-foreground">해당 IBT의 기존 세션·랩·코너 장기 기억을 새 유형으로 교체합니다. 원본 텔레메트리 파일은 변경되지 않습니다.</p>
      <DialogFooter><Button variant="outline" onClick={onCancel} disabled={loading}>취소</Button><Button onClick={onConfirm} disabled={loading}>{loading ? '변경 중' : 'DB 유형 변경'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
