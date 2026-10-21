import { Button } from '../components/ui/button';
import React, { useEffect, useRef } from 'react';
import { Badge } from '../components/ui/badge';
import { Target, Check, ArrowRight } from 'lucide-react';
import { signed, cornerLabel } from './sessionData';
import CornerPhases from './CornerPhases';
import { HandlingDetail } from './HandlingViews';

export default function Insights({ corner, corners, names, onJump, practice, onPractice, notes, activeNote, onNote }) {
  const listRef = useRef(null);
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = 0; }, [corner?.id]);
  useEffect(() => {
    const list = listRef.current, item = list?.querySelector('[aria-pressed=true]');
    if (list && item) { const a = list.getBoundingClientRect(), b = item.getBoundingClientRect(); if (b.top < a.top || b.bottom > a.bottom) list.scrollTop += b.top - a.top - (a.height - b.height) / 2; }
  }, [activeNote]);
  const focus = corner || [...corners].filter(c => Number.isFinite(c.delta_s)).sort((a, b) => b.delta_s - a.delta_s)[0];
  if (!focus) return <section className="coaching-panel"><h2>구간 측정값</h2><p>비교 가능한 코너 데이터가 없습니다.</p></section>;
  const inspected = notes.find(item => item.id === activeNote);
  return <section className="coaching-panel" data-corner={focus.id}><div className="panel-heading"><h2>{corner ? `${focus.id} · 비교 포인트` : '손실 큰 구간부터 확인'}</h2><span className="numeric">{signed(focus.delta_s)} s</span></div>
    {corner && <CornerPhases corner={corner} />}
    {corner && <HandlingDetail items={corner.handling} coverage={corner.handling_coverage} />}
    {corner && <div className="insight-summary"><Badge variant="secondary">{notes.length}개 포인트</Badge><span>최대 5개 · 주요 차이 선별</span></div>}
    {corner ? <div ref={listRef} className="insight-actions numbered-insights" aria-label="이 코너의 비교 포인트">{notes.length ? notes.map(item => <button key={item.key} data-testid={`insight-note-${item.id}`} data-note-key={item.key} data-channel={item.channel} data-pct={item.jumpAt} aria-pressed={activeNote === item.id} disabled={!Number.isFinite(item.jumpAt)} onClick={() => onNote(item)}><i className="insight-number">{item.id}</i><span><b>{item.label}</b><strong>{item.value}</strong><small>{item.text}</small></span><ArrowRight size={14} /></button>) : <div className="insight-empty">관측된 큰 차이가 없습니다. 빠진 채널은 오류나 개입 없음으로 판정하지 않습니다. 상세 수치에서 전체 측정값을 볼 수 있습니다.</div>}</div> : <div className="insight-actions">{[...corners].filter(c => c.delta_s > 0).sort((a, b) => b.delta_s - a.delta_s).slice(0, 3).map(c => <button key={c.id} onClick={() => onJump(c.id, c.apex_pct)}><span><b>{c.id} · {cornerLabel(c, names)}</b><strong>{signed(c.delta_s)} s</strong><ArrowRight size={14} /></span></button>)}</div>}
    <div className="practice-note"><span>번호 = 비교 포인트 · 범위 = 위치 차이 / 시간 구간</span><p>{inspected?.hint || focus.driver_action || '차이는 조작 오류를 단정하지 않습니다. 반복 여부는 랩 분석의 코너별 일관성에서 확인하세요.'}</p></div>
    <Button size="sm" disabled={!focus.id} variant={practice === focus.id ? 'secondary' : 'default'} onClick={() => onPractice(focus.id)}>{practice === focus.id ? <Check /> : <Target />}{practice === focus.id ? '연습 구간으로 선택됨' : '이 코너를 연습 구간으로'}</Button>
  </section>;
}
