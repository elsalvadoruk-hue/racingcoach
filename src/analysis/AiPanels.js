import React from 'react';
import { EmptyState } from './WorkspaceUi';

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
