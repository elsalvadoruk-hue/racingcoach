import React, { useId, useRef, useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, TrendingUp } from 'lucide-react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '../components/ui/card';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '../components/ui/empty';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { NativeSelect, NativeSelectOption, NativeSelectOptGroup } from '../components/ui/native-select';

const text = value => typeof value === 'string' ? value : '';
const number = (value, digits=2) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('ko-KR',{maximumFractionDigits:digits}) : '—';
const percentage = value => Number.isFinite(value) ? `${number(value)}%` : '—';
const change = (value,suffix) => typeof value === 'number' && Number.isFinite(value) ? `${value>0?'+':''}${number(value)}${suffix}` : '—';
const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0,10).replaceAll('-','.') : '날짜 미확인';
const period = value => value ? `${date(value.start_date)}–${date(value.end_date)}` : '비교할 기간 확인 중';
const direction = {IMPROVING:'짧아짐',FASTER:'짧아짐',DECLINING:'길어짐',SLOWER:'길어짐',STEADY:'비슷함',STABLE:'비슷함',MAINTAINED:'비슷함',UNCERTAIN:'판단 보류',UNAVAILABLE:'판단 보류'};
const spreadDirection = {NARROWER:'줄어듦',WIDER:'늘어남',MAINTAINED:'비슷함',UNCERTAIN:'판단 보류'};
const retention = {MAINTAINED:'이전 수준을 유지한 관측',CHANGED:'이전 범위와 달라진 관측',UNCERTAIN:'유지 여부 판단 보류'};
const validPeriod = value => value && typeof value === 'object' && Number.isFinite(value.median_lap_s) && value.median_lap_s > 0;
const axisLabels = {brake_steer:'제동·조향',throttle:'스로틀 전개',brake_application:'브레이크 밟기',brake_release:'브레이크 놓기',
  throttle_modulation:'가속 페달 조절',steering_application:'핸들 돌리기',steering_corrections:'핸들 수정',coasting:'페달을 쉬는 구간',shift_consistency:'변속 시점 반복'};
const inputItems = style => Array.isArray(style?.items) ? style.items.filter(item=>item && typeof item==='object')
  .map((item,index)=>({...item,selectionKey:text(item.id)||text(item.axis)||`input-${index}`}))
  .sort((left,right)=>Number(right.status==='REPEATED')-Number(left.status==='REPEATED')) : [];
const measuredInput = item => item.status!=='UNAVAILABLE' && Number.isFinite(item.value);
const inputLabel = item => text(item.short_label)||axisLabels[text(item.axis)||text(item.id)]||text(item.title)||'입력 관측';
const inputValue = item => Number.isFinite(item.value) ? `${number(item.value)}${item.unit==='s'?'초':text(item.unit)}` : '—';
const inputStatus = item => item.status==='REPEATED'?'반복 확인':measuredInput(item)?'관측':'측정 대기';
const pendingSources = style => Number.isInteger(style?.input_coverage?.pending_source_count)&&style.input_coverage.pending_source_count>0?style.input_coverage.pending_source_count:0;
const shortDate = value => typeof value==='string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(5,10).replace('-','.') : '날짜 미확인';
const shortPeriod = row => row ? row.start_date===row.end_date ? shortDate(row.start_date) : `${shortDate(row.start_date)}–${shortDate(row.end_date)}` : '이전 기록 없음';

function ExplanationDetails({ title, children }) {
  const [open,setOpen]=useState(false);
  const contentId=useId();
  return <section className="driver-style-explanation">
    <Button variant="ghost" size="sm" aria-expanded={open} aria-controls={contentId} onClick={()=>setOpen(!open)}>
      {title}<ChevronDown data-icon="inline-end" aria-hidden="true" />
    </Button>
    {open ? <div id={contentId} className="driver-style-explanation-body">{children}</div> : <div id={contentId} hidden />}
  </section>;
}

