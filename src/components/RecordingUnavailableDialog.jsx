import React from 'react';
import { Button } from './ui/button';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog';

export default function RecordingUnavailableDialog({ recording, onClose, returnFocusRef }) {
  const reason = recording?.eligibility_reason || recording?.reason ||
    ([recording?.detected_laps, recording?.lap_count].some(count => count === 0)
      ? '기록된 랩이 없어 비교할 수 없습니다.'
      : recording?.complete_laps === 0 ? '완주한 랩이 없어 비교할 수 없습니다.'
        : '유효 랩 또는 측정 가능한 완주 랩이 없어 비교할 수 없습니다.');
  return <Dialog open={Boolean(recording)} onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent onCloseAutoFocus={event => {
      if (returnFocusRef?.current) { event.preventDefault(); returnFocusRef.current.focus(); }
    }}>
      <DialogHeader>
        <DialogTitle>이 주행 결과는 선택할 수 없습니다</DialogTitle>
        <DialogDescription>{reason} 다른 주행 파일을 선택해 주세요.</DialogDescription>
      </DialogHeader>
      <p className="break-all text-sm text-muted-foreground">{recording?.display_name || recording?.file_name || recording?.name}</p>
      <DialogFooter><DialogClose asChild><Button>확인</Button></DialogClose></DialogFooter>
    </DialogContent>
  </Dialog>;
}
