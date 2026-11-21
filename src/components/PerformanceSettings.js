import React, { useEffect, useState } from 'react';
import { Field, FieldDescription, FieldLabel } from './ui/field';
import { Switch } from './ui/switch';
import { manualOnOpen, saveManualOnOpen } from './manualLoading';

export default function PerformanceSettings({ apiBase }) {
  const [memorySaver, setMemorySaver] = useState(null);
  const [manualDeferred, setManualDeferred] = useState(manualOnOpen);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const url = `${apiBase}/api/settings/performance`;

  useEffect(() => {
    const controller = new AbortController();
    async function read() {
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error();
        const value = await response.json();
        if (!controller.signal.aborted) setMemorySaver(value.recording_memory_saver !== false);
      } catch {
        if (!controller.signal.aborted) setError('녹화 메모리 설정을 불러오지 못했습니다.');
      }
    }
    read();
    return () => controller.abort();
  }, [url]);

  async function changeMemorySaver(enabled) {
    setBusy(true);
    setError('');
    try {
      const response = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recording_memory_saver: enabled }) });
      if (!response.ok) throw new Error();
      const value = await response.json();
      setMemorySaver(value.recording_memory_saver !== false);
    } catch {
      setError('녹화 메모리 설정을 저장하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  function changeManualLoading(enabled) {
    try {
      saveManualOnOpen(enabled);
      setManualDeferred(enabled);
      setError('');
    } catch {
      setError('매뉴얼 설정을 저장하지 못했습니다.');
    }
  }

  return <div className="flex flex-col gap-4">
    <Field>
      <div className="flex items-center justify-between gap-3">
        <FieldLabel htmlFor="recording-memory-saver">대형 녹화 메모리 절약</FieldLabel>
        <Switch id="recording-memory-saver" checked={memorySaver !== false} disabled={memorySaver === null || busy}
          onCheckedChange={changeMemorySaver} aria-describedby="recording-memory-saver-description" />
      </div>
      <FieldDescription id="recording-memory-saver-description">사용하지 않는 큰 IBT 녹화를 메모리에서 비웁니다. 여러 녹화를 자주 오가면 끄고 최근 3개를 유지할 수 있습니다.</FieldDescription>
    </Field>
    <Field>
      <div className="flex items-center justify-between gap-3">
        <FieldLabel htmlFor="manual-on-open">매뉴얼 열 때 불러오기</FieldLabel>
        <Switch id="manual-on-open" checked={manualDeferred} onCheckedChange={changeManualLoading}
          aria-describedby="manual-on-open-description" />
      </div>
      <FieldDescription id="manual-on-open-description">가이드를 처음 열 때 불러옵니다. 끄면 다음 앱 실행 때 선택한 게임의 가이드를 미리 불러옵니다.</FieldDescription>
    </Field>
    {error && <p role="alert" className="text-sm text-warning">{error}</p>}
  </div>;
}
