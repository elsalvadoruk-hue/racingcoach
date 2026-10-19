import React, { useEffect, useState } from 'react';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from './ui/table';
import { Alert, AlertDescription } from './ui/alert';

const OUTCOME = { INPUT_ACHIEVED: '입력 목표 달성 · 페이스는 다음 주행에서 확인',
  STOPPED: '연습 종료', RESTARTED: '다시 시작', SESSION_CHANGED: '세션 변경으로 종료',
  IDENTITY_CHANGED: '차량·트랙 변경으로 종료', DISCONNECTED: '연결 종료',
  INTERRUPTED: '앱 종료로 중단', IN_PROGRESS: '진행 중' };

export default function PracticeDrillHistory({ apiBase, taskId, revision = 0 }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open || !taskId) return undefined;
    let alive = true;
    Promise.resolve().then(() => fetch(`${apiBase}/api/practice/${taskId}/drills`))
      .then(async (response) => {
        if (!response.ok) throw new Error('반복 연습 기록을 읽지 못했습니다.');
        return response.json();
      }).then((value) => { if (alive) { setData(value); setError(''); } })
      .catch((failure) => { if (alive) setError(failure.message); });
    return () => { alive = false; };
  }, [apiBase, taskId, open, revision]);
  return (
    <details className="rounded-lg border border-border p-3" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-sm font-semibold">저장된 반복 연습 기록</summary>
      {open && <div className="mt-2 flex flex-col gap-3 text-sm">
        {(error || data?.error) && <Alert variant="destructive"><AlertDescription>{error || data.error}</AlertDescription></Alert>}
        {data && !data.runs?.length && <p className="text-muted-foreground">아직 저장된 반복 연습이 없습니다. 이전 버전의 시도는 소급해서 만들지 않습니다.</p>}
        {[...(data?.runs || [])].reverse().map((run) => (
          <div key={run.id} className="flex flex-col gap-1">
            <p>{run.plan?.label} · {new Date(run.at * 1000).toLocaleString('ko-KR')} · {run.attempt_count}회</p>
            <p className="text-xs text-muted-foreground">{OUTCOME[run.outcome] || '종료'} · 아래에는 최근 30회가 표시됩니다.</p>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader><TableRow><TableHead>시도 / 묶음</TableHead><TableHead>시작 위치</TableHead><TableHead>시간</TableHead><TableHead>입력 변화</TableHead><TableHead>입력 / 부가 조건</TableHead></TableRow></TableHeader>
                <TableBody>{(run.attempts || []).map((a) => (
                  <TableRow key={a.n}><TableCell>{a.n} / {a.series || '—'}</TableCell><TableCell>{a.origin_m == null ? '저장 전 진입' : `${a.origin_m.toFixed(0)} m`}</TableCell>
                    <TableCell>{a.time_s.toFixed(3)} s</TableCell>
                    <TableCell>{Number.isFinite(a.input_change) ? `${a.input_change > 0 ? '+' : ''}${a.input_change.toFixed(2)} ${run.plan.target?.unit || ''}` : '측정 불가'}</TableCell>
                    <TableCell>{a.goal_ok === true ? '달성' : a.goal_ok === false ? '미달' : '확인 필요'}
                      {(a.criteria || []).filter((c) => c.ok !== true).map((c) => <p key={c.metric}>{c.label}: {c.ok === false ? '미달' : '측정 불가'}</p>)}
                    </TableCell></TableRow>
                ))}</TableBody>
              </Table>
            </div>
          </div>
        ))}
      </div>}
    </details>
  );
}
