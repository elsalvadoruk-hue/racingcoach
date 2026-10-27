import React, { useEffect, useRef, useState } from 'react';
import { Check, Heart, History, Target } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '../components/ui/card';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '../components/ui/empty';
import { Skeleton } from '../components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group';
import { cn } from '../lib/utils';
import './DriverJournal.css';

const FEELINGS = [['COMFORTABLE','편했어요'],['NEUTRAL','보통이에요'],['DIFFICULT','어려웠어요'],['UNSURE','잘 모르겠어요']];
const DIFFICULTIES = [['BRAKING','브레이크 밟을 때'],['CORNERING','코너 돌 때'],['ACCELERATION','가속할 때'],['OVERALL','전체적으로']];
const statuses = {ACTIVE:'진행 중', ACHIEVED:'달성', CANCELLED:'취소'};
const text = value => typeof value === 'string' ? value : '';
const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) ? `${value.slice(0,10).replaceAll('-','.')} ${value.slice(11,16)} UTC` : '주행 날짜 미확인';
const context = drive => [date(drive?.recorded_at), text(drive?.labels?.car) || '차량 미확인', text(drive?.labels?.track) || '코스 미확인'].join(' · ');

function JournalMessage({journal, compact = false}) {
  if (compact && journal.error) return <div role="alert" className="driver-journal-compact-message"><span className="truncate" title={journal.error}>기록 확인이 필요해요</span><Button variant="ghost" size="sm" onClick={journal.retry} disabled={journal.busy}>기록 다시 확인</Button></div>;
  return journal.error ? <Alert variant="destructive"><AlertTitle>체감·도전 기록 확인이 필요해요</AlertTitle><AlertDescription>{journal.error}<Button variant="outline" size="sm" onClick={journal.retry} disabled={journal.busy}>기록 다시 확인</Button></AlertDescription></Alert>
    : <p className={cn(compact && 'driver-journal-compact-status')} role="status" aria-live="polite">{journal.busy ? '기록을 확인하고 있어요…' : journal.message}</p>;
}

function JournalDetails({open, onOpenChange, trigger, title, children}) {
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="driver-report-scene driver-reflection-dialog driver-journal-detail" onCloseAutoFocus={event=>{event.preventDefault();trigger.current?.focus();}}><DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription>주행 기록의 자세한 내용과 선택 사항을 확인하세요.</DialogDescription></DialogHeader><div className="driver-journal-detail-body">{children}</div><DialogFooter><DialogClose asChild><Button variant="outline">닫기</Button></DialogClose></DialogFooter></DialogContent></Dialog>;
}

function compactTarget(candidate) {
  const target = candidate?.target, parts = [];
  if (Number.isInteger(target?.min_laps) && target.min_laps > 0) parts.push(`${target.min_laps}랩 이상`);
  if (Number.isFinite(target?.pace_limit_s) && target.pace_limit_s > 0) parts.push(`중앙 ≤ ${target.pace_limit_s.toFixed(3)}초`);
  if (Number.isFinite(target?.mad_limit_pct) && target.mad_limit_pct >= 0) parts.push(`편차 ≤ ${target.mad_limit_pct.toFixed(3)}%`);
  return parts.join(' · ') || text(candidate?.description);
}

