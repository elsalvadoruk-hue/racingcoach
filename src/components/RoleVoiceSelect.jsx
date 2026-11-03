import React, { useId } from 'react';
import { Field, FieldLabel, FieldDescription } from './ui/field';
import { NativeSelect } from './ui/native-select';
import { VOICE_ROLES, voiceSelections } from '../lib/voiceLibrary';
import { baseVoice, matchingShortPack, shortPack, voiceLength, voicePatch, voiceScope } from '../lib/voiceLength';

export default function RoleVoiceSelect({ role, library, settings, onChange, disabled, defaults }) {
  const id = useId(), choices = voiceSelections(settings), current = choices[role];
  const allPacks = library?.view?.packs || [];
  const selected = allPacks.find(p => p.key === current);
  const packs = allPacks.filter(p => p.roles?.[role] > 0 && !shortPack(p));
  const length = voiceLength(voiceScope(role), settings, library);
  const value = shortPack(selected) ? role === 'spotter' ? baseVoice(role, settings, library) : ''
    : current ? `pack:${current}` : settings?.voice && role === 'spotter' ? settings.voice : '';
  disabled = disabled || library?.operation?.state === 'RUNNING';
  return <Field data-disabled={disabled || undefined}>
    <FieldLabel htmlFor={id}>{VOICE_ROLES[role]} 목소리</FieldLabel>
    <NativeSelect id={id} aria-label={`${VOICE_ROLES[role]} 목소리`} disabled={disabled} value={value} onChange={e => {
      const next = voicePatch(role, e.target.value, settings, library);
      if (next) onChange(next);
    }}>
      {defaults.map(([value, label]) => <option key={value} value={value} disabled={length === 'short' && !matchingShortPack(role, role === 'spotter' ? value : baseVoice(role, settings, library), library)}>{label}</option>)}
      {current && !selected && <option value={`pack:${current}`} disabled>찾을 수 없는 팩 · 기본 목소리를 선택하세요</option>}
      {packs.map(p => <option key={p.key} value={`pack:${p.key}`}>{p.name} · {p.version} · 커스텀</option>)}
    </NativeSelect>
    {current && !shortPack(selected) && <FieldDescription>이 역할만 적용합니다. 없는 문장은 준비된 기본 음성으로 보완합니다.</FieldDescription>}
    {library?.operation?.kind === 'selection' && library.operation.state === 'RUNNING' && <FieldDescription role="status">목소리 적용 중… 음성 파일 확인이 끝나면 선택할 수 있습니다.</FieldDescription>}
  </Field>;
}
