import React, { useId } from 'react';
import { Field, FieldLabel } from './ui/field';
import { ToggleGroup, ToggleGroupItem } from './ui/toggle-group';
import { canShorten, lengthPatch, voiceLength } from '../lib/voiceLength';

export default function VoiceLengthSelect({ scope, settings, library, onChange, disabled = false }) {
  const id = useId(), label = `${scope === 'spotter' ? '스포터' : '피트월'} 음성 길이`;
  const available = canShorten(scope, settings, library);
  const locked = disabled || !library?.view || library?.operation?.state === 'RUNNING';
  return <Field data-disabled={locked || undefined}>
    <FieldLabel id={id}>{label}</FieldLabel>
    <ToggleGroup type="single" variant="outline" size="sm" className="justify-start" aria-labelledby={id}
      value={voiceLength(scope, settings, library)} disabled={locked} onValueChange={value => {
        const next = lengthPatch(scope, value, settings, library);
        if (next) onChange(next);
      }}>
      <ToggleGroupItem value="default">기본</ToggleGroupItem>
      <ToggleGroupItem value="short" disabled={locked || !available}>짧은 무전</ToggleGroupItem>
    </ToggleGroup>
  </Field>;
}