export function DriverFeelingCard({journal, fixed = false, compact = false}) {
  const [picker, setPicker] = useState(false), [skipped, setSkipped] = useState(false), [detailsOpen, setDetailsOpen] = useState(false);
  const pickerTrigger=useRef(null), detailsTrigger=useRef(null), pendingPickerFocus=useRef(false);
  const data = journal.data, selected = data?.drives?.find(row=>row.drive_id===data.selected_drive_id);
  useEffect(()=>setSkipped(false),[data?.selected_drive_id]);
  useEffect(()=>{
    if(pendingPickerFocus.current && !picker && !journal.busy){
      pendingPickerFocus.current=false;
      if(document.activeElement===document.body)pickerTrigger.current?.focus();
    }
  },[picker,journal.busy]);
  const feeling = data?.feeling;
  return <Card className={cn('driver-report-feeling', compact && 'driver-journal-compact driver-feeling-compact')} aria-label="주행 체감 기록" aria-busy={journal.busy}>
    <CardHeader>{compact ? <CardTitle asChild><h2 className="driver-journal-compact-title"><Heart aria-hidden="true" />나의 체감</h2></CardTitle> : <><div className="driver-report-eyebrow"><Heart aria-hidden="true" />나의 체감</div><CardTitle asChild><h2>오늘 운전하기 어땠나요?</h2></CardTitle></>}<CardDescription className={cn(compact && 'driver-journal-context truncate')} title={selected ? context(selected) : undefined}>{selected ? context(selected) : '분석한 주행을 선택하고 그때의 느낌을 남겨 보세요.'}</CardDescription></CardHeader>
    <CardContent>{!data && journal.busy ? <Skeleton className="h-10 w-full" /> : selected ? <>
      {skipped && !feeling ? <Button variant="outline" onClick={()=>setSkipped(false)}>체감 남기기</Button> : <>
        <ToggleGroup type="single" variant="outline" aria-label="주행 체감" className="driver-journal-choices" value={feeling?.response || ''} disabled={journal.busy}
          onValueChange={value=>{if(value) journal.saveFeeling(value);}}>{FEELINGS.map(([value,label])=><ToggleGroupItem key={value} value={value}>{label}</ToggleGroupItem>)}</ToggleGroup>
        {!compact && feeling?.response === 'DIFFICULT' ? <div className="driver-journal-difficulty"><p>어느 때 어려웠나요? · 선택 사항</p><ToggleGroup type="single" variant="outline" aria-label="어려웠던 상황" className="driver-journal-choices" value={feeling.difficulty || ''} disabled={journal.busy}
          onValueChange={value=>journal.saveFeeling('DIFFICULT',value || null)}>{DIFFICULTIES.map(([value,label])=><ToggleGroupItem key={value} value={value}>{label}</ToggleGroupItem>)}</ToggleGroup></div> : null}
        {!compact ? <p className="driver-journal-note">선택하면 바로 저장합니다. 느낌은 주행 전체에 남기며 측정 결과와 따로 읽습니다.</p> : null}
      </>}
    </> : journal.error ? null : <Empty><EmptyHeader><EmptyTitle>남길 주행을 기다려요</EmptyTitle><EmptyDescription>주행 파일 분석이 끝나면 이곳에서 느낌을 선택할 수 있습니다.</EmptyDescription></EmptyHeader></Empty>}
      <JournalMessage journal={journal} compact={compact} />
    </CardContent>
    <CardFooter className="driver-journal-actions">
      {!fixed && data?.drives?.length ? <Button ref={pickerTrigger} variant="outline" size="sm" onClick={()=>setPicker(true)} disabled={journal.busy}>체감 남길 주행 선택</Button> : null}
      {feeling ? <Button variant="ghost" size="sm" onClick={journal.deleteFeeling} disabled={journal.busy}>체감 기록 지우기</Button>
        : selected && !skipped ? <Button variant="ghost" size="sm" onClick={()=>setSkipped(true)} disabled={journal.busy}>건너뛰기</Button> : null}
      {compact ? <Button ref={detailsTrigger} variant="ghost" size="sm" aria-label="체감 상세 보기" title={DIFFICULTIES.find(([value])=>value===feeling?.difficulty)?.[1]} onClick={()=>setDetailsOpen(true)}>{feeling?.response==='DIFFICULT' ? '상세 상황' : '체감 안내'}</Button> : null}
    </CardFooter>
    <Dialog open={picker} onOpenChange={setPicker}><DialogContent className="driver-report-scene driver-journal-picker" onCloseAutoFocus={event=>{event.preventDefault();pendingPickerFocus.current=!pickerTrigger.current||pickerTrigger.current.disabled;if(!pendingPickerFocus.current)pickerTrigger.current.focus();}}><DialogHeader><DialogTitle>체감을 남길 주행</DialogTitle><DialogDescription>해당 날짜·차량·코스를 확인해 선택하세요.{data?.coverage?.drives?.total>data?.drives?.length?` 최근 ${data.drives.length}개와 지정한 주행을 표시합니다.`:''}</DialogDescription></DialogHeader>
      <div className="driver-journal-drive-options">{data?.drives?.map(drive=><Button key={drive.drive_id} variant="outline" onClick={()=>{journal.selectDrive(drive.drive_id);setPicker(false);}}><span>{context(drive)}</span>{drive.drive_id===data.selected_drive_id ? <Check data-icon="inline-end" /> : null}</Button>)}</div>
      <DialogFooter><DialogClose asChild><Button variant="outline">닫기</Button></DialogClose></DialogFooter>
    </DialogContent></Dialog>
    {compact ? <JournalDetails open={detailsOpen} onOpenChange={setDetailsOpen} trigger={detailsTrigger} title="주행 체감 상세"><DriverFeelingCard journal={journal} fixed={fixed}/></JournalDetails> : null}
  </Card>;
}

