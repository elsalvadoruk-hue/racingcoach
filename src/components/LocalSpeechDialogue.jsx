import React from 'react';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { Button } from './ui/button';

export default function LocalSpeechDialogue({ radio, compact = false }) {
  if (!radio?.dialogue) return radio?.dialogueNotice ? <p role="status">{radio.dialogueNotice}</p> : null;
  const seconds = Math.ceil(radio.dialogue.remaining_ms / 1000);
  return <Alert role="status" aria-label="음성 질문 확인 대기">
    <AlertTitle>{radio.dialogue.prompt}</AlertTitle>
    <AlertDescription>
      <p>{seconds}초 안에 질문 단축키를 누르고 답해 주세요. 응답이 없으면 자동으로 취소됩니다.</p>
      {!compact && <p>“응”, “맞아”, “아니, 뒤차 간격”처럼 답할 수 있습니다. 마이크는 단축키나 녹음 버튼을 눌렀을 때만 켜집니다.</p>}
      <Button variant="outline" size="sm" onClick={radio.cancelDialogue}>확인 대기 취소</Button>
    </AlertDescription>
  </Alert>;
}
