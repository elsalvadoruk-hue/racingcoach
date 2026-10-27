import React, { useEffect, useRef, useState } from 'react';
import { Flag } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '../components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '../components/ui/empty';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '../components/ui/dialog';
import { Skeleton } from '../components/ui/skeleton';
import { cn } from '../lib/utils';
import { readReportJson } from './driverReportRequest';

const finish = {CLASSIFIED:'분류 완료',FINISHED:'완주 관측',DNF:'리타이어',DSQ:'실격',UNKNOWN:'상태 미확인'};
const text = value => typeof value === 'string' ? value : '';
const number = value => Number.isFinite(value) && value >= 0 ? value.toLocaleString('ko-KR',{maximumFractionDigits:2}) : '—';
const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0,10).replaceAll('-','.') : '날짜 미확인';

function ResultTotals({totals,resultCount}) {
  return totals ? <dl className="driver-results-totals"><div><dt>보관한 경기 결과</dt><dd>{number(totals.recorded_results??resultCount)}개</dd></div><div><dt>우승</dt><dd>{number(totals.wins?.value)}</dd></div><div><dt>포디엄</dt><dd>{number(totals.podiums?.value)}</dd></div><div><dt>평균 클래스 순위</dt><dd>{number(totals.average_position?.value)}</dd></div></dl> : null;
}

function ResultStatus({totals}) {
  return totals?<p className="driver-journal-note driver-results-status">{totals.official_races>0?`공식 최종 ${number(totals.official_races)}개`:'공식 결과 미확인'} · 종료 관측 {number(totals.finish_observations)}개 · 결과 미확정 {number(totals.unconfirmed_results)}개</p>:null;
}

function ResultEntry({row,compact=false}) {
  const classification = <Badge variant={row.status==='OFFICIAL'?'secondary':'outline'}>{row.status==='OFFICIAL'?'공식 최종 결과':['IRACING_FINISH_OBSERVATION','NATIVE_FINISH_OBSERVATION'].includes(row.authority)?'종료 관측':'결과 관측'}</Badge>;
  const finishLabel = `${row.finish_status==='UNKNOWN'&&row.authority==='IRACING_RESULT_TABLE'?'종료 미확인':finish[row.finish_status]||'상태 미확인'}${Number.isFinite(row.class_position)&&row.class_position>0?` · ${row.status==='OFFICIAL'?'':'관측 '}클래스 ${number(row.class_position)}위`:''}`;
  return <article className={compact?'driver-results-latest':undefined}>
    <div><time>{date(row.recorded_at)}</time>{classification}{compact?<span>{finishLabel}</span>:null}</div>
    <strong>{text(row.labels?.car)||'차량 미확인'} · {text(row.labels?.track)||'코스 미확인'}</strong>
    {!compact?<><p>{finishLabel}</p><p className="driver-journal-note">{text(row.reason)}</p></>:null}
  </article>;
}

function ResultsEmpty({compact=false}) {
  return <Empty className={compact?'driver-results-empty-compact':undefined}><EmptyHeader><EmptyTitle>아직 보관한 최종 결과가 없어요</EmptyTitle>{!compact?<EmptyDescription>기존 파일과 새 경기에서 최종 본인 결과를 확인하면 보관합니다. 진행 중 순위는 공식 결과로 집계하지 않습니다.</EmptyDescription>:null}</EmptyHeader></Empty>;
}

function ResultsError({current,onRetry,compact=false}) {
  return current.error ? <Alert variant="destructive" className={compact?'driver-results-error-compact':undefined}><AlertTitle>경기 결과 조회 실패</AlertTitle><AlertDescription>{!compact?current.error:null}<Button variant="outline" size="sm" disabled={current.busy} onClick={onRetry}>경기 결과 다시 확인</Button></AlertDescription></Alert> : null;
}

function ResultRange({coverage,resultCount}) {
  return coverage?.period_result_count>resultCount?<p className="driver-journal-note">합계는 선택 기간의 전체 보관 결과로 계산하며 목록은 최근 {resultCount}개를 표시합니다.</p>:null;
}

