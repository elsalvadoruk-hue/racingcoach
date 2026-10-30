import React, { useState } from 'react';
import { Badge } from '../components/ui/badge';
import { Field, FieldGroup, FieldLabel, FieldSet, FieldLegend } from '../components/ui/field';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import Panel from './OverlayPanel';
import { formationItems, formationDefaults, formationSelection, modules, moduleTiming, phaseOrder } from './overlayCatalog';

function SelectionPanel({ label, description, filter, legend, footer, children }) {
  return <Panel title="표시 항목 · 복수 선택" description={description}
    className="library-panel" label={label} footer={<span><span className="legend-dot"/>{footer}</span>}>
    {filter}
    <FieldSet className="library-scroll">
      <FieldLegend className="sr-only">{legend}</FieldLegend>
      <FieldGroup>{children}</FieldGroup>
    </FieldSet>
  </Panel>;
}

function Choice({ item, checked, disabled, unavailable = disabled, badge, timing, onChange }) {
  return <Field data-disabled={unavailable || undefined}>
    <FieldLabel className="module-choice" data-checked={checked} data-unavailable={unavailable || undefined}>
      <input type="checkbox" aria-label={item.name} checked={checked} disabled={disabled} onChange={onChange}/>
      <span className="module-choice-copy">
        <span className="module-choice-title"><b>{item.name}</b>{badge && <Badge variant="outline">{badge}</Badge>}</span>
        <small>{item.desc}</small>
        <em>{timing}</em>
      </span>
    </FieldLabel>
  </Field>;
}

export function FormationChoices({ config, game, onChange }) {
  const [filter, setFilter] = useState('all');
  const chosen = config.formation_items || formationDefaults;
  const selected = formationSelection(config, game);
  const items = filter === 'review' ? formationItems.filter(item => item.review) : formationItems;
  const toggle = id => onChange({ formation_items: chosen.includes(id) ? chosen.filter(key => key !== id) : [...chosen, id] });
  return <SelectionPanel label="포메이션 표시 항목" legend="출발 준비와 지난번 돌아보기"
    description="출발 순위는 항상 표시합니다. 함께 볼 정보를 선택하세요."
    footer={`${selected.length}개 선택 · 출발 순위 항상 표시`}
    filter={<ToggleGroup type="single" value={filter} onValueChange={value => value && setFilter(value)}
      aria-label="포메이션 항목 필터" className="library-filter">
      <ToggleGroupItem value="all">전체 {formationItems.length}개</ToggleGroupItem>
      <ToggleGroupItem value="review">지난번 돌아보기</ToggleGroupItem>
    </ToggleGroup>}>
    {items.map(item => {
      const unsupported = Boolean(item.game && item.game !== game);
      const fixed = item.id === '1';
      return <Choice key={item.id} item={item} checked={fixed || (!unsupported && chosen.includes(item.id))}
        disabled={fixed || unsupported} unavailable={unsupported} badge={fixed ? '항상 표시' : unsupported ? `${item.game === 'lmu' ? 'LMU' : 'iRacing'} 전용` : undefined}
        timing={unsupported ? '현재 게임에서 지원하지 않음' : fixed ? '출발 순위 · 참가 규모' : item.review ? '지난번 돌아보기 · 8초 간격 순환' : '출발 준비 · 8초 간격 순환'}
        onChange={() => toggle(item.id)}/>;
    })}
  </SelectionPanel>;
}

export function CoachingChoices({ selected, session, onToggle }) {
  const [filter, setFilter] = useState('recommended');
  const visible = filter === 'recommended' ? modules.filter(item => item.sessions.includes(session)) : modules;
  return <SelectionPanel label="표시 항목 목록" legend="상황별로 표시할 코칭 항목"
    description="필요한 항목을 모두 체크하세요. 상황에 맞춰 자동으로 표시합니다."
    footer={`${selected.length}개 선택 · 한 번에 최대 3개 표시`}
    filter={<ToggleGroup type="single" value={filter} onValueChange={value => value && setFilter(value)}
      aria-label="항목 필터" className="library-filter">
      <ToggleGroupItem value="recommended">이 세션 추천</ToggleGroupItem>
      <ToggleGroupItem value="all">전체 {modules.length}개</ToggleGroupItem>
    </ToggleGroup>}>
    {visible.map(item => {
      const checked = selected.includes(item.id);
      const unavailable = item.pending || (session === 'formation' && !phaseOrder.formation.includes(item.id));
      return <Choice key={item.id} item={item} checked={checked} disabled={Boolean(unavailable && !checked)}
        badge={item.pending ? '준비 중' : undefined} timing={moduleTiming(item.id, session)} onChange={() => onToggle(item.id)}/>;
    })}
  </SelectionPanel>;
}

export function FormationSummary({ config, game }) {
  const selected = formationSelection(config, game);
  return <Panel title="출발 전 텍스트" label="포메이션 표시 안내"
    description="출발 순위와 참가 규모는 고정하고, 준비된 선택 정보만 순환합니다.">
    <div className="phase-plan">
      <div data-active="true"><b>항상 표시<Badge variant="secondary">고정</Badge></b><p>공식 출발 순위 · 참가 규모</p></div>
      {[['출발 준비', false], ['지난번 돌아보기', true]].map(([title, review]) =>
        <div key={title}><b>{title}</b><p>{selected.filter(item => item.review === review).map(item => item.name).join(' · ') || '선택한 항목 없음'}</p></div>)}
    </div>
    <p className="selection-hint">무전 OFF·음소거·브리핑 0개에서도 표시됩니다. 선택 정보는 8초마다 바뀌며, 긴 메모는 원문 그대로 나눠 표시합니다. 그린에 레이스 화면으로 전환합니다.</p>
    <p className="selection-hint">공식 출처가 없으면 출발 순위를 확인 중으로 표시합니다. 규정 입력이 없으면 규정이 없는 것으로 해석하지 않습니다.</p>
  </Panel>;
}
