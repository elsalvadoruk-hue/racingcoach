import React, { useId } from 'react';
import { Field, FieldLabel, FieldDescription } from './ui/field';
import { NativeSelect } from './ui/native-select';
import { VOICE_ROLES, voiceSelections } from '../lib/voiceLibrary';

export default function RoleVoiceSelect({ role, library, settings, onChange, disabled, defaults }) {
  const id = useId(), choices = voiceSelections(settings), current = choices[role];
  const packs = library?.view?.packs?.filter(p => p.roles?.[role] > 0) || [];
  return <Field data-disabled={disabled || undefined}>
    <FieldLabel htmlFor={id}>{VOICE_ROLES[role]} 목소리</FieldLabel>
    <NativeSelect id={id} aria-label={`${VOICE_ROLES[role]} 목소리`} disabled={disabled} value={current ? `pack:${current}` : settings?.voice && role === 'spotter' ? settings.voice : ''} onChange={e => {
      const voice_packs = { ...choices }, custom = e.target.value.startsWith('pack:');
      if (custom) voice_packs[role] = e.target.value.slice(5); else delete voice_packs[role];
      onChange({ voice_packs, custom_pack: voice_packs.spotter || null, ...(!custom && role === 'spotter' ? { voice: e.target.value } : {}) });
    }}>
      {defaults.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      {current && !packs.some(p => p.key === current) && <option value={`pack:${current}`} disabled>찾을 수 없는 팩 · 기본 목소리를 선택하세요</option>}
      {packs.map(p => <option key={p.key} value={`pack:${p.key}`}>{p.name} · {p.version} · 커스텀</option>)}
    </NativeSelect>
    {current && <FieldDescription>이 역할만 적용합니다. 없는 문장은 준비된 기본 음성으로 보완합니다.</FieldDescription>}
  </Field>;
}