function InputMeaning({item}) {
  if([item.meaning,item.example,item.calculation,item.interpretation].some(value=>text(value))) return <section className="driver-input-meaning" aria-label={`${inputLabel(item)}의 뜻과 계산 기준`}>
    <h3>{inputLabel(item)}는 무엇인가요?</h3>
    {text(item.meaning)?<p>{item.meaning}</p>:null}
    {text(item.example)?<aside className="driver-input-example"><span>예시로 이해하기</span><p>{item.example}</p></aside>:null}
    <p className="driver-style-detail-caution">{text(item.interpretation)||'차량과 코스·노면에 따라 필요한 조작이 달라집니다. 이 수치만으로 운전의 좋고 나쁨이나 실력을 판단하지 않습니다.'}</p>
    <ExplanationDetails title="계산 조건과 성향 이름 기준">
      {text(item.calculation)?<p>{item.calculation}</p>:text(item.method)?<p>{item.method}</p>:<p>이 항목의 계산 조건을 아직 확인하지 못했습니다.</p>}
      {Array.isArray(item.names)&&item.names.some(value=>text(value))?<p>반복 근거가 충분하면 {item.names.filter(value=>text(value)).join(' 또는 ')} 이름으로 표시합니다. 근거가 부족하면 관측값과 보류 이유를 먼저 확인합니다.</p>:null}
    </ExplanationDetails>
  </section>;
  const axis=text(item.axis)||text(item.id);
  if(axis==='brake_steer') return <section className="driver-input-meaning" aria-label="제동·조향 겹침의 뜻과 계산 기준">
    <h3>브레이크와 핸들을 함께 쓴 시간</h3>
    <p>브레이크를 밟고 있는 동안 핸들도 돌린 시간이 얼마나 겹쳤는지 나타냅니다. 단위는 %이며, 숫자가 클수록 두 조작을 함께 한 시간의 비율이 큽니다.</p>
    <aside className="driver-input-example"><span>예시로 이해하기</span><p>예를 들어 브레이크를 10% 이상 밟은 시간이 2초이고, 그중 핸들도 돌린 시간이 0.5초이면 이 한 번의 겹침은 25%입니다.</p></aside>
    <p className="driver-style-detail-caution">겹침이 크거나 작다는 이유만으로 잘한 운전인지 판단할 수 없습니다. 실제 코너 진입 위치나 트레일 브레이킹의 성공 여부는 이 수치만으로 알 수 없습니다.</p>
    <ExplanationDetails title="계산 조건과 성향 이름 기준">
    <p>시작과 끝을 확인한 제동 중 0.15~30초 동안 이어지고 최대 제동량이 20% 이상인 구간을 셉니다. 제동 중 또는 끝난 뒤 1.5초 안에 핸들을 돌린 흔적이 있어야 합니다.</p>
    <p>핸들을 돌렸다고 보는 기준은 iRacing에서 약 2.9도, LMU에서 한쪽 최대 조향량의 2%입니다. 브레이크 10% 이상인 시간 중 핸들도 이 기준 이상으로 돌린 시간만 겹침으로 계산합니다.</p>
    <p>한 제동에서 20% 이상이면서 0.15초 이상 겹친 입력은 제동 연결형 쪽으로, 겹침이 5% 이하인 입력은 조작 분리형 쪽으로 셉니다. 충분히 반복될 때만 해당 이름을 붙입니다.</p>
    </ExplanationDetails>
  </section>;
  if(axis==='throttle') return <section className="driver-input-meaning" aria-label="스로틀 전개의 뜻과 계산 기준">
    <h3>가속 페달을 더 밟는 데 걸린 시간</h3>
    <p>스로틀은 가속 페달의 입력량을 뜻합니다. 페달을 20% 이하로 밟은 상태에서 80% 이상까지 더 밟는 데 걸린 시간을 초 단위로 나타냅니다.</p>
    <aside className="driver-input-example"><span>예시로 이해하기</span><p>예를 들어 페달 20% 이하에서 80% 이상에 닿기까지 0.6초가 걸렸다면 이 한 번의 전개 시간은 0.6초입니다. 숫자가 작을수록 페달을 더 빨리 깊게 밟았다는 뜻입니다.</p></aside>
    <p className="driver-style-detail-caution">이 시간만으로 차가 얼마나 잘 가속했는지, 접지력을 잘 썼는지 알 수 없습니다. 차량과 노면·코너에 따라 필요한 조작이 달라지므로 짧거나 길다는 이유만으로 좋고 나쁨을 정하지 않습니다.</p>
    <ExplanationDetails title="계산 조건과 성향 이름 기준"><p>시작을 확인할 수 있고 8초 안에 80%에 도달한 구간만 셉니다. 페달 입력이 크게 꺾이지 않은 전개 중 시간 해상도 오차를 빼도 0.6초 이상이면 점진 가속형 쪽으로, 오차를 더해도 0.35초 이하이면 빠른 가속 전개형 쪽으로 셉니다.</p></ExplanationDetails>
  </section>;
  return null;
}

