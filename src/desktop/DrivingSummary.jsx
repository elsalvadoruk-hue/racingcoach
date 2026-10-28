import React, { useRef, useState } from 'react';
import { ArrowRight, BookmarkCheck, ChartColumn, Check, Flag, MapPin, Target } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge, badgeVariants } from '../components/ui/badge';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '../components/ui/empty';
import { cn } from '../lib/utils';
import { TrackMap } from './Telemetry';
import { cornerLabel, number, signed, length, data, labRequest, analysisOptions } from './sessionData';
import { cornerAnnotations } from './cornerAnnotations';
import { DeviationLine } from './CornerPhases';
import { HandlingBadges, repeatedHandling } from './HandlingViews';
import SummaryAnalysis, { measuredSections, noteSection } from './SummaryAnalysis';
import FittedSections from './FittedSections';
import { requestJson, API_BASE } from '../analysis/analysisClient';
import PracticeCoach from '../components/PracticeCoach';
import SelectedDriveReflection from './SelectedDriveReflection';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { useBattleReport } from './battleReport';
import './battle.css';

// One line in the summary header: how many battles, and where the biggest chance was.
function BattleBanner({ filename, game, onOpen }) {
  const { report } = useBattleReport(filename, game);
  if (report?.status !== 'READY' || !report.summary?.battles) return null;
  const { battles, side_by_side: beside, top } = report.summary;
  return <div className="summary-battle" role="status"><strong>배틀 {battles}번{beside ? ` · 나란히 ${beside}` : ''}</strong>
    {top?.action && <span>대응 비교: {top.label} · {top.lap}랩 {top.action}</span>}
    <Button variant="outline" size="sm" onClick={onOpen}>배틀 분석 보기<ArrowRight data-icon="inline-end" /></Button></div>;
}

const columnNames = { tasks: '오늘의 연습 과제', next: '다음 주행에서 할 일', analysis: '비교·분석' };
const phaseNames = { braking: '제동', rotation: '회전', drive: '가속', straight_after: '탈출 뒤' };

