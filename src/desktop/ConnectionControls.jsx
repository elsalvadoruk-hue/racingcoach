import React from 'react';
import { Button } from '../components/ui/button';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { NativeSelect } from '../components/ui/native-select';
import { SESSION_MODES } from '../analysis/analysisShared';

export default function ConnectionControls({ workspace: w }) {
  return <details className="desktop-connection-controls"><summary>현재 주행 분류 · 분석 기록 저장</summary>
    <p>현재 분석 중인 파일: {w.selectedFile || '선택 전'}</p>
    <FieldGroup><Field><FieldLabel htmlFor="desktop-session-mode">주행 분류</FieldLabel>
      <NativeSelect id="desktop-session-mode" value={w.sessionMode} disabled={w.loading || !w.analysis}
        onChange={event => w.handleSessionModeChange(event.target.value)}>
        {SESSION_MODES.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
      </NativeSelect></Field>
      {w.sessionMode === 'ENDURANCE' && <Field><FieldLabel htmlFor="desktop-pit-rule">피트 작업 규정</FieldLabel>
        <NativeSelect id="desktop-pit-rule" value={w.pitRule} onChange={event => w.setPitRule(event.target.value)}>
          <option value="CONCURRENT">동시 교체</option><option value="SEQUENTIAL">순차 교체</option>
        </NativeSelect></Field>}
    </FieldGroup>
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={w.loading || !w.selectedFile}
      onClick={() => w.runAnalysis({ persistMemory: true })}>이 분류로 분석 기록 저장</Button>
      <Button variant="outline" disabled={w.loading || !w.selectedFile} onClick={() => w.runAnalysis({ persistMemory: false })}>분석 갱신</Button></div>
    {w.error && <p role="alert">{w.error}</p>}
  </details>;
}