function StyleReadingGuide() {
  return <section className="driver-style-reading-guide" aria-label="성향 수치와 표시 읽는 법">
    <h3>숫자와 표시 읽는 법</h3>
    <p className="driver-style-detail-caution">궁금한 항목을 펼쳐 표본과 계산 기준을 확인하세요.</p>
    <div>
      <ExplanationDetails title="대표 수치"><p>각 항목의 측정값을 랩·주행·날짜별 중앙값으로 정리한 뒤, 날짜와 차량·코스·레이아웃·세션 종류 조합을 같은 비중으로 평균냅니다. 중앙값은 값을 크기순으로 놓았을 때 가운데 값입니다. 오래 달린 파일 하나가 전체 수치를 좌우하지 않게 합니다.</p></ExplanationDetails>
      <ExplanationDetails title="회 · 일 · 관측 · 확인된 독립 세션"><p>회는 각 항목의 측정 조건을 통과한 입력·구간 횟수, 일은 입력을 측정한 날짜 수(UTC), 관측은 입력이 남은 주행 기록 묶음 수입니다. 독립 세션은 실제 세션 식별과 리플레이가 아님을 확인한 주행 수입니다. 세션을 확인하지 못한 기록도 측정값은 표시하며, 날짜 미확인 기록을 날짜 수에 더하지 않습니다.</p></ExplanationDetails>
      <ExplanationDetails title="관측 · 반복 확인"><p>관측은 이번 범위에서 측정한 입력입니다. 반복 확인은 각 조합에서 2일 이상·확인된 독립 세션 3개 이상·세션당 입력 5회 이상을 확보하고, 날짜와 조합의 비중을 맞춘 같은 양상이 70% 이상일 때 붙입니다. 확인된 세션에서도 같은 방향을 확인합니다. 성향 이름은 기량 점수가 아닙니다.</p></ExplanationDetails>
      <ExplanationDetails title="차량·코스와 관측 범위"><p>함께 적힌 차량·코스·레이아웃·세션 종류에서 나타난 입력입니다. 연료·날씨·타이어·셋업까지 같은 조건이라는 뜻은 아니며, 다른 조합에서도 항상 같은 방식으로 운전한다고 확대하지 않습니다.</p></ExplanationDetails>
      <ExplanationDetails title="관측 주기 · 시간 해상도"><p>Hz는 1초에 입력을 몇 번 기록했는지 나타냅니다. 시간 해상도는 원본 표본 두 칸에 해당하는 시간이며, 60Hz라면 약 0.033초입니다. LMU는 보간으로 늘린 점 대신 원본 채널 주기를 사용합니다. 이보다 작은 시간 차이는 구분하기 어렵습니다.</p></ExplanationDetails>
      <ExplanationDetails title="다른 양상"><p>함께 관측된 반대쪽 입력 분류의 횟수와 비중입니다. 비중은 날짜와 조합을 같은 비중으로 정리하므로, 표시 횟수를 전체 횟수로 나눈 값과 다를 수 있습니다. 반대 양상이 있다는 이유만으로 실수라고 판정하지 않습니다.</p></ExplanationDetails>
      <ExplanationDetails title="어떤 구간을 측정하나요?"><p>분석에서 측정 가능한 완주 랩 중 트랙 위에서 시속 18km를 초과해 움직이는 구간을 사용합니다. 피트·확인된 리플레이·끊긴 시간축·부족한 입력은 제외하고 원본 주기 10Hz 이상을 요구합니다. 리플레이 여부가 미확인인 자료는 부분 관측으로 남깁니다. 공식 유효 랩이나 실력 향상을 확정하는 판정과는 별개입니다.</p></ExplanationDetails>
    </div>
  </section>;
}

