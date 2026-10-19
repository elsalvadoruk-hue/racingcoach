import React, { useCallback, useEffect, useState } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';

const STATUS = {
  CONFIRMED: '확정됨 · 화면과 음성이 이 번호를 사용합니다.',
  SUGGESTED: '공식 지도에서 가져온 제안입니다. 확인하고 틀린 곳만 고친 뒤 확정하세요.',
  NO_OFFICIAL_MAP: '이 트랙은 공식 지도의 코너 표기가 없습니다. 직접 입력해 확정할 수 있습니다.',
  MAP_FIT_FAILED: '공식 지도를 주행 궤적에 맞추지 못했습니다. 직접 입력해 확정할 수 있습니다.',
  NO_GPS: '이 파일에는 위치 데이터가 없어 제안을 만들 수 없습니다. 직접 입력해 확정할 수 있습니다.',
  NO_VALID_LAP: '유효한 완주 랩이 있어야 코너를 나눌 수 있습니다.',
};
const callOf = (row) => row.spoken?.trim() || (row.number?.trim() ? `${row.number.trim().split('-')[0]}번 코너` : row.name?.trim() || '');
const toRows = (corners, useSuggestion) => corners.map((corner) => ({
  id: corner.id, order: corner.order, apex_pct: corner.apex_pct, labels: corner.labels || [],
  suggested_number: corner.suggested_number || '', suggested_name: corner.suggested_name || '',
  number: corner.number ?? (useSuggestion ? corner.suggested_number || '' : ''),
  name: corner.name ?? (useSuggestion ? corner.suggested_name || '' : ''), spoken: corner.spoken || '',
}));

// Official turn numbers or names for the automatic segments: suggested from iRacing's map, confirmed once per layout.
export default function CornerNames({ apiBase, filename, onSaved }) {
  const [data, setData] = useState(null);
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const apply = useCallback((payload) => { setData(payload); setRows(toRows(payload.corners || [], !payload.confirmed)); }, []);
  useEffect(() => {
    if (!filename) return undefined;
    let stopped = false;
    setData(null); setError(''); setSaved(false);
    fetch(`${apiBase}/api/corner-names?filename=${encodeURIComponent(filename)}`)
      .then(async (response) => { const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || '코너 번호를 불러오지 못했습니다.'); return payload; })
      .then((payload) => { if (!stopped) apply(payload); })
      .catch((failure) => { if (!stopped) setError(failure.message); });
    return () => { stopped = true; };
  }, [apiBase, filename, apply]);
  const change = (id, patch) => { setSaved(false); setRows((current) => current.map((row) => row.id === id ? { ...row, ...patch } : row)); };
  const save = async () => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${apiBase}/api/corner-names`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename, corners: rows.map(({ id, number, name, spoken }) => ({ id, number, name, spoken })) }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '코너 번호를 저장하지 못했습니다.');
      apply(payload); setSaved(true); onSaved?.();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  if (!filename) return null;
  if (!data) return <p role={error ? 'alert' : 'status'} className="text-sm text-muted-foreground">{error || '코너 번호를 확인하고 있습니다…'}</p>;
  const named = data.kind === 'named';
  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-center gap-3">
      <Badge variant={data.confirmed ? 'default' : 'secondary'}>{data.confirmed ? '확정됨' : '확인 필요'}</Badge>
      <p role="status" className="text-sm text-foreground">{STATUS[data.status] || data.status}</p>
    </div>
    {data.fit_median_m != null && <p className="text-xs text-muted-foreground">공식 지도와 주행 궤적의 맞춤 오차 중앙값 {data.fit_median_m}m · {named ? '이 트랙은 코너를 이름으로 부릅니다.' : '번호가 없는 구간은 음성에서 “다음 코너”로만 부릅니다.'}</p>}
    {!!rows.length && <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm">
      <thead className="text-xs text-muted-foreground"><tr><th className="p-2">자동 구간</th><th className="p-2">위치</th><th className="p-2">지도 표기</th><th className="p-2">코너 번호</th><th className="p-2">코너 이름</th><th className="p-2">음성으로 부를 말</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id} className="border-t border-border">
        <td className="p-2 font-mono">{row.order}</td>
        <td className="p-2 font-mono text-muted-foreground">{Math.round(row.apex_pct * 1000) / 10}%</td>
        <td className="p-2 text-muted-foreground">{row.labels.join(', ') || '—'}</td>
        <td className="p-2"><Input aria-label={`자동 구간 ${row.order} 코너 번호`} value={row.number} maxLength={12} onChange={(event) => change(row.id, { number: event.target.value })} className="h-9 w-20" /></td>
        <td className="p-2"><Input aria-label={`자동 구간 ${row.order} 코너 이름`} value={row.name} maxLength={60} onChange={(event) => change(row.id, { name: event.target.value })} className="h-9 w-40" /></td>
        <td className="p-2"><Input aria-label={`자동 구간 ${row.order} 음성으로 부를 말`} value={row.spoken} maxLength={60} placeholder={callOf({ ...row, spoken: '' }) || '다음 코너'} onChange={(event) => change(row.id, { spoken: event.target.value })} className="h-9 w-44" /></td>
      </tr>)}</tbody>
    </table></div>}
    <div className="flex flex-wrap items-center gap-3">
      <Button type="button" variant="outline" disabled={busy || data.suggestion_status !== 'SUGGESTED'} onClick={() => { setSaved(false); setRows(toRows((data.corners || []).map((corner) => ({ ...corner, number: null, name: null })), true)); }}>지도 제안으로 다시 채우기</Button>
      <Button type="button" disabled={busy || !rows.length} onClick={save}>{busy ? '저장 중…' : data.confirmed ? '수정 내용 확정' : '이대로 확정'}</Button>
      {saved && <p role="status" className="text-sm text-success">확정했습니다. 이 트랙의 다른 주행과 실시간 음성에도 적용됩니다.</p>}
    </div>
    {error && <p role="alert" className="text-sm text-warning">{error}</p>}
    <p className="text-xs leading-relaxed text-muted-foreground">자동 구간은 주행 데이터로 나눈 것이라 공식 코너와 일대일로 맞지 않을 수 있습니다. 시케인처럼 공식 코너 두 개가 한 구간이면 “1-2”처럼 적고, 공식 번호가 없는 구간은 비워 두세요. 영문 이름은 “음성으로 부를 말”에 한글로 적으면 그대로 읽습니다(예: 플룩플라츠). 확정한 내용은 트랙 레이아웃별로 저장되어 다른 차량에도 적용됩니다.</p>
  </div>;
}
