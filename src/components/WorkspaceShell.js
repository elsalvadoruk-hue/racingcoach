import ScrollToTop from './ScrollToTop';
import React, { useEffect, useState } from 'react';
import { ArrowRight, Check, CircleHelp, Gauge, LockKeyhole } from 'lucide-react';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Switch } from './ui/switch';
import { Label } from './ui/label';

const SCREEN = {
  summary: ['OVERVIEW', '주행 요약', '이번 주행의 기록과 다음 랩에서 집중할 한 가지'],
  compare: ['TELEMETRY', '코너 비교', '같은 위치에서 속도, 조작과 주행라인을 비교하세요.'],
  setup: ['SETUP LAB', '셋업 실험', '느낀 증상에서 시작해, 한 가지 변경을 검증하세요.'],
  live: ['RACE RADIO', '음성 코치', '스포터, 경기 전략과 주행 중 대화를 한곳에서'],
  history: ['DRIVING JOURNAL', '기록', '주행 분석과 셋업 변경의 흐름을 확인하세요.'],
  settings: ['WORKSPACE', '설정', '연결 상태와 개인 기록을 관리하세요.'],
  guide: ['GETTING STARTED', '사용 가이드', '필요한 기능의 사용법을 빠르게 찾아보세요.'],
};

export function WorkflowSteps({ steps, current = 0, label = '실험 진행 순서' }) {
  return <ol aria-label={label} className="coach-workflow">{steps.map((step, index) => <li key={step} aria-current={index === current ? 'step' : undefined} className={index < current ? 'is-complete' : ''}>
    <span className="coach-step-number" aria-hidden="true">{index < current ? <Check size={13} /> : index + 1}</span>
    <span>{step}</span>{index < steps.length - 1 && <ArrowRight size={12} aria-hidden="true" className="coach-step-arrow" />}
  </li>)}</ol>;
}

// Jump list for the current screen: follows the page (inside the sticky sidebar), opens a folded panel
// (Panel listens for coach-reveal-section) and marks the section being read.
export function SectionNav({ sections }) {
  const [current, setCurrent] = useState(null);
  const key = (sections || []).map((item) => item.id).join('|');
  useEffect(() => {
    if (!sections?.length || typeof IntersectionObserver === 'undefined') return undefined;
    const seen = new Map();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => seen.set(entry.target.id, entry.isIntersecting ? entry.boundingClientRect.top : null));
      const visible = sections.filter((item) => seen.get(item.id) != null);
      if (visible.length) setCurrent(visible[0].id);
    }, { rootMargin: '-80px 0px -45% 0px' });
    sections.forEach((item) => { const element = document.getElementById(item.id); if (element) observer.observe(element); });
    return () => observer.disconnect();
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!sections?.length) return null;
  const go = (id) => {
    window.dispatchEvent(new CustomEvent('coach-reveal-section', { detail: id }));
    setCurrent(id);
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
  };
  return <nav aria-label="이 화면의 항목" className="coach-section-nav">
    <p className="coach-nav-caption">이 화면의 항목</p>
    {sections.map((item) => <button key={item.id} type="button" onClick={() => go(item.id)} aria-current={current === item.id ? 'location' : undefined} className="coach-section-link">{item.label}</button>)}
  </nav>;
}

export default function WorkspaceShell({ tabs, activeTab, onTab, hoverHelpEnabled, onToggleHelp, onGuide, hasAnalysis, trialActive, sections = null, children }) {
  const [eyebrow, title, description] = SCREEN[activeTab] || SCREEN.summary;
  return <div className="coach-page">
    <a href="#coach-content" className="coach-skip-link">본문으로 이동</a>
    <ScrollToTop />
    <header className="coach-masthead">
      <div className="coach-identity"><span className="coach-brand-mark" aria-hidden="true"><Gauge size={23} strokeWidth={1.6} /></span><div><h1>iRacing 주행 코치</h1><p className="coach-brand-caption">DRIVING LAB</p></div></div>
      <div className="coach-tools">
        <div className="coach-help-switch"><CircleHelp size={15} aria-hidden="true" /><Label htmlFor="hover-help-switch">마우스 오버 설명</Label><Switch id="hover-help-switch" aria-label="마우스 오버 설명" checked={hoverHelpEnabled} onCheckedChange={onToggleHelp} /></div>
        <Button variant="ghost" size="sm" onClick={onGuide} className="coach-guide-link">처음 사용 가이드<ArrowRight data-icon="inline-end" aria-hidden="true" /></Button>
      </div>
    </header>
    <div className="coach-layout">
      <aside className="coach-sidebar"><p className="coach-nav-caption">WORKSPACE</p><nav aria-label="분석 화면">{tabs.map(({ key, label, icon: Icon }) => <Button variant="ghost" key={key} id={`tab-${key}`} aria-current={activeTab === key ? 'page' : undefined} onClick={() => onTab(key)} className={`coach-nav-item ${key === 'history' ? 'coach-nav-divider' : ''}`}>
        <Icon data-icon="inline-start" aria-hidden="true" /><span className="coach-nav-label">{label}</span>
      </Button>)}</nav><div className="coach-sidebar-note">{trialActive ? <LockKeyhole size={14} aria-hidden="true" /> : <span className={`coach-status-dot ${hasAnalysis ? 'is-ready' : ''}`} aria-hidden="true" />}<span>{trialActive ? '셋업 실험 진행 중' : hasAnalysis ? '분석 완료' : '주행 선택 대기'}</span></div><SectionNav sections={sections} /></aside>
      <main id="coach-content" tabIndex={-1} className="coach-content">
        <header className="coach-screen-heading"><div><span className="coach-eyebrow">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>{trialActive && <Badge variant="outline">실험 진행 중</Badge>}</header>
        {children}
        <footer className="coach-footer"><span>IRACING DRIVING LAB</span></footer>
      </main>
    </div>
  </div>;
}