function InsightDetails({ title, children }) {
  const [open,setOpen]=useState(false);
  const triggerRef=useRef(null);
  return <>
    <Button ref={triggerRef} size="sm" variant="outline" aria-label={title} aria-haspopup="dialog" aria-expanded={open} onClick={()=>setOpen(true)}>자세히</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="driver-report-scene driver-insight-dialog" onCloseAutoFocus={event=>{event.preventDefault();triggerRef.current?.focus();}}>
        <DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription>관측 수치의 범위와 표본·방법·보류 이유를 확인합니다.</DialogDescription></DialogHeader>
        <div className="driver-insight-dialog-body">{children}</div>
        <DialogFooter><DialogClose asChild><Button variant="outline">닫기</Button></DialogClose></DialogFooter>
      </DialogContent>
    </Dialog>
  </>;
}

function CompactStyleCard({style}) {
  const items=inputItems(style);
  const measured=items.filter(measuredInput);
  const [page,setPage]=useState(0);
  const pageCount=Math.max(1,Math.ceil(measured.length/3));
  const currentPage=Math.min(page,pageCount-1);
  const visible=measured.slice(currentPage*3,currentPage*3+3);
  const scope=Array.from(new Set(visible.map(item=>text(item.scope)).filter(Boolean))).join(' / ');
  return <Card className="driver-report-card driver-report-style driver-insight-compact" aria-label="운전 성향 관측">
    <CardHeader><div className="driver-insight-compact-heading"><CardTitle asChild><h2>나의 운전 성향</h2></CardTitle><span className="driver-style-kind-count">측정 {measured.length} / 전체 {items.length}종</span></div></CardHeader>
    <CardContent className="driver-insight-compact-body">
      {measured.length ? <div className="driver-style-compact-items" aria-label="현재 페이지의 성향 항목">{visible.map(item=><article className="driver-style-compact-item" key={item.selectionKey}>
        <strong title={`${inputLabel(item)} · ${text(item.title)} · 입력 ${number(item.event_count,0)}회`}>{text(item.title)||inputLabel(item)}</strong>
        <span className="numeric">{inputValue(item)}</span>
        <Badge variant="secondary">{inputStatus(item)}</Badge>
      </article>)}</div> : <p className="driver-insight-compact-note">입력을 측정할 수 있는 기록을 확인하고 있어요.</p>}
      {pendingSources(style)?<p className="driver-insight-compact-scope" title={text(style.input_coverage.reason)}>기존 기록 {pendingSources(style)}개 보완 대기</p>:scope ? <p className="driver-insight-compact-scope" title={scope}>{scope}</p> : null}
    </CardContent>
    <CardFooter className="driver-insight-compact-footer"><div className="driver-style-page-controls" role="group" aria-label="성향 항목 페이지">
      <Button size="sm" variant="outline" aria-label="이전 성향 3개" disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}><ChevronLeft data-icon="inline-start" aria-hidden="true"/></Button>
      <span aria-live="polite">{measured.length?`${currentPage*3+1}–${Math.min((currentPage+1)*3,measured.length)} / ${measured.length}종`:'측정 대기'}</span>
      <Button size="sm" variant="outline" aria-label="다음 성향 3개" disabled={currentPage===pageCount-1} onClick={()=>setPage(currentPage+1)}><ChevronRight data-icon="inline-start" aria-hidden="true"/></Button>
    </div>
      <InsightDetails title="운전 성향 자세히"><DriverStyleCard style={style} /></InsightDetails>
    </CardFooter>
  </Card>;
}

