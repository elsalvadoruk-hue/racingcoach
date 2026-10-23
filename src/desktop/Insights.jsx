import { Button } from '../components/ui/button';
import React, { useEffect, useRef } from 'react';
import { Badge } from '../components/ui/badge';
import { Target, Check, ArrowRight } from 'lucide-react';
import { signed, cornerLabel } from './sessionData';
import CornerPhases from './CornerPhases';
import RacingLine from './RacingLine';
import { HandlingBadges, HandlingDetail } from './HandlingViews';

export default function Insights({ corner, corners, names, onJump, practice, onPractice, notes, activeNote, onNote, compact = false }) {
  const listRef = useRef(null);
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = 0; }, [corner?.id]);
  useEffect(() => {
    const list = listRef.current, item = list?.querySelector('[aria-pressed=true]');
    const scroller = list?.closest('.insight-detail-scroll') || list;
    if (scroller && item) { const a = scroller.getBoundingClientRect(), b = item.getBoundingClientRect(); if (b.top < a.top || b.bottom > a.bottom) scroller.scrollTop += b.top - a.top - (a.height - b.height) / 2; }
  }, [activeNote]);
  const focus = corner || [...corners].filter(c => Number.isFinite(c.delta_s)).sort((a, b) => b.delta_s - a.delta_s)[0];
  if (!focus) return <section className="coaching-panel"><h2>구간 측정값</h2><p>비교 가능한 코너 데이터가 없습니다.</p></section>;
  const inspected = notes.find(item => item.id === activeNote);
  const practiceButton = <Button size="sm" disabled={!focus.id} variant={practice === focus.id ? 'secondary' : 'default'} onClick={() => onPractice(focus.id)}>{practice === focus.id ? <Check /> : <Target />}{practice === focus.id ? '연습 구간으로 선택됨' : '이 코너를 연습 구간으로'}</Button>;
  // Narrow sidebar: a summary that never scrolls; the full explanation opens when the panel is enlarged.
  if (compact && corner) {
    const handling = corner.handling || [];
    const unknown = (corner.handling_coverage || []).reduce((sum, item) => sum + item.unknown, 0);
    return <section className="coaching-panel is-compact" data-corner={focus.id}><div className="panel-heading"><h2>{`${focus.id} · 비교 포인트`}</h2><span className="numeric">{signed(focus.delta_s)} s</span></div>
      <section className="comparison-facts" aria-label="선택한 두 랩 비교"><CornerPhases corner={corner} /></section>
      {(handling.length > 0 || unknown > 0) && <div className="insight-mini-row"><span>차 거동</span>{handling.length ? <HandlingBadges items={handling} /> : <small>관측 없음</small>}{unknown > 0 && <small>{unknown}회 확인 필요</small>}</div>}
      <div className="insight-actions numbered-insights is-compact" aria-label="이 코너의 비교 포인트">{notes.length ? notes.slice(0, 3).map(item => <button key={item.key} data-testid={`insight-note-${item.id}`} data-note-key={item.key} aria-pressed={activeNote === item.id} disabled={!Number.isFinite(item.jumpAt)} onClick={() => onNote(item)}><i className="insight-number">{item.id}</i><span><b>{item.label}</b><strong>{item.value}</strong></span><ArrowRight size={14} /></button>) : <div className="insight-empty">관측된 큰 차이가 없습니다.</div>}
        {notes.length > 1 && <small className="insight-more">비교 포인트 모두 {notes.length}개 · 펼치면 전부 보입니다</small>}</div>
      {practiceButton}
      <p className="insight-expand-hint">빈 곳을 누르면 크게 펼쳐 자세히 봅니다.</p>
    </section>;
  }
  return <section className="coaching-panel" data-corner={focus.id}><div className="panel-heading"><h2>{corner ? `${focus.id} · 비교 포인트` : '손실 큰 구간부터 확인'}</h2><span className="numeric">{signed(focus.delta_s)} s</span></div>
    {corner && <section className="comparison-facts" aria-label="선택한 두 랩 비교"><h3>선택한 두 랩 비교</h3><CornerPhases corner={corner} /><RacingLine line={corner.line} /></section>}
    {corner && <HandlingDetail items={corner.handling} coverage={corner.handling_coverage} />}
    {corner && <div className="insight-summary"><h3>그래프 비교 포인트</h3><Badge variant="secondary">{notes.length}개</Badge></div>}
    {corner ? <div ref={listRef} className="insight-actions numbered-insights" aria-label="이 코너의 비교 포인트">{notes.length ? notes.map(item => <button key={item.key} data-testid={`insight-note-${item.id}`} data-note-key={item.key} data-channel={item.channel} data-pct={item.jumpAt} aria-pressed={activeNote === item.id} disabled={!Number.isFinite(item.jumpAt)} onClick={() => onNote(item)}><i className="insight-number">{item.id}</i><span><b>{item.label}</b><strong>{item.value}</strong><small>{item.text}</small></span><ArrowRight size={14} /></button>) : <div className="insight-empty">관측된 큰 차이가 없습니다. 빠진 채널은 오류나 개입 없음으로 판정하지 않습니다. 상세 수치에서 전체 측정값을 볼 수 있습니다.</div>}</div> : <div className="insight-actions">{[...corners].filter(c => c.delta_s > 0).sort((a, b) => b.delta_s - a.delta_s).slice(0, 3).map(c => <button key={c.id} onClick={() => onJump(c.id, c.apex_pct)}><span><b>{c.id} · {cornerLabel(c, names)}</b><strong>{signed(c.delta_s)} s</strong><ArrowRight size={14} /></span></button>)}</div>}
    <div className="practice-note"><span>번호 = 비교 포인트 · 범위 = 위치 차이 / 시간 구간</span><p>{inspected?.hint || focus.driver_action || '차이는 조작 오류를 단정하지 않습니다. 반복 여부는 랩 분석의 코너별 일관성에서 확인하세요.'}</p></div>
    {practiceButton}
  </section>;
}
