import { Button } from './ui/button';
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CircleHelp } from 'lucide-react';
import { helpFor } from './helpContent';
import { useGame } from '../lib/gameContext';
import { readGameItem, writeGameItem } from '../lib/gameStorage';

export const HOVER_HELP_KEY = 'iracing-tuner.hover-help.v1';
export function useHoverHelpSetting() {
  const { selectedGame } = useGame();
  const [enabled, setEnabled] = useState(() => {
    try { return readGameItem(HOVER_HELP_KEY, selectedGame) !== 'false'; }
    catch { return true; }
  });
  useEffect(() => { setEnabled(readGameItem(HOVER_HELP_KEY, selectedGame) !== 'false'); }, [selectedGame]);
  const toggle = () => {
    const next = !enabled;
    setEnabled(next);
    try { writeGameItem(HOVER_HELP_KEY, selectedGame, String(next)); }
    catch { /* The toggle still works if browser storage is unavailable. */ }
  };
  return [enabled, toggle];
}

export function HelpHint({ topic, label }) {
  const content = helpFor(topic);
  if (!content) return null;
  return <button type="button" data-help={topic} data-help-trigger="true"
    aria-label={`${label || content[0]} 도움말`}
    className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400">
    <CircleHelp aria-hidden="true" className="h-4 w-4" />
  </button>;
}

// Delegation keeps help on native controls without wrapping or nesting buttons.
// Only explicitly annotated features participate; chart measurement tooltips stay separate.
export function HelpLayer({ hoverEnabled = true }) {
  const [active, setActive] = useState(null);
  const [position, setPosition] = useState(null);
  const current = useRef(null);
  const popup = useRef(null);
  const hideTimer = useRef(null);
  useEffect(() => {
    const clearTimer = () => window.clearTimeout(hideTimer.current);
    const close = () => { clearTimer(); current.current = null; setActive(null); setPosition(null); };
    if (!hoverEnabled) close();
    const anchorOf = (node) => node instanceof Element ? node.closest('[data-help]') : null;
    const show = (anchor, pinned = false) => {
      if (!anchor || !helpFor(anchor.dataset.help)) return;
      clearTimer();
      if (current.current?.pinned && current.current.anchor !== anchor && !pinned) return;
      if (current.current?.anchor === anchor) {
        if (pinned) { current.current = { ...current.current, pinned: true }; setActive(current.current); }
        return;
      }
      current.current = { anchor, topic: anchor.dataset.help, pinned };
      setPosition(null); setActive(current.current);
    };
    const over = (event) => {
      if (!hoverEnabled) return;
      if (popup.current?.contains(event.target)) { clearTimer(); return; }
      show(anchorOf(event.target));
    };
    const out = (event) => {
      const next = event.relatedTarget;
      if (current.current?.pinned || current.current?.anchor.contains(next) || popup.current?.contains(next)) return;
      clearTimer(); hideTimer.current = window.setTimeout(() => {
        if (!current.current?.pinned && !current.current?.anchor.contains(document.activeElement)) close();
      }, 160);
    };
    const focus = (event) => show(anchorOf(event.target));
    const blur = (event) => {
      if (!current.current?.pinned && !current.current?.anchor.contains(event.relatedTarget)) close();
    };
    const click = (event) => {
      const trigger = event.target instanceof Element ? event.target.closest('[data-help-trigger]') : null;
      if (trigger) {
        if (current.current?.anchor === trigger && current.current.pinned) close();
        else show(trigger, true);
      } else if (!popup.current?.contains(event.target)) close();
    };
    const key = (event) => { if (event.key === 'Escape') close(); };
    const navigation = () => close();
    const listeners = { mouseover: over, mouseout: out, focusin: focus, focusout: blur, click, keydown: key };
    Object.entries(listeners).forEach(([name, handler]) => document.addEventListener(name, handler));
    window.addEventListener('coach-navigate', navigation);
    return () => {
      clearTimer(); Object.entries(listeners).forEach(([name, handler]) => document.removeEventListener(name, handler));
      window.removeEventListener('coach-navigate', navigation);
    };
  }, [hoverEnabled]);
  useLayoutEffect(() => {
    if (!active || !popup.current) return undefined;
    const { anchor } = active;
    const id = 'coach-feature-tooltip';
    const oldDescription = anchor.getAttribute('aria-describedby');
    anchor.setAttribute('aria-describedby', [oldDescription, id].filter(Boolean).join(' '));
    const place = () => {
      if (!anchor.isConnected || !anchor.getClientRects().length) { current.current = null; setActive(null); return; }
      const box = anchor.getBoundingClientRect();
      const tip = popup.current.getBoundingClientRect();
      const left = Math.max(12, Math.min(box.left, window.innerWidth - tip.width - 12));
      const below = box.bottom + 8;
      const top = Math.max(12, Math.min(below + tip.height <= window.innerHeight - 12 ? below : box.top - tip.height - 8, window.innerHeight - tip.height - 12));
      setPosition({ left, top });
    };
    place(); window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    return () => {
      if (oldDescription === null) anchor.removeAttribute('aria-describedby'); else anchor.setAttribute('aria-describedby', oldDescription);
      window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true);
    };
  }, [active]);
  if (!active) return null;
  const [title, description] = helpFor(active.topic);
  return createPortal(<div ref={popup} id="coach-feature-tooltip" role="tooltip"
    className="rounded-xl border border-primary/50 bg-background p-4 text-sm leading-relaxed text-foreground shadow-2xl"
    style={{ position: 'fixed', zIndex: 10000, width: 'min(340px, calc(100vw - 24px))', maxHeight: 'calc(100dvh - 24px)', overflowY: 'auto', visibility: position ? 'visible' : 'hidden', ...position }}>
    <p className="mb-2 font-bold text-primary">{title}</p><p>{description}</p>
    <p className="mt-2 text-xs text-muted-foreground">Esc 또는 바깥을 누르면 닫힙니다.</p>
  </div>, document.body);
}

export function QuickStart({ onGuide, onSetup, hasAnalysis }) {
  return <aside aria-label="처음 사용 안내" className="coach-start">
    <div><p>{hasAnalysis ? '다음 단계' : '첫 주행 시작하기'}</p><p>{hasAnalysis ? '느낀 증상을 셋업 실험으로 이어가세요.' : 'IBT 파일을 선택해 코너별 주행을 확인하세요.'}</p></div>
    <div className="coach-start-actions"><Button variant="outline" type="button" aria-label="처음 사용 가이드 열기" onClick={onGuide}>처음 사용 가이드 열기 →</Button>
      {hasAnalysis && <Button variant="outline" type="button" aria-label="증상 입력하러 가기" onClick={onSetup}>증상 입력하러 가기 →</Button>}</div>
  </aside>;
}