function CompactGrowthCard({growth}) {
  const previous=validPeriod(growth?.previous)?growth.previous:null,current=validPeriod(growth?.current)?growth.current:null;
  const available=previous && current && Number.isFinite(growth?.pace_change_pct)
    && ['CONDITION_MATCHED','OBSERVATION_ONLY'].includes(growth?.comparison_status);
  const strength=Array.isArray(growth?.strengths) ? growth.strengths.find(item=>item && text(item.title)) : null;
  const comparison=available ? growth.comparison_status==='CONDITION_MATCHED'?'조건 일치':'조건 차이 포함' : '비교 보류';
  const scope=text(growth?.scope);
  const note=available ? text(strength?.title)||retention[growth?.retention]||'유지 여부 판단 보류' : current?'이전 기록과 비교 근거 확인 중':'측정 기록을 확인하고 있어요';
  return <Card className="driver-report-growth driver-insight-compact" aria-label="이전과 최근 주행 비교">
    <CardHeader><div className="driver-insight-compact-heading"><CardTitle className="driver-insight-compact-title" asChild><h2>주행 변화 비교</h2></CardTitle><Badge variant="secondary">{comparison}</Badge></div></CardHeader>
    <CardContent className="driver-insight-compact-body">
      <table className="driver-growth-compact-periods" aria-label="전후 랩 중앙값과 편차"><thead><tr><th scope="col">시기</th><th scope="col">랩 중앙값</th><th scope="col">편차</th><th scope="col">표본</th></tr></thead>
        <tbody>{[['이전',previous],['최근',current]].map(([label,row])=><tr key={label}><th scope="row" title={row?period(row):'해당 시기의 측정 기록 없음'}>{label}</th>
          <td className="numeric">{row?`${number(row.median_lap_s,3)}초`:'—'}</td><td className="numeric">{percentage(row?.mad_pct)}</td><td>{row?`${number(row.lap_count,0)}랩`:'—'}</td></tr>)}</tbody>
      </table>
      {available ? <div className="driver-growth-compact-changes"><span>페이스 {change(growth.pace_change_pct,'%')}</span><span>편차 {change(growth.dispersion_change_pp,'%p')}</span></div> : null}
      {scope ? <p className="driver-insight-compact-scope" title={scope}>{scope}</p> : null}
      {previous || current ? <p className="driver-growth-compact-range" title={`${previous?period(previous):'이전 기록 없음'} → ${current?period(current):'최근 기록 없음'}`}>{previous?shortPeriod(previous):'이전 기록 없음'} → {current?shortPeriod(current):'최근 기록 없음'}</p> : null}
    </CardContent>
    <CardFooter className="driver-insight-compact-footer"><p className="driver-insight-compact-note" title={note}>{note}</p>
      <InsightDetails title="주행 변화 자세히"><DriverGrowthCard growth={growth} /></InsightDetails>
    </CardFooter>
  </Card>;
}

export function DriverStyleCard({style, compact=false}) {
  if(compact) return <CompactStyleCard style={style} />;
  return <StyleDetailsTabs style={style} />;
}