export function DriverChallengeCard({journal, onNavigate, compact = false}) {
  const [historyOpen, setHistoryOpen] = useState(false), [detailsOpen, setDetailsOpen] = useState(false);
  const historyTrigger=useRef(null), detailsTrigger=useRef(null);
  const data = journal.data, active = data?.active_challenge, history = data?.challenge_history || [];
  const achieved = Number.isInteger(data?.coverage?.challenge_history?.achieved) && data.coverage.challenge_history.achieved>=0 ? data.coverage.challenge_history.achieved : null;
  return <Card className={cn('driver-report-card driver-report-challenge', compact && 'driver-journal-compact driver-challenge-compact')} aria-label="다음 도전" aria-busy={journal.busy}>
    <CardHeader>{compact ? <div className="driver-journal-compact-heading"><CardTitle asChild><h2 className="driver-journal-compact-title"><Target aria-hidden="true" />다음 목표</h2></CardTitle>{achieved>0 ? <Badge variant="secondary">달성 {achieved}개</Badge> : null}</div> : <><div className="driver-report-eyebrow"><Target aria-hidden="true" />다음 목표{achieved>0 ? <Badge variant="secondary">달성 {achieved}개</Badge> : null}</div><CardTitle asChild><h2>{active?.title || '다음 주행의 작은 도전'}</h2></CardTitle></>}</CardHeader>
    <CardContent>{!data && journal.busy ? <Skeleton className="h-16 w-full" /> : active ? <>
      {compact ? <div className="driver-journal-active-summary"><Badge variant="secondary">진행 중</Badge><strong className="truncate" title={text(active.description)}>{text(active.title)}</strong><p className="truncate" title={text(active.description)}>{compactTarget(active)}</p></div> : <><Badge variant="secondary">진행 중</Badge><CardDescription>{text(active.description)}</CardDescription><p className="driver-journal-note">{text(active.evaluation?.reason) || '선택한 뒤의 새 주행에서 같은 기준을 확인합니다.'}</p></>}
      <div className="driver-journal-actions"><Button size="sm" variant="outline" onClick={journal.reconcile} disabled={journal.busy}>달성 여부 확인</Button><Button size="sm" variant="ghost" onClick={()=>journal.cancelChallenge(active.id)} disabled={journal.busy}>도전 취소</Button></div>
    </> : data?.candidates?.length ? <div className={cn('driver-journal-candidates', compact && 'driver-journal-candidates-compact')}>{data.candidates.map(candidate=><div key={candidate.candidate_id} title={compact ? [text(candidate.description),text(candidate.reason)].filter(Boolean).join(' ') : undefined}><strong className={cn(compact && 'truncate')}>{text(candidate.title)}</strong><p className={cn(compact && 'truncate')}>{compact ? compactTarget(candidate) : text(candidate.description)}</p><Button size="sm" variant="outline" onClick={()=>journal.chooseChallenge(candidate)} disabled={journal.busy}>{compact ? <><span aria-hidden="true">선택</span><span className="sr-only">이 도전 선택 · {text(candidate.title)}</span></> : <>이 도전 선택<span className="sr-only"> · {text(candidate.title)}</span></>}</Button></div>)}</div>
      : <CardDescription className={cn(compact && 'driver-journal-empty-compact')} title={compact ? text(data?.candidate_reason) : undefined}>{text(data?.candidate_reason) || '측정 가능한 주행을 분석하면 그 기록에 맞는 목표를 선택할 수 있습니다.'}</CardDescription>}
      {compact ? <JournalMessage journal={journal} compact/> : journal.error ? <p role="alert">{journal.error}</p> : null}
    </CardContent>
    <CardFooter className="driver-journal-actions"><Button ref={historyTrigger} variant="ghost" size="sm" onClick={()=>setHistoryOpen(true)}><History data-icon="inline-start" />도전·달성 기록</Button>{onNavigate && !compact ? <Button variant="ghost" size="sm" onClick={onNavigate}>주행 분석에서 확인</Button> : null}{compact ? <Button ref={detailsTrigger} variant="ghost" size="sm" onClick={()=>setDetailsOpen(true)}>도전 상세</Button> : null}</CardFooter>
    <Dialog open={historyOpen} onOpenChange={setHistoryOpen}><DialogContent className="driver-report-scene driver-journal-history" onCloseAutoFocus={event=>{event.preventDefault();historyTrigger.current?.focus();}}><DialogHeader><DialogTitle>도전과 달성 기록</DialogTitle><DialogDescription>선택할 때의 목표를 고정하고, 이후 확인한 주행의 결과를 보관합니다.{data?.coverage?.challenge_history?.total>history.length?` 최근 ${history.length}개 이력을 표시합니다.`:''}</DialogDescription></DialogHeader>
      <div className="driver-journal-history-list">{history.length ? history.map(row=><article key={row.id}><Badge variant="secondary">{statuses[row.status] || '확인 필요'}</Badge><h3>{text(row.title)}</h3><p>{text(row.evaluation?.reason) || text(row.description)}</p><p>{date(row.achieved_at || row.cancelled_at || row.accepted_at)}</p></article>) : <p>선택한 도전의 달성·취소 이력이 이곳에 남습니다.</p>}</div>
      <DialogFooter><DialogClose asChild><Button variant="outline">닫기</Button></DialogClose></DialogFooter>
    </DialogContent></Dialog>
    {compact ? <JournalDetails open={detailsOpen} onOpenChange={setDetailsOpen} trigger={detailsTrigger} title="도전 목표 상세"><DriverChallengeCard journal={journal} onNavigate={onNavigate}/></JournalDetails> : null}
  </Card>;
}
