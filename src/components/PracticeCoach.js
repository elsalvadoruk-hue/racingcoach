import React, { useEffect, useState } from 'react';
import { Button } from './ui/button';
import FocusMiniMap from './FocusMiniMap';
import CornerDrillPanel from './CornerDrillPanel';
import CornerTrace, { CAUSE_EVENT } from './CornerTrace';
import PracticeDrillHistory from './PracticeDrillHistory';

// "Today's one thing": one driving focus at a time, checked on the next run with measurements only
// (iRacing_Backend/practice.py). The detailed closed-loop panel stays below for drivers who want more.
const STATUS = {
  ACHIEVED: ['개선 확인', 'border-success/40 bg-success/10 text-success'],
  UNVERIFIED: ['효과 확인 필요', 'border-warning/40 bg-warning/10 text-warning'],
  INPUT_ONLY: ['입력만 바뀜', 'border-warning/40 bg-warning/10 text-warning'],
  FASTER_OTHER: ['다른 이유로 빨라짐', 'border-primary/40 bg-primary/10 text-primary'],
  NOT_YET: ['아직', 'border-border bg-muted text-muted-foreground'],
  NO_DATA: ['측정 불가', 'border-border bg-muted text-muted-foreground'],
  WAITING: ['다음 주행 대기', 'border-border bg-muted text-muted-foreground'],
};
// Ranked focuses repeat a root cause per speed class, so a candidate is named by its corners too.
const cornerText = (focus) => {
  const corners = focus.affected_corners || [focus.corner].filter(Boolean);
  return corners.length > 2 ? `${corners.slice(0, 2).join(', ')} 외 ${corners.length - 2}곳` : corners.join(', ');
};
// The driver's pick of what losses are measured against, kept between files.
const SOURCE_KEY = 'iracing.practice.source';
const storedSource = () => { try { return window.localStorage.getItem(SOURCE_KEY); } catch { return null; } };

const CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩';
const numbered = (labels) => labels.map((label, i) => `${CIRCLED[i] || `${i + 1}.`} ${label}`).join(', ');
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const signed = (value, digits = 2) => (finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}` : '--');

function InputChange({ row, target }) {
  if (!target || !finite(row.input_change)) return <span className="text-muted-foreground">입력 측정 없음</span>;
  const digits = target.unit === '초' ? 2 : 1;
  return (
    <span className={row.input_done ? 'text-success' : 'text-muted-foreground'}>
      {target.label} {signed(row.input_change, digits)} {target.unit}
      <span className="text-muted-foreground"> (목표 {target.sign < 0 ? '−' : '+'}{target.goal} {target.unit}){row.input_done ? ' ✓' : ''}</span>
    </span>
  );
}

export default function PracticeCoach({ apiBase, filename, practice, coach, visualizations, trackLength, corners = [], loadWindow, referenceLabel,
  externalCorners = [], externalLoadWindow, externalLabel, focusSets, garage61Available = false, onUseGarage61 }) {
  const [view, setView] = useState(practice);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [choice, setChoice] = useState(0);
  useEffect(() => { setView(practice); setError(''); }, [practice]);
  const hasGarage61 = (focusSets?.GARAGE61 || []).length > 0;
  const [sourcePick, setSourcePick] = useState(() => storedSource());
  // the driver's last pick, else the Garage61 fast laps when this analysis has them
  const pickedSource = sourcePick === 'PERSONAL' || sourcePick === 'GARAGE61' ? sourcePick : hasGarage61 ? 'GARAGE61' : 'PERSONAL';
  const pickSource = (value) => { setSourcePick(value); try { window.localStorage.setItem(SOURCE_KEY, value); } catch { /* per-viewer convenience only */ } };
  const focuses = (focusSets ? focusSets[pickedSource] || [] : coach?.ranked_focuses || []).slice(0, 3);
  useEffect(() => {
    const concrete = focuses.findIndex((item) => item.root_cause !== 'DRIVER_VARIANCE');
    setChoice(concrete >= 0 ? concrete : 0);
  }, [coach, focusSets, pickedSource]); // eslint-disable-line react-hooks/exhaustive-deps

  const call = async (path, body) => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${apiBase}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '요청을 처리하지 못했습니다.');
      return data;
    } catch (failure) { setError(failure.message); return null; } finally { setBusy(false); }
  };
  const start = async () => {
    const data = await call('/api/practice/start', focusSets ? { filename, focus_index: choice, source: pickedSource } : { filename, focus_index: choice });
    if (data) setView(data.practice);
  };
  const close = async (status) => {
    const data = await call(`/api/practice/${view.active.id}/close`, { status });
    if (data) setView({ active: null, check: null, history: data.history });
  };

  const active = view?.active;
  const check = view?.check;
  const focus = focuses[choice];
  const history = view?.history?.by_cause || [];
  const markers = visualizations?.markers || [];
  const focusLabels = focus ? (focus.affected_corners || [focus.corner]).filter(Boolean) : [];
  // Losses measured against the Garage61 fast laps are shown against them too (graph, map positions);
  // otherwise against the driver's own reference lap.
  const source = active ? active.reference_source : focus?.source;
  const garage61 = String(source || '').startsWith('GARAGE61') && externalCorners.length > 0 && Boolean(externalLoadWindow);
  const shownCorners = garage61 ? externalCorners : corners;
  const shownLoader = garage61 ? externalLoadWindow : loadWindow;
  const shownLabel = garage61 ? (externalLabel || 'Garage61 빠른 랩') : referenceLabel;
  const labels = active ? (active.corners || []).map((c) => c.label) : focusLabels;
  // this run's measured corner first (it carries both laps' points for the graph), else the saved position
  const mapCorners = labels.map((label) => shownCorners.find((c) => c.label === label)
    || markers.find((m) => m.label === label) || (active?.corners || []).find((c) => c.label === label)).filter(Boolean);
  const mapKey = mapCorners.map((c) => c.label).join('|');
  const [pick, setPick] = useState(0);
  useEffect(() => { setPick(0); }, [mapKey]);
  const picked = mapCorners[Math.min(pick, Math.max(0, mapCorners.length - 1))];
  const measured = picked && shownCorners.find((c) => c.label === picked.label);
  const map = <FocusMiniMap key={mapKey} path={visualizations?.track_path} corners={mapCorners} trackLength={trackLength} pick={pick} onPick={setPick} vertical={Boolean(measured && shownLoader)} />;
  const referenceNote = (
    <p className="mt-1 text-xs text-muted-foreground" data-testid="practice-reference">
      손실 기준: <b className="text-foreground">{String(source || '').startsWith('GARAGE61') ? (externalLabel || 'Garage61 빠른 랩') : (referenceLabel || '내 기준 랩')}</b>
      {active && !String(source || '').startsWith('GARAGE61')
        && ' · 이 과제는 내 랩끼리 비교해 정한 것입니다. 빠른 드라이버 기준 과제를 원하면 이 과제를 그만두고 “Garage61 빠른 랩”을 고르세요.'}
    </p>
  );
  const rootCause = active ? active.root_cause : focus?.root_cause;
  const graph = measured && shownLoader ? (
    <div className="min-w-0" data-testid="practice-graph">
      <h4 className="mb-2 text-sm font-semibold text-foreground">{numbered(labels).split(', ')[Math.min(pick, labels.length - 1)]}: 이번 랩과 기준 랩 비교</h4>
      <CornerTrace corner={measured} trackLength={trackLength} load={shownLoader} focusEvent={CAUSE_EVENT[rootCause]} referenceLabel={shownLabel} />
    </div>
  ) : null;
  const hasMap = mapCorners.length > 0 && (visualizations?.track_path || []).some((p) => Number.isFinite(p?.lat));
  const visuals = (graph || hasMap) && (
    <div className={graph ? 'grid gap-4 border-t border-border pt-4 xl:grid-cols-[minmax(0,1fr)_auto]' : 'border-t border-border pt-4'}>
      {graph}
      {hasMap && map}
    </div>
  );
  return (
    <div className="flex flex-col gap-4" data-testid="practice-coach">
      <p className="text-sm text-muted-foreground">
        한 번에 한 가지만 고칩니다. 과제를 시작하면 같은 차·트랙·레이아웃의 다음 주행을 분석할 때, 그 코너에서 입력이 과제대로 바뀌었는지와 코너가 빨라졌는지를 측정값으로 확인합니다.
      </p>
      {!active && focusSets && (
        <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="practice-source">
          <span className="text-muted-foreground">손실을 비교할 기준</span>
          {[['PERSONAL', '내 주행'], ['GARAGE61', 'Garage61 빠른 랩']].map(([value, label]) => (
            <button key={value} type="button" aria-pressed={pickedSource === value} onClick={() => pickSource(value)}
              className={`rounded-lg border px-3 py-1 font-semibold transition ${pickedSource === value ? 'border-primary bg-primary/10 text-primary' : 'border-border text-foreground hover:bg-accent/40'}`}>{label}</button>
          ))}
        </div>
      )}
      {!active && focusSets && pickedSource === 'GARAGE61' && !hasGarage61 && (
        <div className="rounded-xl border border-border bg-background p-4 text-sm" data-testid="practice-garage61-missing">
          {garage61Available ? (
            <>
              <p className="text-foreground">이 분석에는 Garage61 빠른 랩과의 비교가 들어 있지 않습니다. Garage61 합성 기준으로 다시 분석하면 빠른 드라이버와 비교한 과제가 나옵니다(코너 비교 화면의 비교 기준도 Garage61 합성으로 바뀝니다).</p>
              <Button variant="default" type="button" disabled={busy || !onUseGarage61} onClick={onUseGarage61} className="mt-3 px-3 py-2 text-sm">Garage61 기준으로 다시 분석</Button>
            </>
          ) : (
            <p className="text-muted-foreground">이 트랙·차량의 Garage61 기준 랩이 없습니다. 설정 탭에 Garage61 토큰을 넣으면 앱이 빠른 드라이버의 랩을 받아 옵니다. 그동안은 “내 주행”으로 과제를 고를 수 있습니다.</p>
          )}
        </div>
      )}
      {active ? (
        <div className="flex flex-col gap-4 rounded-xl border border-primary/30 bg-background p-4">
          <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-base font-semibold text-foreground">진행 중인 과제: {active.title}</h3>
            {check && <span className={`rounded-lg border px-2 py-1 text-xs font-semibold ${(STATUS[check.status] || STATUS.NO_DATA)[1]}`}>{(STATUS[check.status] || STATUS.NO_DATA)[0]}</span>}
          </div>
          <p className="mt-1 text-sm text-foreground">{active.action}</p>
          <p className="mt-1 text-xs text-muted-foreground">코너: {numbered((active.corners || []).map((c) => c.label))} · 시작한 주행 {active.source_file}</p>
          {referenceNote}
          {check && <p className="mt-3 text-sm font-semibold text-foreground" data-testid="practice-summary">{check.summary}</p>}
          {(check?.reasons || []).length > 0 && <p className="mt-1 text-xs text-muted-foreground">확인 필요: {check.reasons.join(' · ')}</p>}
          {check && (check.corners || []).length > 0 && (
            <div className="mt-2 flex flex-col gap-1 font-mono text-sm">
              {check.corners.map((row) => (
                <div key={row.label} className="flex flex-wrap gap-x-4">
                  <span className="font-sans font-semibold text-foreground">{row.label}</span>
                  {row.matched ? <InputChange row={row} target={check.target} /> : <span className="text-muted-foreground">{row.note}</span>}
                  {finite(row.gain_s) && <span className={row.gain_s > 0 ? 'text-success' : row.gain_s < 0 ? 'text-destructive' : 'text-muted-foreground'}>
                    구간 {row.time_before_s.toFixed(3)} → {row.time_after_s.toFixed(3)} s ({row.gain_s > 0 ? `${row.gain_s.toFixed(3)}초 빨라짐` : `${Math.abs(row.gain_s).toFixed(3)}초 느려짐`})
                  </span>}
                  {(row.criteria || []).map((c) => <span key={c.metric} className="text-xs text-muted-foreground">{c.label}: {c.ok === true ? '충족' : c.ok === false ? '미달' : '측정 불가'}</span>)}
                </div>
              ))}
            </div>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="default" type="button" disabled={busy} onClick={() => close('DONE')} className="px-3 py-2 text-sm">연습 종료로 기록</Button>
            <Button variant="outline" type="button" disabled={busy} onClick={() => close('DROPPED')} className="border px-3 py-2 text-sm">그만두고 다른 과제 고르기</Button>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">연습 종료는 효과 확인과 별개입니다. 종료한 뒤에도 같은 차·트랙의 다음 정상 주행을 분석하면 가장 최근 종료 과제를 다시 확인합니다.</p>
          <div className="mt-4"><CornerDrillPanel apiBase={apiBase} filename={filename} task={active} corners={corners} /></div>
          </div>
          {visuals}
        </div>
      ) : focus ? (
        <div className="flex flex-col gap-4 rounded-xl border border-border bg-background p-4">
          <div className="min-w-0">
          <h3 className="text-base font-semibold text-foreground">오늘의 한 가지: {focus.root_cause_label}</h3>
          {referenceNote}
          <p className="mt-1 text-sm text-foreground">
            {numbered(focusLabels)}에서 합쳐 {finite(focus.total_observed_loss_s) ? focus.total_observed_loss_s.toFixed(2) : '--'}초를 잃었습니다
            {Array.isArray(focus.estimated_recovery_s) ? ` (되찾을 수 있는 시간 약 ${focus.estimated_recovery_s[0].toFixed(2)}~${focus.estimated_recovery_s[1].toFixed(2)}초)` : ''}.
          </p>
          <p className="mt-2 text-sm font-semibold text-foreground">{focus.action}</p>
          {(focus.success_criteria || []).length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-muted-foreground">{focus.success_criteria.map((item) => <li key={item}>{item}</li>)}</ul>}
          {focuses.length > 1 && (
            <div className="mt-4">
              <p className="mb-2 text-xs text-muted-foreground">다른 후보 (눌러서 바꾸기)</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {focuses.map((item, index) => (
                  <button key={index} type="button" aria-pressed={index === choice} onClick={() => setChoice(index)}
                    className={`flex flex-col items-start rounded-lg border px-3 py-2 text-left transition ${index === choice ? 'border-primary bg-primary/10' : 'border-border hover:bg-accent/40'}`}>
                    <span className={`text-sm font-semibold ${index === choice ? 'text-primary' : 'text-foreground'}`}>{item.root_cause_label}</span>
                    <span className="text-xs text-muted-foreground">{cornerText(item)}{finite(item.total_observed_loss_s) ? ` · ${item.total_observed_loss_s.toFixed(2)}초` : ''}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <Button variant="default" type="button" disabled={busy || !filename} onClick={start} className="mt-4 px-4 py-2 text-sm">이 과제로 연습 시작</Button>
          </div>
          {visuals}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">이번 주행에서는 고칠 만한 반복 손실을 찾지 못했습니다. 유효 랩을 몇 개 더 달려 보세요.</p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {(view?.followups || []).map((item) => <div key={item.task_id} className="flex flex-col gap-1 text-sm" data-testid="practice-followup">
        <p className="font-semibold">종료한 과제 재확인: {item.title}</p>
        <p>{item.check.summary}</p>
        {(item.check.reasons || []).length > 0 && <p className="text-xs text-muted-foreground">{item.check.reasons.join(' · ')}</p>}
      </div>)}
      {history.length > 0 && (
        <details className="rounded-xl border border-border p-3">
          <summary className="cursor-pointer text-sm font-semibold text-foreground">배운 것 · 지금까지의 과제</summary>
          <div className="mt-2 grid gap-1 text-sm">
            {history.map((item) => (
              <div key={item.root_cause} className="flex flex-wrap justify-between gap-2" data-testid={`practice-history-${item.root_cause}`}>
                <span className="text-foreground">{item.title}</span>
                <span className="font-mono text-muted-foreground">시도 {item.tried} · 종료 {item.done} · 개선 확인 {item.verified || 0} · 확인된 시간 {item.gain_s.toFixed(2)}초</span>
              </div>
            ))}
          </div>
          {(view?.history?.recent || []).map((item) => <div key={item.id} className="mt-3 flex flex-col gap-1 text-xs">
            <p>{item.title} · {item.status === 'DONE' ? '연습 종료' : item.status === 'ACTIVE' ? '진행 중' : '중단'}</p>
            <PracticeDrillHistory apiBase={apiBase} taskId={item.id} />
          </div>)}
        </details>
      )}
    </div>
  );
}