function StyleDetailsTabs({style}) {
  const items=inputItems(style);
  const measured=items.filter(measuredInput), unavailable=items.filter(item=>!measuredInput(item));
  const [selected,setSelected]=useState('');
  const [tab,setTab]=useState(items.length?'input':'guide');
  const item=items.find(row=>row.selectionKey===selected)||measured[0]||items[0];
  return <section className="driver-style-details" aria-label="운전 성향 관측">
    {!items.length ? <Empty><EmptyHeader><EmptyTitle>아직은 관찰 중이에요</EmptyTitle><EmptyDescription>{text(style?.reason)||'조작을 설명할 수 있는 입력과 반복 표본을 확인하고 있습니다.'}</EmptyDescription></EmptyHeader></Empty> : null}
    {pendingSources(style)?<section className="driver-style-coverage-note" role="status"><strong>기존 기록 {pendingSources(style)}개 보완 대기</strong><p>{text(style.input_coverage.reason)||'기존 기록의 새 성향 항목을 백그라운드에서 확인하고 있습니다.'}</p></section>:null}
    <Tabs value={items.length?tab:'guide'} onValueChange={setTab}>
      <TabsList aria-label="성향 설명 항목" className="driver-style-detail-tabs">
        {items.length?<TabsTrigger value="input">항목 설명 · 전체 {items.length}종</TabsTrigger>:null}
        <TabsTrigger value="guide">수치 읽는 법</TabsTrigger>
      </TabsList>
      {item?<TabsContent value="input" className="driver-style-detail-panel">
        <label className="driver-style-item-selector"><span>설명할 성향 항목</span><NativeSelect size="sm" aria-label="설명할 성향 항목" value={item.selectionKey} onChange={event=>setSelected(event.target.value)}>
          {measured.length?<NativeSelectOptGroup label="측정한 항목">{measured.map(row=><NativeSelectOption key={row.selectionKey} value={row.selectionKey}>{inputLabel(row)} · {inputStatus(row)}</NativeSelectOption>)}</NativeSelectOptGroup>:null}
          {unavailable.length?<NativeSelectOptGroup label="측정을 기다리는 항목">{unavailable.map(row=><NativeSelectOption key={row.selectionKey} value={row.selectionKey}>{inputLabel(row)} · 측정 대기</NativeSelectOption>)}</NativeSelectOptGroup>:null}
        </NativeSelect></label>
        <div className="driver-style-selected-item" key={item.selectionKey}>
        <div className="driver-style-detail-heading"><h3>{text(item.title)||inputLabel(item)}</h3><Badge variant="secondary">{inputStatus(item)}</Badge></div>
        {!measuredInput(item)?<p className="driver-style-unavailable" role="status">{text(item.reason)||'이 항목에 필요한 입력이나 표본을 아직 확인하지 못했습니다.'}</p>:null}
        <div className="driver-style-detail-summary"><div><span>대표 수치</span><strong className="numeric">{inputValue(item)}</strong></div>
          <dl>{[[item.axis==='coasting'?'관측 구간':'입력 횟수',`${number(item.event_count,0)}${item.axis==='coasting'?'개':'회'}`],['관측일',`${number(item.day_count,0)}일`],['관측 기록',`${number(item.observation_count,0)}개`],['확인된 독립 세션',`${number(item.session_count,0)}개`]].map(([label,amount])=><div key={label}><dt>{label}</dt><dd>{amount}</dd></div>)}</dl>
        </div>
        {text(item.scope)?<p className="driver-style-detail-caution">관측 범위 · {item.scope}</p>:null}
        <InputMeaning item={item}/>
        <ExplanationDetails title="이번 수치의 측정 근거">
          {text(item.reason)?<p>{item.reason}</p>:null}
          {text(item.method)?<p>{item.method}</p>:null}
          {Number.isFinite(item.sampling_hz)&&Number.isFinite(item.timing_uncertainty_s)?<p>관측 주기 {number(item.sampling_hz,0)}Hz 이상 · 시간 해상도 약 {number(item.timing_uncertainty_s,3)}초</p>:null}
          {Number.isFinite(item.counter_evidence?.share_pct)&&item.counter_evidence.event_count>0?<p>다른 양상도 {number(item.counter_evidence.event_count,0)}회 · {number(item.counter_evidence.share_pct,1)}% 관측됐습니다.</p>:null}
        </ExplanationDetails>
        </div>
      </TabsContent>:null}
      <TabsContent value="guide" className="driver-style-detail-panel"><StyleReadingGuide/></TabsContent>
    </Tabs>
    {items.length&&text(style?.reason)?<p className="driver-style-detail-caution">{style.reason}</p>:null}
  </section>;
}

