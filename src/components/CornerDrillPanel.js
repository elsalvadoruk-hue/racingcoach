import React, { useEffect, useState } from 'react';
import { Button } from './ui/button';
import { playBeep } from '../drillBeeps';
import PracticeDrillHistory from './PracticeDrillHistory';

// Corner drill with iRacing's Active Reset, for a corner of the running practice task (live_drill.py).
// The driver presses Active Reset's keys himself, where it is safe; this panel starts the measuring,
// shows the plan and every attempt, and says when the corner is repeated well enough.
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const metres = (pct, length) => (finite(pct) && length ? `${Math.round(pct * length).toLocaleString()} m` : '--');
const signed = (value, digits = 2) => (finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}` : '--');

const SOUNDS = [
  ['save_hint', '낮고 긴 “삐—”', '저장 권장 위치. 근처 직선의 안전한 곳에서 Active Reset “저장” 키를 누르세요.'],
  ['brake_beep', '“삐, 삐, 삑”', '기준 랩의 제동 지점까지 1초·0.5초 전, 마지막 높은 “삑”이 제동 지점입니다.'],
  ['throttle_beep', '짧은 “뾱”', '기준 랩이 풀스로틀로 돌아간 지점입니다.'],
  ['end_beep', '낮은 “삐삐”', '구간 끝. Active Reset “실행” 키를 누르면 저장한 곳으로 돌아갑니다.'],
  ['done', '올라가는 “띠리링”', '입력과 부가 조건을 반복해서 지켰습니다. 페이스 개선은 다음 정상 주행에서 확인합니다.'],
];

export function DrillGuide() {
  return (
    <details className="rounded-lg border border-border p-3 text-sm" data-testid="drill-guide">
      <summary className="cursor-pointer font-semibold text-foreground">구간 반복 연습 사용법 (처음이면 꼭 읽으세요)</summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-muted-foreground">
        <li>iRacing <b>설정 → 키 설정(Keybinds) → Advanced Analysis</b>에서 Active Reset의 <b>저장</b>(시작점 스냅샷 저장)과 <b>실행</b>(저장한 곳으로 돌아가기) 키를 운전대 버튼처럼 주행 중 누르기 쉬운 곳에 지정합니다.</li>
        <li>Active Reset은 <b>혼자 하는 오프라인 연습(테스트 주행)</b>에서만 됩니다. 다른 사람이 있는 세션, 퀄리파잉, 레이스에서는 키를 눌러도 아무 일도 일어나지 않습니다. 과제와 같은 차·트랙·레이아웃으로 들어가세요.</li>
        <li>이 앱 <b>음성 코치</b> 탭에서 <b>스포터를 켭니다</b>. 비프는 스포터 연결로 전달됩니다. 무전 출력장치와 음량을 그대로 따릅니다.</li>
        <li>차고에서 <b>연료</b>를 패널에 나온 추천량만큼 넣습니다. 실행 키로 돌아갈 때마다 저장한 순간의 연료로 돌아가므로, 저장할 때 연료가 과제를 정한 랩과 비슷해야 같은 무게로 연습합니다(그 랩의 시작 연료 + 워밍업 바퀴 사용량).</li>
        <li>여기서 <b>구간 연습 시작</b>을 누르고 트랙에 나갑니다.</li>
        <li><b>처음에는 타이어를 데웁니다</b>(약 2분이면 데워집니다). 랩타임 <b>1분 20초 미만</b> 코스는 피트에서 나온 바퀴를 포함해 <b>출발선을 두 번</b> 지날 때까지, 그보다 긴 코스는 <b>피트에서 나와 첫 바퀴가 끝날 때</b>까지입니다. 주행 코치가 목소리로 알려 주고, 이 동안은 저장하지 않고 측정도 하지 않습니다. 이미 타이어가 따뜻하면 “워밍업 건너뛰기”를 누르세요.</li>
        <li>“타이어 준비됐습니다”가 나온 바퀴에서, 제동 구간 앞 직선에서 낮고 긴 “삐—”가 나면 <b>그 근처 직선에서 차가 똑바로 안정적으로 달릴 때</b> 저장 키를 누르세요. 비프는 권장 위치일 뿐이고, 누르는 곳은 직접 고릅니다. 저장할 때의 속도·연료·타이어 상태가 함께 저장되어 매번 같은 조건으로 다시 출발합니다.</li>
        <li>코너를 지나 낮은 “삐삐”가 나면 <b>실행</b> 키를 누릅니다. 저장한 곳으로 바로 돌아가 다시 달립니다. 앱은 차가 뒤로 순간이동한 것을 보고 새 시도로 셉니다.</li>
        <li>제동 지점 앞에서 “삐, 삐, 삑”이 납니다. <b>마지막 높은 “삑”이 기준 랩의 제동 지점</b>입니다. 정점 뒤 짧은 “뾱”은 기준 랩이 풀스로틀로 돌아간 지점입니다. Bloops처럼 소리에 맞춰 입력을 맞춰 보세요.</li>
        <li>같은 저장 지점에서 출발한 <b>최근 5번 중 4번</b>이 입력 목표와 속도·조향 등 부가 조건을 함께 지키고, 그 시도들의 <b>제동 시작이 6m 안</b>, <b>구간 시간이 0.2초 안</b>에서 반복되면 입력 목표 달성입니다(검증 전 초기 기준). 측정값이 없으면 달성으로 세지 않습니다.</li>
        <li>복귀 위치나 출발 속도·연료·설정이 달라지면 새 묶음으로 셉니다. 피트로드·리플레이·차량 밖·주변 차량 겹침·인시던트 증가·데이터가 끊긴 시도는 세지 않습니다.</li>
        <li>목표를 채우면 “띠리링”과 함께 주행 코치가 “구간 연습 목표를 채웠습니다”라고 말합니다.</li>
        <li>시도별 측정값은 자동 저장됩니다. 연습을 마치면 일반 주행에서 유효 랩을 3개 이상 달리고 파일을 분석하세요. 연습 종료와 페이스 개선 확인은 따로 기록합니다.</li>
      </ol>
      <div className="mt-3 grid gap-1">
        <span className="text-xs font-semibold text-muted-foreground">소리 미리 듣기</span>
        {SOUNDS.map(([kind, name, meaning]) => (
          <div key={kind} className="flex flex-wrap items-center gap-2 text-xs">
            <Button variant="outline" type="button" onClick={() => playBeep(kind === 'brake_beep' ? 'brake_point' : kind)} className="border px-2 py-1 text-xs">{name}</Button>
            <span className="text-muted-foreground">{meaning}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

export default function CornerDrillPanel({ apiBase, filename, task, corners = [] }) {
  const labels = (task?.corners || []).map((corner) => corner.label);
  const [label, setLabel] = useState(labels[0] || '');
  const [drill, setDrill] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setLabel(labels[0] || ''); }, [task?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const mine = drill?.active && drill.plan?.task_id === task?.id;
  useEffect(() => {
    let live = true;
    const read = () => {
      if (typeof fetch !== 'function') return;
      Promise.resolve().then(() => fetch(`${apiBase}/api/drill`)).then((response) => response.json())
        .then((data) => { if (live) setDrill(data); }).catch(() => {});
    };
    read();
    const timer = setInterval(read, 1000);
    return () => { live = false; clearInterval(timer); };
  }, [apiBase]);

  const call = async (method, path, body) => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${apiBase}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '요청을 처리하지 못했습니다.');
      setDrill(data);
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  const start = () => call('POST', `/api/practice/${task.id}/drill`, { filename, corner_label: label });
  const stop = () => call('DELETE', '/api/drill');
  const skipWarmup = () => call('POST', '/api/drill/skip-warmup');

  const plan = mine ? drill.plan : null;
  const length = plan?.track_length_m;
  const attempts = mine ? [...(drill.attempts || [])].reverse() : [];
  const target = drill?.target;
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-3" data-testid="corner-drill">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-foreground">구간 반복 연습 · iRacing Active Reset</h4>
        {mine && drill.done && <span className="rounded-lg border border-success/40 bg-success/10 px-2 py-1 text-xs font-semibold text-success">입력 목표 달성</span>}
      </div>
      <p className="text-xs text-muted-foreground">
        이 코너만 오프라인 테스트 주행에서 반복합니다. 저장·실행 키는 직접 누르고, 앱은 소리로 위치를 알려 주고 시도마다 측정합니다.
      </p>
      {!mine ? (
        <div className="flex flex-wrap items-center gap-2">
          {labels.length > 1 && (
            <select value={label} onChange={(event) => setLabel(event.target.value)} aria-label="연습할 코너" className="rounded-lg border border-border bg-background px-2 py-1.5 text-sm">
              {labels.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          )}
          <Button variant="default" type="button" disabled={busy || !label || !filename} onClick={start} className="px-3 py-2 text-sm">구간 연습 시작{labels.length === 1 ? ` · ${label}` : ''}</Button>
          {drill?.active && <span className="text-xs text-warning">다른 과제의 구간 연습이 켜져 있습니다. 시작하면 그것을 대신합니다.</span>}
        </div>
      ) : (
        <>
          <div className="grid gap-2 text-sm sm:grid-cols-2">
            <div><span className="text-muted-foreground">코너</span> <b>{plan.label}</b> · 구간 약 {plan.section_m} m</div>
            <div><span className="text-muted-foreground">저장 권장 위치</span> 랩 {metres(plan.start_pct, length)} 지점 (제동 지점 약 {Math.round(((plan.brake_pct - plan.start_pct + 1) % 1) * length)} m 전)</div>
            <div><span className="text-muted-foreground">기준 제동 지점</span> 랩 {metres(plan.brake_pct, length)}</div>
            {plan.reference_label && <div className="sm:col-span-2" data-testid="drill-reference"><span className="text-muted-foreground">비프 기준</span> {plan.reference_label}{plan.reference_source === 'GARAGE61' ? '의 제동·풀스로틀 지점 (빠른 드라이버 기준)' : '의 제동·풀스로틀 지점'}</div>}
            <div><span className="text-muted-foreground">구간 끝 (실행 키)</span> 랩 {metres(plan.end_pct, length)}</div>
            {plan.fuel && (
              <div className="sm:col-span-2" data-testid="drill-fuel"><span className="text-muted-foreground">넣을 연료</span> <b>{plan.fuel.suggest_l} L</b>
                <span className="text-xs text-muted-foreground"> · 과제 랩({plan.fuel.lap}랩) 시작 {plan.fuel.lap_start_l} L + 워밍업 {plan.fuel.warm_laps ?? 2}바퀴 × {plan.fuel.per_lap_l} L</span></div>
            )}
          </div>
          {drill.phase === 'WARMUP' && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 p-2 text-sm" data-testid="drill-warmup">
              <span>타이어 워밍업 {drill.warm_laps}/{drill.warmup_laps}바퀴 · {drill.warmup_laps > 1 ? '랩이 짧은 코스라 피트에서 나온 바퀴를 포함해 출발선을 두 번 지나면' : '피트에서 나와 첫 바퀴가 끝나면'} 저장 안내를 시작합니다.</span>
              <Button variant="outline" type="button" disabled={busy} onClick={skipWarmup} className="border px-2 py-1 text-xs">워밍업 건너뛰기 (이미 따뜻함)</Button>
            </div>
          )}
          <p className="text-sm font-semibold text-foreground" data-testid="drill-progress">
            {drill.phase === 'WARMUP' ? '워밍업 중에는 저장하지 않습니다.'
              : !drill.saved ? '아직 시작점이 저장되지 않았습니다. 낮고 긴 “삐—” 근처 직선에서 저장 키를 누르세요.'
              : drill.done ? '같은 저장 지점에서 입력과 부가 조건을 반복해서 지켰습니다. 페이스 개선은 다음 정상 주행에서 확인합니다.'
                : !target ? '이 과제는 실시간 목표를 측정할 수 없습니다. 자동으로 달성 처리하지 않습니다.'
                : `같은 저장 지점 최근 ${drill.recent_count}번 중 목표 달성 ${drill.recent_ok}번 (${drill.needed.recent}번 중 ${drill.needed.ok}번과 반복 편차 조건을 함께 확인)`}
          </p>
          {target && <p className="text-xs text-muted-foreground">목표: {target.label} {target.sign < 0 ? '−' : '+'}{target.goal} {target.unit} (과제를 정한 주행 대비). 부가 조건이나 채널이 없으면 달성으로 세지 않습니다.</p>}
          {(attempts[0]?.criteria || []).length > 0 && <ul className="flex flex-col gap-1 text-xs text-muted-foreground">
            {attempts[0].criteria.map((c) => <li key={c.metric}>{c.label}: {c.ok === true ? '충족' : c.ok === false ? '미달' : '측정 불가'}</li>)}
          </ul>}
          {drill.history_error && <p role="alert" className="text-sm text-destructive">{drill.history_error}</p>}
          {attempts.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] font-mono text-xs">
                <thead><tr className="text-muted-foreground">
                  <th className="text-left font-normal">시도</th><th className="text-right font-normal">구간 시간</th><th className="text-right font-normal">처음 3번 대비</th>
                  <th className="text-right font-normal">제동 시작</th><th className="text-right font-normal">최저 속도</th><th className="text-right font-normal">풀스로틀</th>
                  {target && <th className="text-right font-normal">{target.label} 변화</th>}
                </tr></thead>
                <tbody>
                  {attempts.slice(0, 12).map((row) => (
                    <tr key={row.n} className={row.origin_m == null ? 'text-muted-foreground' : 'text-foreground'}>
                      <td>{row.n}{row.origin_m == null ? ' (진입)' : ''}</td>
                      <td className="text-right">{row.time_s.toFixed(3)}</td>
                      <td className={`text-right ${finite(drill.baseline_s) && row.origin_m != null && row.comparable !== false ? (row.time_s < drill.baseline_s ? 'text-success' : 'text-destructive') : ''}`}>
                        {finite(drill.baseline_s) && row.origin_m != null && row.comparable !== false ? signed(row.time_s - drill.baseline_s, 3) : '--'}
                      </td>
                      <td className="text-right">{finite(row.brake_start_m) ? `${row.brake_start_m.toFixed(0)} m` : '--'}</td>
                      <td className="text-right">{finite(row.min_speed_kph) ? row.min_speed_kph.toFixed(1) : '--'}</td>
                      <td className="text-right">{finite(row.full_throttle_m) ? `${row.full_throttle_m.toFixed(0)} m` : '--'}</td>
                      {target && <td className={`text-right ${row.input_ok ? 'text-success' : ''}`}>{finite(row.input_change) ? `${signed(row.input_change, 1)} ${target.unit}${row.input_ok ? ' ✓' : ''}` : '--'}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 text-xs text-muted-foreground">제동 시작·풀스로틀은 저장 위치(구간 시작)에서 몇 m 뒤인지입니다. “(진입)”은 저장 전 그냥 달려 들어온 시도로, 시간 비교에서 뺍니다.</p>
            </div>
          )}
          <Button variant="outline" type="button" disabled={busy} onClick={stop} className="w-fit border px-3 py-2 text-sm">구간 연습 끝내기</Button>
        </>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      <PracticeDrillHistory apiBase={apiBase} taskId={task?.id} revision={mine ? drill.serial : 0} />
      <DrillGuide />
    </div>
  );
}