export default function DrivingSummary({ comparison, names, onCorner, onCompare, onPractice, analysisWorkspace, battleFile = null, game = 'iracing', onBattle }) {
  // LMU recordings now run through the shared analysis: the practice flow is
  // the iRacing one. Only a payload from the old measurement-only route keeps
  // its limits; the LMU map stays a schematic of the recorded coordinates.
  const schematic = analysisWorkspace?.game === 'lmu';
  const lmu = schematic && data.session?.game !== 'lmu';
  const [practice, setPractice] = useState(data.practice || data.analysisResult?.practice || {});
  const [practiceOpen, setPracticeOpen] = useState(false);
  const [reflectionOpen, setReflectionOpen] = useState(false);
  const reflectionTrigger=useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const options = comparison.practiceSummary?.options || [];
  const [choice, setChoice] = useState(0);
  const [inspected, setInspected] = useState(null);
  // Exactly one column is wide at a time; the other two stay as short summaries.
  const [open, setOpen] = useState('tasks');
  const option = options[choice], focus = option?.focus;
  const plan = choice === 0 ? comparison.practiceSummary?.next_run : option?.next_run;
  const affected = (option?.corner_ids || []).map(id => comparison.corners.find(c => c.id === id)).filter(Boolean);
  const corner = affected.find(c => c.id === inspected) || affected[0];
  const prepared = !lmu && Boolean(practice.active);
  async function savePreparation() {
    if (lmu) { onPractice?.(); return; }
    if (prepared) { setPracticeOpen(true); return; }
    setBusy(true); setError('');
    try {
      const current = await labRequest('comparison', { filename: data.filename, ...analysisOptions(analysisWorkspace), target_lap: data.defaultTarget,
        reference_lap: data.defaultReference, reference_filename: data.reference_filename,
        reference_selection: data.reference_selection, external_reference_files: data.external || [],
        reference_mode: (data.external || []).length > 1 ? 'AGGREGATE' : (data.external || []).length ? 'SINGLE' : 'LOCAL' });
      const selected = current.practiceSummary?.options?.find(o => o.focus.root_cause === focus.root_cause && o.focus.corner_id === focus.corner_id);
      if (!selected) throw new Error('비교 조건이 변경되었습니다. 분석을 갱신해 주세요.');
      const result = await requestJson('/api/practice/start', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: data.filename, source: selected.practice_source || 'WORKSPACE', focus_index: selected.practice_focus_index ?? choice }) });
      data.practice = result.practice; setPractice(result.practice);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const notes = corner ? cornerAnnotations(corner, comparison.rows || [], length) : [];
  const minSpeed = corner && Number.isFinite(corner.target.min_speed_kph) && Number.isFinite(corner.reference.min_speed_kph)
    ? corner.target.min_speed_kph - corner.reference.min_speed_kph : null;
  const placeKey = { BRAKE_RELEASE_LATE: 'brake_release', BRAKE_POINT_EARLY: 'brake_start', THROTTLE_RETURN_LATE: 'full_throttle' }[focus?.root_cause];
  const place = placeKey && notes.find(note => note.key === placeKey);
  const [metricKey, metricLabel, metricUnit, metricDigits] = {
    OVER_BRAKING_ABS: ['abs_duty_pct', 'ABS 개입 차이', '%p', 1],
    EXIT_INSTABILITY: ['throttle_relifts', '스로틀 재리프트 차이', '회', 0],
    ROTATION_DEFICIT: ['avg_abs_steer_deg', '평균 조향량 차이', '°', 1],
  }[focus?.root_cause] || ['coast_time_s', '코스팅 시간 차이', 's', 2];
  const metricDelta = corner && Number.isFinite(corner.target[metricKey]) && Number.isFinite(corner.reference[metricKey])
    ? corner.target[metricKey] - corner.reference[metricKey] : null;
  const lockList = plan?.lock || ['확인할 유지 조건이 없습니다.'];
  const criteriaList = plan?.success_criteria || [];
  const repeats = comparison.handling?.repeated || comparison.analysisResult?.handling?.repeated || [];
  const repeated = repeatedHandling(repeats);
  const handling = corner?.handling || [];

  // A click anywhere in a narrow column widens it; its own action buttons still do their job.
  const columnProps = id => ({
    id: `summary-${id}`, 'data-column': id, 'data-open': open === id ? 'true' : 'false',
    onClick: event => { if (open !== id && !event.target.closest('[data-own-action]')) setOpen(id); },
  });
  const widen = id => <Button variant="ghost" size="sm" className="summary-widen" aria-expanded={open === id} aria-controls={`summary-${id}`}
    aria-label={`${columnNames[id]} 넓게 보기`} disabled={open === id} onClick={() => setOpen(id)}>{open === id ? '펼침' : '넓게'}</Button>;

  return <section className={cn('driving-summary', plan?.mode?.startsWith('SETUP_') && 'summary-has-setup')} aria-label="주행 요약" data-expanded={open}>
    <div className="summary-intro"><div><h2>주행 요약</h2><p>과제를 고르면 내용이 바뀝니다. 카드 빈 곳을 누르면 넓게 봅니다.</p></div>{battleFile && <BattleBanner filename={battleFile} game={game} onOpen={onBattle} />}<Button ref={reflectionTrigger} variant="outline" size="sm" onClick={()=>setReflectionOpen(true)}>이 주행 체감 남기기</Button></div>
    <div className="summary-columns">
      <Card className={cn('summary-column summary-task', open === 'tasks' && 'is-open')} aria-labelledby="today-task-title" {...columnProps('tasks')}>
        <CardHeader className="summary-column-header"><div className="summary-column-title"><CardTitle asChild><h3 id="today-task-title" className="flex items-center gap-2"><Target />오늘의 연습 과제<Badge variant="secondary">{options.length}개</Badge></h3></CardTitle>{widen('tasks')}</div>
          {open === 'tasks' && <CardDescription>{lmu ? '관측 시간 차이 · 확인할 입력 · 반복 측정 기준을 확인하세요.' : plan?.mode?.startsWith('SETUP_') ? '다음 주행은 셋업 확인 계획입니다. 입력 연습은 별도 주행으로 진행하세요.' : '문제 · 바꿀 조작 · 확인 기준입니다. 과제를 고르면 다른 칸도 바뀝니다.'}</CardDescription>}</CardHeader>
        <CardContent className="summary-column-content">
          {focus ? <ToggleGroup className="summary-issues" type="single" value={String(choice)} onValueChange={value => { if (value !== '') { setChoice(Number(value)); setInspected(null); } }} aria-label="이번 주행에 집중할 과제">
            {options.map((item, index) => <ToggleGroupItem data-own-action className={cn('summary-issue', choice === index && 'is-focused')} key={item.focus.root_cause} value={String(index)} aria-label={`이번 연습: ${item.focus.root_cause_label}`} data-testid="summary-issue">
              <span className="summary-issue-heading"><span className="summary-rank numeric">{String(index + 1).padStart(2, '0')}</span><strong className="summary-issue-title">{item.focus.root_cause_label}</strong><strong className="summary-issue-loss numeric">+{number(item.focus.total_observed_loss_s, 3)}<small> s</small></strong></span>
              <span className="summary-issue-location"><MapPin aria-hidden="true" />{item.corner_ids.join(' · ') || '위치 확인 필요'}{open === 'tasks' && <span className={badgeVariants({ variant: choice === index ? 'default' : 'outline' })}>{choice === index ? '집중 과제' : '이번에 연습'}</span>}</span>
              {open === 'tasks' && <>
                <span className="summary-issue-action">{item.focus.action}</span>
                <DeviationLine deviation={item.focus.first_deviation || comparison.corners.find(c => c.id === item.focus.corner_id)?.first_deviation} />
                <span className="summary-issue-check"><Check aria-hidden="true" />{(item.focus.success_criteria || []).join(' · ')}</span>
              </>}
            </ToggleGroupItem>)}
          </ToggleGroup> : <Empty><EmptyHeader><EmptyTitle>추천할 연습 과제가 없습니다</EmptyTitle><EmptyDescription>현재 비교에 뚜렷한 손실이 없거나 자료가 부족합니다. 다른 랩이나 기준을 선택해 확인하세요.</EmptyDescription></EmptyHeader><Button variant="outline" data-own-action onClick={onCompare}>비교 변경</Button></Empty>}
        </CardContent>
        {open === 'tasks' && <CardFooter className="summary-column-footer"><p>{lmu ? '시간 차이는 실제 관측된 구간의 차이이며 원인이나 조작 효과를 확정하지 않습니다.' : '손실은 각 과제의 대상 구간 합계입니다. 서로 겹칠 수 있으므로 합산하지 않습니다.'}</p></CardFooter>}
      </Card>

      <Card className={cn('summary-column summary-next', open === 'next' && 'is-open')} aria-labelledby="next-run-title" {...columnProps('next')}>
        <CardHeader className="summary-column-header"><div className="summary-column-title"><CardTitle asChild><h3 id="next-run-title" className="flex items-center gap-2"><Flag />다음 주행에서 할 일</h3></CardTitle>{widen('next')}</div>
          <CardDescription>{plan?.mode_label || '비교 자료를 먼저 확인하세요.'}</CardDescription></CardHeader>
        <CardContent className="summary-column-content">{plan ? <>
          {focus && plan.mode === 'DRIVING_FOCUS' && <><div className="summary-task-title"><div className="summary-plan-meta"><Badge variant={prepared ? 'default' : 'secondary'}>{prepared ? '연습 진행 중' : '이번에 집중'}</Badge><span className="summary-loss">{lmu ? '관측 시간 차이' : '관측 손실'} <strong className="numeric">+{number(focus.total_observed_loss_s, 3)} <small>s</small></strong></span></div><h2>{focus.root_cause_label}</h2></div>
            <p className="summary-action">{focus.action}</p>
            {open === 'next' && <div className="summary-corners" aria-label="연습 대상 코너">{affected.map(c => <Button key={c.id} data-own-action variant="outline" size="sm" onClick={() => setInspected(c.id)} aria-pressed={corner?.id === c.id}><MapPin data-icon="inline-start" />{c.id} · {cornerLabel(c, names)}</Button>)}</div>}</>}
          <div className="summary-run-goal"><span>이번 주행 목표</span><h2>{lmu ? '측정 가능' : '유효'} <b>{lmu ? plan.required_measured_laps : plan.required_valid_laps}</b>랩 이상 <small>권장 {lmu ? plan.recommended_measured_laps : plan.recommended_valid_laps}랩</small></h2>{plan.required_clean_corners > 0 && <p>정상 구간 {plan.required_clean_corners}회 이상 확인</p>}{focus && plan.mode === 'DRIVING_FOCUS' && <Button data-own-action disabled={busy || !data.filename} onClick={savePreparation} variant={prepared ? 'secondary' : 'default'}><BookmarkCheck data-icon="inline-start" />{busy ? '과제 저장 중…' : prepared ? '진행 과제 확인' : lmu ? '기록에서 연습 기준 준비' : '이 과제로 연습 시작'}</Button>}</div>
          {plan.mode !== 'DRIVING_FOCUS' && <div className="summary-run-instruction"><strong>{plan.title}</strong><p>{plan.primary_instruction}</p></div>}
          {open === 'next'
            ? <div className="summary-run-details"><FittedSections label="공간에 맞춘 다음 주행 계획" sections={[
              { key: 'lock', content: <div className="summary-run-lock"><h4>유지할 것</h4><ul>{lockList.map((text, i) => <li key={i}>{text}</li>)}</ul></div> },
              { key: 'criteria', content: <div className="summary-run-criteria"><h4>주행 후 확인할 기준</h4><ul>{criteriaList.map((text, i) => <li key={i}><Check aria-hidden="true" /><span>{text}</span></li>)}</ul></div> },
              ...(corner ? measuredSections(corner) : []),
              ...notes.map(noteSection),
            ]} /></div>
            : <p className="summary-run-counts">유지할 것 {lockList.length}개 · 확인 기준 {criteriaList.length}개</p>}
        </> : <Empty><EmptyHeader><EmptyTitle>다음 주행 계획 확인 전</EmptyTitle><EmptyDescription>비교할 기록을 선택하면 계획을 계산합니다.</EmptyDescription></EmptyHeader></Empty>}</CardContent>
        {open === 'next' && <CardFooter className="summary-column-footer"><ArrowRight aria-hidden="true" /><p>{plan?.after_run || '새 기록을 불러와 비교해 주세요.'}</p></CardFooter>}
      </Card>

      <Card className={cn('summary-column summary-analysis', open === 'analysis' && 'is-open')} aria-labelledby="analysis-title" {...columnProps('analysis')}>
        <CardHeader className="summary-column-header"><div className="summary-column-title"><CardTitle asChild><h3 id="analysis-title" className="flex items-center gap-2"><ChartColumn />비교·분석</h3></CardTitle>{widen('analysis')}</div>
          <CardDescription>{corner ? `${corner.id} · ${cornerLabel(corner, names)} · 구간 ${signed(corner.delta_s)} s` : '과제 구간이 없습니다.'}</CardDescription></CardHeader>
        <CardContent className="summary-column-content">{corner ? (open === 'analysis' ? <>
          <SummaryAnalysis corner={corner} notes={notes} affected={affected} names={names} repeats={repeats} />
          <Button className="summary-analysis-link" variant="outline" size="sm" data-own-action onClick={() => onCorner(corner.id)}>코너 비교에서 보기<ArrowRight data-icon="inline-end" /></Button>
        </> : <>
          {corner.phases && <dl className="summary-phase-mini">{['braking', 'rotation', 'drive', 'straight_after'].map(key => { const value = corner.phases[key]?.delta_s; return <div key={key}><dt>{phaseNames[key]}</dt><dd className={cn('numeric', Number.isFinite(value) && value > 0 && 'loss', Number.isFinite(value) && value < 0 && 'gain')}>{Number.isFinite(value) ? `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(2)}` : '—'}</dd></div>; })}</dl>}
          <DeviationLine deviation={corner.first_deviation} />
          {handling.length > 0 && <div className="summary-mini-row"><span>차 거동</span><HandlingBadges items={handling} /></div>}
          <p className="summary-run-counts">그래프 비교 포인트 {notes.length}개{repeated.length > 0 ? ` · 반복되는 거동 ${repeated.length}개` : ''}</p>
        </>) : <p className="insight-empty">과제를 고르면 그 구간의 비교 결과를 보여 줍니다.</p>}</CardContent>
      </Card>
    </div>

    <div className="summary-context">
      <Card className="summary-location">
        <CardHeader className="summary-context-header"><CardTitle>연습 위치</CardTitle><CardDescription>{schematic ? '기록 좌표 개략도' : 'GPS 주행선'} · 초록 이득 / 빨강 {lmu ? '시간 차이' : '손실'}</CardDescription></CardHeader>
        <CardContent className="summary-map-content"><TrackMap schematic={schematic} points={comparison.paths.target} loss={comparison.loss} corners={affected} selected={corner} onSelect={setInspected} names={names} /></CardContent>
      </Card>
      <Card className="summary-evidence">
        <CardHeader className="summary-context-header"><div className="flex items-center justify-between gap-3"><CardTitle>{corner ? `${corner.id} · ${cornerLabel(corner, names)}` : '측정 근거'}</CardTitle><Button variant="ghost" size="sm" disabled={!corner} onClick={() => onCorner(corner.id)}>코너 비교에서 보기<ArrowRight data-icon="inline-end" /></Button></div><CardDescription>현재 선택한 내 랩과 기준 랩의 실제 차이{lmu && comparison.observation_notes?.length > 0 && <span className="block">{comparison.observation_notes.join(' ')}</span>}</CardDescription></CardHeader>
        <CardContent className="summary-evidence-content"><dl><div><dt>구간 시간 차이</dt><dd className="numeric">{signed(corner?.delta_s)} <small>s</small></dd></div><div><dt>최저 속도 차이</dt><dd className="numeric">{signed(minSpeed, 1)} <small>km/h</small></dd></div><div><dt>{place ? `${place.label} 위치 차이` : metricLabel}</dt><dd className="numeric">{signed(place ? place.difference : metricDelta, place ? 0 : metricDigits)} <small>{place ? 'm' : metricUnit}</small></dd></div></dl></CardContent>
        <CardFooter className="summary-save-status" role="status"><span className="summary-status-dot" /><p>{lmu ? '기록에서 측정 랩 3~6개로 연습 기준을 저장하고 새 주행의 편차와 시간을 확인하세요.' : error || practice.check?.summary || (practice.active ? `${practice.active.title} · 다음 주행을 분석하면 입력과 구간 시간 변화를 확인합니다.` : '과제를 시작하면 다음 주행 평가와 연결됩니다.')}</p><Button size="sm" variant="outline" onClick={() => lmu ? onPractice?.() : setPracticeOpen(true)}>연습 기록·반복 연습</Button></CardFooter>
      </Card>
    </div>
    {!lmu && <Dialog open={practiceOpen} onOpenChange={setPracticeOpen}><DialogContent className="desktop-practice-dialog"><DialogHeader><DialogTitle>연습 과제·주행 후 확인</DialogTitle><DialogDescription>입력 변화와 구간 시간은 각각 평가합니다.</DialogDescription></DialogHeader>
      <PracticeCoach apiBase={API_BASE} filename={data.filename} practice={practice} onPracticeChange={next => { data.practice = next; setPractice(next); }} coach={data.analysisResult?.closed_loop_coach}
        visualizations={data.analysisResult?.visualizations} trackLength={length} corners={comparison.corners}
        focusSets={data.analysisResult?.practice_focuses} referenceLabel={comparison.reference_label}
        loadWindow={analysisWorkspace?.localCornerLoader} externalCorners={data.external?.length ? comparison.corners : []}
        externalLoadWindow={data.external?.length ? analysisWorkspace?.externalCornerLoader : null} externalLabel={comparison.reference_label} />
    </DialogContent></Dialog>}
    <Dialog open={reflectionOpen} onOpenChange={setReflectionOpen}><DialogContent className="driver-reflection-dialog" onCloseAutoFocus={event=>{event.preventDefault();reflectionTrigger.current?.focus();}}><DialogHeader><DialogTitle>이 주행의 체감</DialogTitle><DialogDescription>지금 분석한 파일에 연결된 주행에 느낌을 남깁니다.</DialogDescription></DialogHeader><SelectedDriveReflection base={API_BASE} game={schematic ? 'lmu' : 'iracing'} link={data.analysisResult?.driver_history} /></DialogContent></Dialog>
  </section>;
}
