import React from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from './ui/card';
import { HelpHint } from './HelpGuide';
import { cn } from '../lib/utils';

export function LiveHeader({ title, description, topic, icon: Icon, action }) {
  return <CardHeader className="coach-live-header p-0">
    <div><CardTitle asChild><h2>{Icon && <Icon className="coach-section-icon" size={18} aria-hidden="true" />}{title}{topic && <HelpHint topic={topic} />}</h2></CardTitle>{description && <CardDescription className="mt-2">{description}</CardDescription>}</div>
    {action}
  </CardHeader>;
}

export function LiveMetric({ label, value, note, tone }) {
  const pending = value == null || ['수집 중', '조건 확인 필요', '위치 미확인', '—'].includes(value);
  const descriptive = pending || (typeof value === 'string' && /[가-힣]{2}/.test(value));
  return <Card className="bg-muted/40 shadow-none"><CardHeader className="p-4 pb-0"><CardDescription className="min-h-10 leading-relaxed">{label}</CardDescription></CardHeader>
    <CardContent className="p-4 pt-2"><p className={cn('coach-fuel-value', descriptive && 'is-text', pending && 'text-muted-foreground', tone === 'warning' && !pending && 'text-warning')}>{value ?? '수집 중'}</p>{note && <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{note}</p>}</CardContent>
  </Card>;
}