export default function DriverResults({base,game,period,revision=0,compact=false}) {
  const key=`${base}|${game}|${period}`;
  const [attempt,setAttempt]=useState(0),[state,setState]=useState({key:null,data:null,busy:true,error:''});
  const [expanded,setExpanded]=useState(false);
  const [detailsOpen,setDetailsOpen]=useState(false);
  const detailsTrigger=useRef(null);
  useEffect(()=>setDetailsOpen(false),[key]);
  useEffect(()=>{
    const controller=new AbortController();let active=true;
    setExpanded(false);
    setState(previous=>({key,data:previous.key===key?previous.data:null,busy:true,error:''}));
    readReportJson(`${base}/api/driver-results?game=${game}&period_days=${period}`,controller).then(payload=>{
      if(payload?.game!==game||payload.period_days!==Number(period)||!Array.isArray(payload.results))throw new Error('요청한 게임과 기간의 경기 결과를 확인하지 못했습니다.');
      if(active)setState({key,data:payload,busy:false,error:''});
    }).catch(error=>{if(active&&error.name!=='AbortError')setState(previous=>({...previous,key,busy:false,error:error.message||'경기 결과를 읽지 못했습니다.'}));});
    return()=>{active=false;controller.abort();};
  },[base,game,period,revision,key,attempt]);
  const current=state.key===key?state:{data:null,busy:true,error:''};
  const results=Array.isArray(current.data?.results)?current.data.results.filter(row=>row&&typeof row==='object'):[];
  const totals=current.data?.aggregate;
  const resultCount=current.data?.coverage?.period_result_count??results.length;
  const selectedPeriod=Number(period)===0?'전체 기간':`최근 ${period}일`;
  const retry=()=>setAttempt(value=>value+1);
  const card=<Card className={cn('driver-report-results',compact&&'driver-results-compact')} data-compact={compact} aria-label="보관한 경기 결과" aria-busy={current.busy}><CardHeader><div className="driver-report-eyebrow"><Flag aria-hidden="true" />경기 결과 보관함</div><CardTitle asChild><h2>경기 결과 기록</h2></CardTitle><CardDescription className={compact?'sr-only':undefined}>{selectedPeriod} · 본인의 경기 결과 관측과 공식 최종 결과를 구분해 보관합니다.</CardDescription></CardHeader>
    <CardContent><ResultsError current={current} onRetry={retry} compact={compact}/>
      {current.error&&!current.data ? null : current.busy&&!current.data ? <Skeleton className="h-20 w-full" /> : <>
        <ResultTotals totals={totals} resultCount={resultCount}/>
        {!compact?<ResultStatus totals={totals}/>:null}
        {results.length ? compact?<ResultEntry row={results[0]} compact/>:<div className="driver-results-list">{results.slice(0,expanded?200:6).map((row,index)=><ResultEntry key={text(row.id)||index} row={row}/>)}</div> : <ResultsEmpty compact={compact}/>}
        {!compact&&results.length>6?<Button variant="outline" size="sm" onClick={()=>setExpanded(value=>!value)}>{expanded?'최근 6개만 보기':`보관 결과 ${results.length}개 보기`}</Button>:null}
        {!compact?<ResultRange coverage={current.data?.coverage} resultCount={results.length}/>:null}
      </>}
    </CardContent><CardFooter className={compact?'driver-results-compact-actions':undefined}>{compact?<><p>{selectedPeriod} · {totals?.official_races>0?`공식 ${number(totals.official_races)}개`:current.error&&!current.data?'조회 확인 필요':'공식 미확인'}{totals?.observation_results>0?` · 관측 ${number(totals.observation_results)}개`:''}</p><DialogTrigger asChild><Button ref={detailsTrigger} variant="outline" size="sm">경기 결과 상세</Button></DialogTrigger></>:<p>보관 결과 수는 공식 경기 참가 횟수와 다릅니다. 우승·포디엄·평균 순위는 공식 최종 결과로만 계산합니다.</p>}</CardFooter></Card>;
  return compact?<Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>{card}<DialogContent className="driver-report-scene driver-results-dialog" onCloseAutoFocus={event=>{event.preventDefault();detailsTrigger.current?.focus();}}>
    <DialogHeader><DialogTitle>보관한 경기 결과 상세</DialogTitle><DialogDescription>{game==='lmu'?'LMU':'iRacing'} · {selectedPeriod} · 공식 최종 결과와 결과 관측을 구분합니다.</DialogDescription></DialogHeader>
    <div className="driver-results-dialog-body"><ResultsError current={current} onRetry={retry}/>{current.error&&!current.data?null:current.busy&&!current.data?<Skeleton className="h-20 w-full"/>:<>
      <ResultTotals totals={totals} resultCount={resultCount}/><ResultStatus totals={totals}/>
      {current.data?.coverage?<section className="driver-results-coverage" aria-label="경기 결과 보관 범위"><h3>보관 범위</h3><p>연결 원본 {number(current.data.coverage.source_count)}개 · 전체 보관 결과 {number(current.data.coverage.result_count)}개 · 날짜 미확인 {number(current.data.coverage.undated_result_count)}개</p><p>선택 기간의 결과 {number(current.data.coverage.period_result_count)}개를 집계합니다. 날짜를 확인하지 못한 결과는 전체 기간에서 볼 수 있습니다.</p></section>:null}
      {results.length?<div className="driver-results-list">{results.slice(0,200).map((row,index)=><ResultEntry key={text(row.id)||index} row={row}/>)}</div>:<ResultsEmpty/>}
      <ResultRange coverage={current.data?.coverage} resultCount={Math.min(results.length,200)}/>
      {current.data?.coverage?.excluded_empty_result_count>0?<p className="driver-journal-note">본인 결과가 없는 이전 관측 {number(current.data.coverage.excluded_empty_result_count)}개는 결과 목록에서 제외했습니다.</p>:null}
      <p className="driver-journal-note">보관 결과 수는 공식 경기 참가 횟수와 다릅니다. 종료 관측도 공식 최종 결과로 확정한 것은 아니며 우승·포디엄·평균 순위에 넣지 않습니다. 날짜는 UTC 기준입니다.</p>
    </>}</div><DialogFooter><DialogClose asChild><Button variant="outline">닫기</Button></DialogClose></DialogFooter>
  </DialogContent></Dialog>:card;
}