export function DriverGrowthCard({growth, compact=false}) {
  if(compact) return <CompactGrowthCard growth={growth} />;
  const previous=validPeriod(growth?.previous)?growth.previous:null,current=validPeriod(growth?.current)?growth.current:null;
  const available = previous && current && Number.isFinite(growth?.pace_change_pct)
    && ['CONDITION_MATCHED','OBSERVATION_ONLY'].includes(growth?.comparison_status);
  const reasons = Array.isArray(growth?.uncertainty?.reasons) ? growth.uncertainty.reasons.filter(value=>typeof value==='string') : [];
  const strengths = Array.isArray(growth?.strengths) ? growth.strengths.filter(value=>value && typeof value==='object').slice(0,2) : [];
  return <Card className="driver-report-growth" aria-label="이전과 최근 주행 비교"><CardHeader><div className="driver-report-eyebrow"><TrendingUp aria-hidden="true" />이전 기록과 지금</div><CardTitle asChild><h2>주행 변화 비교</h2></CardTitle><CardDescription>{text(growth?.scope)||'같은 차량·코스의 이전 기록과 최근 기록을 비교합니다.'}</CardDescription></CardHeader>
    <CardContent>
      {previous || current ? <div className="driver-growth-periods">{[['이전',previous],['최근',current]].map(([label,row])=><section key={label} aria-label={`${label} 비교 범위`}><h3>{label}</h3>{row ? <><p>{period(row)}</p><dl><div><dt>측정 랩</dt><dd>{number(row.lap_count,0)}랩 · {number(row.day_count,0)}일</dd></div><div><dt>관측 범위</dt><dd>관측 {number(row.observation_count,0)}개 · 확인된 세션 {number(row.session_count,0)}개</dd></div><div><dt>랩 중앙값</dt><dd>{number(row.median_lap_s,3)}초</dd></div><div><dt>랩 편차</dt><dd>{percentage(row.mad_pct)}</dd></div></dl>{row.lap_count<2?<p className="driver-journal-note">랩 간 편차는 측정 랩 2개부터 표시합니다.</p>:null}</> : <p>이 시기의 측정 기록을 아직 확인하지 못했습니다.</p>}</section>)}</div> : null}
      {available ? <>
        <div className="driver-growth-changes"><p>페이스 {change(growth.pace_change_pct,'%')} · {direction[growth.pace_direction]||'판단 보류'}</p><p>편차 {change(growth.dispersion_change_pp,'%p')} · {spreadDirection[growth.dispersion_direction]||'판단 보류'}</p><p>{retention[growth.retention]||'유지 여부 판단 보류'}</p></div>
        <Badge variant="secondary">{growth.comparison_status==='CONDITION_MATCHED'?'조건을 확인한 비교':'조건 차이가 포함된 관측 비교'}</Badge>
        {growth.comparison_window==='SPLIT_OBSERVED_DAYS' ? <p className="driver-journal-note">선택 범위의 관측일을 앞·뒤로 나눈 비교</p> : growth.comparison_window==='ADJACENT_PERIODS' ? <p className="driver-journal-note">직전 같은 길이의 기간과 비교</p> : null}
      </> : <p>{reasons[0] || '측정 가능한 기록이 두 시기에 쌓이면 같은 조합의 변화를 보여드립니다.'}</p>}
      {reasons.length > (available?0:1) ? <ul className="driver-growth-reasons">{reasons.slice(available?0:1,3).map((reason,index)=><li key={`${index}:${reason}`}>{reason}</li>)}</ul> : null}
      {available && strengths.length ? <section aria-label="별도로 확인한 관측 결과"><h3>확인한 결과</h3>{strengths.map((item,index)=><div key={text(item.id)||index}><strong>{text(item.title)}</strong><p>{text(item.reason)}</p></div>)}</section> : null}
      {text(growth?.reason) ? <p className="driver-journal-note">{growth.reason}</p> : null}
    </CardContent><CardFooter><p>입력 방식의 변화와 페이스·안정성의 변화는 각각 읽습니다.</p></CardFooter></Card>;
}
