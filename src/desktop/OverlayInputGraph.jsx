import React from 'react';
import { Field, FieldGroup, FieldLabel, FieldDescription } from '../components/ui/field';
import { Switch } from '../components/ui/switch';
import { NativeSelect, NativeSelectOption } from '../components/ui/native-select';

const CHANNELS = [['throttle', '스로틀', '가속 입력', 'throttle-line'], ['brake', '브레이크', '제동 입력', 'brake-line'], ['steering', '조향', '조향 크기 · 절댓값', 'steering-line']];

export function GraphControls({ config, onChange }) {
  const set = values => onChange({ graph: {...config.graph,...values} });
  return <FieldGroup className="graph-control-fields">
    {CHANNELS.map(([key,label,help,cls]) => <Field orientation="horizontal" key={key}><div><FieldLabel htmlFor={`graph-${key}`}><i className={`channel-dot ${cls}`}/>{label}</FieldLabel><FieldDescription>{help}</FieldDescription></div><Switch id={`graph-${key}`} checked={config.graph[key]} onCheckedChange={v=>set({[key]:v})}/></Field>)}
    <Field><FieldLabel htmlFor="graph-window">입력값 표시 범위</FieldLabel><NativeSelect id="graph-window" value={config.graph.window} onChange={e=>set({window:Number(e.target.value)})}>{[6,10,16].map(value=><NativeSelectOption value={value} key={value}>최근 {value}초</NativeSelectOption>)}</NativeSelect></Field>
    <Field orientation="horizontal"><FieldLabel htmlFor="graph-future">기준의 다음 2초</FieldLabel><Switch id="graph-future" checked={config.graph.future} onCheckedChange={v=>set({future:v})}/></Field>
    <FieldDescription>기준이 준비된 경우 같은 트랙 위치에 맞춰 보여주는 예시입니다.</FieldDescription>
  </FieldGroup>;
}

export function GraphExtras({ config, onChange }) {
  const set = values => onChange({ graph: {...config.graph,...values} });
  return <FieldGroup className="graph-control-fields">
    <Field orientation="horizontal"><FieldLabel htmlFor="graph-gear">현재·기준 기어</FieldLabel><Switch id="graph-gear" checked={config.graph.gear} onCheckedChange={v=>set({gear:v})}/></Field>
    <Field orientation="horizontal"><FieldLabel htmlFor="graph-feedback">직전 코너 비교</FieldLabel><Switch id="graph-feedback" checked={config.graph.feedback} onCheckedChange={v=>set({feedback:v})}/></Field>
    <FieldDescription>직전 코너 시간 차이와 제동 시작 위치 차이를 그래프 아래에 짧게 표시합니다.</FieldDescription>
  </FieldGroup>;
}
