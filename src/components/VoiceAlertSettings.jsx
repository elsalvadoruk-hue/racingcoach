import React, { memo, useState } from 'react';
import { ChevronDown, RotateCcw, Volume2 } from 'lucide-react';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from './ui/field';
import { Switch } from './ui/switch';
import { alertGroups, defaultAlerts } from '../lib/voiceAlerts';
import './voiceAlerts.css';

const AlertRow = memo(function AlertRow({ scope, item, selected, disabled, onChange }) {
  const [example, setExample] = useState(false);
  const id = `voice-alert-${scope}-${item.id}`;
  return <div className="voice-alert-row" data-enabled={selected}>
    <Field orientation="horizontal" data-disabled={disabled || undefined}>
      <FieldContent><FieldLabel htmlFor={id}>{item.label}</FieldLabel><FieldDescription id={`${id}-help`}>{item.description}</FieldDescription></FieldContent>
      <div className="voice-alert-actions"><Button variant="ghost" size="sm" aria-label={`${item.label} 예시`} aria-expanded={example} aria-controls={`${id}-example`} onClick={() => setExample(v => !v)}>예시</Button><span aria-hidden="true">{selected ? '켬' : '끔'}</span><Switch id={id} checked={selected} disabled={disabled} aria-describedby={`${id}-help`} onCheckedChange={value => onChange(item.id, value)} /></div>
    </Field>
    {example && <div className="voice-alert-example" id={`${id}-example`}><Volume2 aria-hidden="true" /><p>“{item.example}”<small>상황을 설명하기 위한 문구 예시</small></p></div>}
  </div>;
});

export default function VoiceAlertSettings({ scope, game = 'iracing', alerts, onChange, disabled = false }) {
  const groups = alertGroups(scope, game), defaults = defaultAlerts(scope, game);
  const selected = { ...defaults, ...alerts };
  const name = scope === 'spotter' ? '스포터' : '피트월';
  const items = groups.flatMap(g => g.items);
  const update = (id, value) => onChange({ ...selected, [id]: value,
    ...(scope === 'spotter' && id === 'overlap' && !value ? { clear: false, repeat: false } : {}) });
  return <section className="voice-alert-settings" aria-label={`${name} 상황별 음성 안내`}>
    <div className="voice-alert-heading"><div><h3>상황별 음성 안내</h3><p>듣고 싶은 안내만 켜두세요.</p></div><Badge variant="secondary" aria-live="polite">{items.filter(i => selected[i.id]).length} / {items.length} 켬</Badge></div>
    <div className="voice-alert-bulk" aria-label={`${name} 일괄 설정`}><Button variant="outline" size="sm" disabled={disabled} onClick={() => onChange(Object.fromEntries(items.map(i => [i.id, true])))}>전체 켜기</Button><Button variant="outline" size="sm" disabled={disabled} onClick={() => onChange(Object.fromEntries(items.map(i => [i.id, false])))}>전체 끄기</Button><Button variant="ghost" size="sm" disabled={disabled} onClick={() => onChange(defaults)}><RotateCcw data-icon="inline-start" />기본값으로</Button></div>
    <div className="voice-alert-groups">{groups.map((group, index) => <details key={group.id} className="voice-alert-group" open={index === 0}>
      <summary><span>{group.title}</span><small>{group.items.filter(i => selected[i.id]).length} / {group.items.length} 켬</small><ChevronDown aria-hidden="true" /></summary>
      <FieldSet><FieldLegend className="sr-only">{group.title}</FieldLegend><FieldGroup className="voice-alert-fields">{group.items.map(item => <AlertRow key={item.id} scope={scope} item={item} selected={selected[item.id]}
        disabled={disabled || (scope === 'spotter' && ['clear', 'repeat'].includes(item.id) && selected.overlap === false)} onChange={update} />)}</FieldGroup></FieldSet>
    </details>)}</div>
    <p className="voice-alert-note">{scope === 'spotter' ? '깃발 안내도 종류별로 선택합니다. 클리어·반복은 좌우 차량 안내를 켰을 때 사용합니다.' : '차량·주행 근거와 게임 지원 조건을 확인한 안내만 나옵니다. 스위치를 켜도 미지원 기능이 활성화되지는 않습니다.'}</p>
  </section>;
}
