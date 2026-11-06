import React, { useEffect, useRef, useState } from 'react';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog';

const POLL_MS = 15000;

// iRacing tire numbers are read with Windows OCR. Without the English OCR component,
// some dashboard digits are read as letters and the reading is withheld. An in-app
// update does not run 설치.bat's OCR step, so the app asks here once the tire
// overlay or voice is on and the overlay reports that English OCR is missing.
export default function TireOcrPrompt({ apiBase, fetcher = fetch }) {
  const [view, setView] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const url = path => `${apiBase}/api/tire-overlay${path}?game=iracing`;

  useEffect(() => {
    alive.current = true;
    let timer, controller;
    const refresh = async () => {
      controller = new AbortController();
      try {
        const response = await fetcher(url(''), { signal: controller.signal });
        const data = await response.json();
        if (alive.current && response.ok && data?.version === 1) setView(data);
      } catch { /* the prompt never gets in the way when the tire service is unavailable */ }
      if (alive.current) timer = setTimeout(refresh, POLL_MS);
    };
    refresh();
    return () => { alive.current = false; clearTimeout(timer); controller?.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, fetcher]);

  const post = async path => {
    setBusy(true); setError('');
    try {
      const response = await fetcher(url(path), { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.detail || '요청을 처리하지 못했습니다.');
      if (alive.current) setView(data);
    } catch (failure) {
      if (alive.current) setError(failure.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const wanted = view?.running && (view.enabled || view.voice_enabled) && view.ocr_english === false;
  const requested = view?.ocr_install === 'requested';
  return <Dialog open={Boolean(wanted && !dismissed)} onOpenChange={open => { if (!open) setDismissed(true); }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>영어 Windows OCR을 설치해 주세요</DialogTitle>
        <DialogDescription>
          iRacing 타이어 숫자를 제대로 읽으려면 영어 Windows OCR이 필요합니다. 지금은 {view?.ocr_language ? `현재 언어(${view.ocr_language})로 ` : ''}읽고 있어
          일부 계기판 숫자를 글자로 잘못 읽고 측정이 보류될 수 있습니다.
        </DialogDescription>
      </DialogHeader>
      <p className="text-sm text-muted-foreground">
        OCR 구성요소만 추가하며 Windows 표시 언어는 바뀌지 않습니다. 설치에는 관리자 승인과 인터넷이 필요하고 몇 분 걸릴 수 있습니다.
        {requested ? ' 설치 창이 끝나면 다시 확인을 눌러 주세요.' : ''}
      </p>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <DialogFooter>
        <Button variant="outline" onClick={() => setDismissed(true)}>나중에</Button>
        {requested
          ? <Button disabled={busy} onClick={() => post('/ocr-language/recheck')}>다시 확인</Button>
          : <Button disabled={busy} onClick={() => post('/ocr-language/install')}>영어 OCR 설치 (관리자 승인)</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
