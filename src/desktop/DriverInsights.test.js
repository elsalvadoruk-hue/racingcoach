import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import userEvent from '@testing-library/user-event';
import { DriverStyleCard, DriverGrowthCard } from './DriverInsights';

const measuredPeriod = { start_date:'2026-10-20',end_date:'2026-10-21',day_count:2,
  observation_count:2,session_count:0,lap_count:6,median_lap_s:100,mad_pct:.5 };
const growth = {status:'PARTIAL',comparison_status:'OBSERVATION_ONLY',scope:'GT3 · GP · 연습',
  previous:{...measuredPeriod},current:{...measuredPeriod,start_date:'2026-10-22',end_date:'2026-10-23',mad_pct:.1},
  pace_change_pct:0,dispersion_change_pp:-.4,pace_direction:'MAINTAINED',dispersion_direction:'NARROWER',
  retention:'MAINTAINED',comparison_window:'SPLIT_OBSERVED_DAYS',
  uncertainty:{reasons:['연료·날씨 조건은 확인되지 않았습니다.'],sample_size_sufficient:true},
  reason:'관측 변화의 원인이나 실력 향상을 확정하지 않습니다.',strengths:[]};
const input = {id:'brake_steer',axis:'brake_steer',title:'제동·조향 겹침 관측',status:'OBSERVATION_ONLY',
  value:0,unit:'%',event_count:6,observation_count:2,session_count:0,day_count:2,scope:'GT3 · GP',
  reason:'확인한 입력 수치를 먼저 표시합니다.',method:'입력이 겹친 시간 비율입니다.',
  counter_evidence:{event_count:0,share_pct:0}};

test('input facts keep zero, observation and session samples, and method without a persistent name',()=>{
  render(<DriverStyleCard style={{status:'PARTIAL',items:[input],reason:'성향과 빠르기는 별도입니다.'}} />);
  expect(screen.getByText('제동·조향 겹침 관측')).toBeInTheDocument();
  expect(screen.getAllByText('0%')).toHaveLength(1);
  expect(screen.getAllByRole('definition')[2]).toHaveTextContent('2개');
  expect(screen.getAllByRole('definition')[3]).toHaveTextContent('0개');
  expect(screen.queryByText('입력이 겹친 시간 비율입니다.')).not.toBeInTheDocument();
  userEvent.click(screen.getByRole('button',{name:'이번 수치의 측정 근거'}));
  expect(screen.getByText('입력이 겹친 시간 비율입니다.')).toBeInTheDocument();
  expect(screen.queryByText('반복 확인')).not.toBeInTheDocument();
});

test('repeated inputs show opposing evidence and keep every supplied axis accessible',()=>{
  render(<DriverStyleCard style={{items:[{...input,status:'REPEATED',title:'제동 연결형',
    counter_evidence:{event_count:3,share_pct:20,dates:['2026-10-23']}},
    {...input,id:'throttle',axis:'throttle',title:'점진 가속형',status:'REPEATED'},
    {...input,id:'excluded',title:'세 번째 축'}]}} />);
  expect(screen.getAllByText('반복 확인')).toHaveLength(1);
  userEvent.click(screen.getByRole('button',{name:'이번 수치의 측정 근거'}));
  expect(screen.getByText(/다른 양상도 3회 · 20%/)).toBeInTheDocument();
  userEvent.selectOptions(screen.getByRole('combobox',{name:'설명할 성향 항목'}),'throttle');
  expect(screen.getByRole('heading',{name:'점진 가속형'})).toBeInTheDocument();
  userEvent.selectOptions(screen.getByRole('combobox',{name:'설명할 성향 항목'}),'excluded');
  expect(screen.getByRole('heading',{name:'세 번째 축'})).toBeInTheDocument();
});

test('missing style uses its actual missing evidence explanation',()=>{
  render(<DriverStyleCard style={{status:'UNAVAILABLE',items:[],reason:'조향 채널이 없습니다.'}} />);
  expect(screen.getByText('조향 채널이 없습니다.')).toBeInTheDocument();
  expect(screen.queryByText(/NaN|undefined/)).not.toBeInTheDocument();
});

test('growth preserves measured periods, zero change, directions and observation uncertainty',()=>{
  render(<DriverGrowthCard growth={growth} />);
  expect(screen.getByText('페이스 0% · 비슷함')).toBeInTheDocument();
  expect(screen.getByText('편차 -0.4%p · 줄어듦')).toBeInTheDocument();
  expect(screen.getByText('이전 수준을 유지한 관측')).toBeInTheDocument();
  expect(screen.getByText('조건 차이가 포함된 관측 비교')).toBeInTheDocument();
  expect(screen.getByText('선택 범위의 관측일을 앞·뒤로 나눈 비교')).toBeInTheDocument();
  expect(within(screen.getByRole('region',{name:'이전 비교 범위'})).getByText('100초')).toBeInTheDocument();
  expect(screen.getByText('연료·날씨 조건은 확인되지 않았습니다.')).toBeInTheDocument();
  expect(screen.getByText(growth.reason)).toBeInTheDocument();
});

test('a new combination still shows current measurements while its comparison is withheld',()=>{
  render(<DriverGrowthCard growth={{...growth,status:'UNAVAILABLE',comparison_status:'UNAVAILABLE',previous:null,
    pace_change_pct:null,uncertainty:{reasons:['이 조합의 이전 기록이 없습니다.']}}} />);
  expect(within(screen.getByRole('region',{name:'최근 비교 범위'})).getByText('100초')).toBeInTheDocument();
  expect(screen.getByText('이 조합의 이전 기록이 없습니다.')).toBeInTheDocument();
  expect(screen.queryByText('조건을 확인한 비교')).not.toBeInTheDocument();
  expect(screen.queryByText('페이스 0% · 비슷함')).not.toBeInTheDocument();
});

test('a one-lap period keeps its measured pace while withholding dispersion in compact and detailed views',async()=>{
  render(<DriverGrowthCard compact growth={{...growth,current:{...growth.current,lap_count:1,mad_pct:null}}}/>);
  const table=screen.getByRole('table',{name:'전후 랩 중앙값과 편차'});
  expect(within(table).getByRole('cell',{name:'—',exact:true})).toBeInTheDocument();
  expect(within(table).getByText('0.5%')).toBeInTheDocument();
  expect(screen.queryByText('—%')).not.toBeInTheDocument();
  userEvent.click(screen.getByRole('button',{name:'주행 변화 자세히'}));
  const dialog=await screen.findByRole('dialog',{name:'주행 변화 자세히'});
  const current=within(dialog).getByRole('region',{name:'최근 비교 범위'});
  expect(within(current).getByText('100초')).toBeInTheDocument();
  expect(within(current).getByText('—')).toBeInTheDocument();
  expect(within(current).queryByText('—%')).not.toBeInTheDocument();
  expect(within(current).getByText('랩 간 편차는 측정 랩 2개부터 표시합니다.')).toBeInTheDocument();
});

test('confirmed comparison keeps observed result separate from input style',()=>{
  render(<DriverGrowthCard growth={{...growth,comparison_status:'CONDITION_MATCHED',pace_direction:'UNCERTAIN',
    dispersion_direction:'MAINTAINED',retention:'UNCERTAIN',strengths:[{id:'PACE_REPRODUCTION',
      title:'이 조합에서 페이스와 편차를 유지한 관측',reason:'조작 성향과 별도입니다.'}]}} />);
  expect(screen.getByText('페이스 0% · 판단 보류')).toBeInTheDocument();
  expect(screen.getByText('유지 여부 판단 보류')).toBeInTheDocument();
  expect(screen.getByText('조건을 확인한 비교')).toBeInTheDocument();
  expect(screen.getByText('이 조합에서 페이스와 편차를 유지한 관측')).toBeInTheDocument();
  expect(screen.getByText('조작 성향과 별도입니다.')).toBeInTheDocument();
});

test('malformed or absent growth never claims verified conditions or prints invalid numbers',()=>{
  const view=render(<DriverGrowthCard />);
  expect(screen.queryByText(/NaN|undefined/)).not.toBeInTheDocument();
  view.rerender(<DriverGrowthCard growth={{...growth,comparison_status:'UNKNOWN',pace_change_pct:Infinity,
    uncertainty:{reasons:[null,3,'표본 확인 중']}}} />);
  expect(screen.getByText('표본 확인 중')).toBeInTheDocument();
  expect(screen.queryByText('조건을 확인한 비교')).not.toBeInTheDocument();
  expect(screen.queryByText(/Infinity|NaN|undefined/)).not.toBeInTheDocument();
});

test('compact style keeps measured values and repeat states while details restore focus',async()=>{
  render(<DriverStyleCard compact style={{items:[{...input,status:'REPEATED',title:'제동 연결형'},
    {...input,id:'throttle',axis:'throttle',title:'스로틀 전개 관측',value:.65,unit:'s'},
    {...input,id:'excluded',axis:'excluded',title:'세 번째 축'}],reason:'입력 방식과 빠르기는 별도입니다.'}} />);
  expect(screen.getAllByRole('button',{name:/ · 0% ·/})).toHaveLength(2);
  expect(screen.getByRole('button',{name:/ · 0.65초 ·/})).toBeInTheDocument();
  expect(screen.getByText('측정 3 / 전체 3종')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/ · 반복 확인 ·/})).toBeInTheDocument();
  expect(screen.queryByText('관측')).not.toBeInTheDocument();
  expect(screen.getByText('GT3 · GP')).toBeInTheDocument();
  expect(screen.getByText('세 번째 축')).toBeInTheDocument();
  expect(screen.queryByText(input.method)).not.toBeInTheDocument();
  const trigger=screen.getByRole('button',{name:'운전 성향 자세히'});
  userEvent.click(trigger);
  const dialog=await screen.findByRole('dialog',{name:'운전 성향 자세히'});
  expect(within(dialog).queryByText(input.method)).not.toBeInTheDocument();
  userEvent.click(within(dialog).getByRole('button',{name:'이번 수치의 측정 근거'}));
  expect(within(dialog).getByText(input.method)).toBeInTheDocument();
  userEvent.selectOptions(within(dialog).getByRole('combobox',{name:'설명할 성향 항목'}),'throttle');
  expect(within(dialog).queryByText(input.method)).not.toBeInTheDocument();
  userEvent.click(within(dialog).getByRole('button',{name:'이번 수치의 측정 근거'}));
  expect(within(dialog).getByText(input.method)).toBeInTheDocument();
  expect(within(dialog).getByText('입력 방식과 빠르기는 별도입니다.')).toBeInTheDocument();
  userEvent.click(within(dialog).getAllByRole('button',{name:'닫기',exact:true})[0]);
  await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await waitFor(()=>expect(trigger).toHaveFocus());
});

test('compact growth exposes pre/post pace, dispersion, changes, comparison type and strength',async()=>{
  render(<DriverGrowthCard compact growth={{...growth,comparison_status:'CONDITION_MATCHED',strengths:[{
    id:'PACE_REPRODUCTION',title:'이 조합에서 페이스와 편차를 유지한 관측',reason:'조작 성향과 별도입니다.'}]}} />);
  const table=screen.getByRole('table',{name:'전후 랩 중앙값과 편차'});
  expect(within(table).getAllByText('100초')).toHaveLength(2);
  expect(within(table).getByText('0.5%')).toBeInTheDocument();
  expect(within(table).getByText('0.1%')).toBeInTheDocument();
  expect(screen.getByText('페이스 0%')).toBeInTheDocument();
  expect(screen.getByText('편차 -0.4%p')).toBeInTheDocument();
  expect(screen.getByText('조건 일치')).toBeInTheDocument();
  expect(screen.getByText('이 조합에서 페이스와 편차를 유지한 관측')).toBeInTheDocument();
  expect(screen.getByText('GT3 · GP · 연습')).toBeInTheDocument();
  expect(screen.queryByText(growth.reason)).not.toBeInTheDocument();
  const trigger=screen.getByRole('button',{name:'주행 변화 자세히'});
  userEvent.click(trigger);
  const dialog=await screen.findByRole('dialog',{name:'주행 변화 자세히'});
  expect(within(dialog).getByText(growth.reason)).toBeInTheDocument();
  expect(within(dialog).getByText('2026.10.20–2026.10.21')).toBeInTheDocument();
  userEvent.keyboard('{Escape}');
  await waitFor(()=>expect(trigger).toHaveFocus());
});

test('compact observation comparison and current-only measurements never imply matched conditions',()=>{
  const view=render(<DriverGrowthCard compact growth={growth} />);
  expect(screen.getByText('조건 차이 포함')).toBeInTheDocument();
  expect(screen.queryByText('조건 일치')).not.toBeInTheDocument();
  view.rerender(<DriverGrowthCard compact growth={{...growth,status:'UNAVAILABLE',comparison_status:'UNAVAILABLE',
    previous:null,pace_change_pct:null}} />);
  expect(screen.getByText('100초')).toBeInTheDocument();
  expect(screen.getByText('비교 보류')).toBeInTheDocument();
  expect(screen.queryByText('페이스 0%')).not.toBeInTheDocument();
});

test('compact empty cards keep details available without fabricating measurements',async()=>{
  const view=render(<DriverStyleCard compact style={{items:[],reason:'조향 채널이 없습니다.'}} />);
  expect(screen.queryByText('조향 채널이 없습니다.')).not.toBeInTheDocument();
  userEvent.click(screen.getByRole('button',{name:'운전 성향 자세히'}));
  const dialog=await screen.findByRole('dialog',{name:'운전 성향 자세히'});
  expect(within(dialog).getByText('조향 채널이 없습니다.')).toBeInTheDocument();
  userEvent.click(within(dialog).getAllByRole('button',{name:'닫기',exact:true})[0]);
  await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  view.rerender(<DriverGrowthCard compact />);
  expect(screen.getByText('비교 보류')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'주행 변화 자세히'})).toBeEnabled();
  expect(screen.queryByText(/NaN|Infinity|undefined/)).not.toBeInTheDocument();
});

test('style details explain both input measurements in plain language with units, examples and actual bounds',async()=>{
  render(<DriverStyleCard compact style={{items:[input,{...input,id:'throttle',axis:'throttle',
    title:'스로틀 전개 관측',value:.65,unit:'s'}]}}/>);
  expect(screen.queryByRole('region',{name:'제동·조향 겹침의 뜻과 계산 기준'})).not.toBeInTheDocument();
  userEvent.click(screen.getByRole('button',{name:'운전 성향 자세히'}));
  const dialog=await screen.findByRole('dialog',{name:'운전 성향 자세히'});
  const brake=within(dialog).getByRole('region',{name:'제동·조향 겹침의 뜻과 계산 기준'});
  expect(within(brake).getByRole('heading',{name:'브레이크와 핸들을 함께 쓴 시간'})).toBeInTheDocument();
  expect(within(brake).getByText(/단위는 %/)).toBeInTheDocument();
  expect(within(brake).getByText(/2초.*0\.5초.*25%/)).toBeInTheDocument();
  const calculation=within(brake).getByRole('button',{name:'계산 조건과 성향 이름 기준'});
  expect(calculation).toHaveAttribute('aria-expanded','false');
  expect(within(brake).queryByText(/0\.15~30초/)).not.toBeInTheDocument();
  userEvent.click(calculation);
  expect(calculation).toHaveAttribute('aria-expanded','true');
  expect(within(brake).getByText(/0\.15~30초.*20%.*1\.5초/)).toBeInTheDocument();
  expect(within(brake).getByText(/iRacing.*2\.9도.*LMU.*2%/)).toBeInTheDocument();
  expect(within(brake).getByText(/20%.*0\.15초.*5%/)).toBeInTheDocument();
  expect(within(brake).getByText(/코너 진입 위치나 트레일 브레이킹의 성공 여부/)).toBeInTheDocument();
  expect(within(dialog).queryByRole('region',{name:'스로틀 전개의 뜻과 계산 기준'})).not.toBeInTheDocument();
  userEvent.selectOptions(within(dialog).getByRole('combobox',{name:'설명할 성향 항목'}),'throttle');
  expect(within(dialog).queryByRole('region',{name:'제동·조향 겹침의 뜻과 계산 기준'})).not.toBeInTheDocument();
  const throttle=within(dialog).getByRole('region',{name:'스로틀 전개의 뜻과 계산 기준'});
  expect(within(throttle).getByRole('heading',{name:'가속 페달을 더 밟는 데 걸린 시간'})).toBeInTheDocument();
  expect(within(throttle).getByText(/스로틀은 가속 페달.*20% 이하.*80% 이상.*초 단위/)).toBeInTheDocument();
  expect(within(throttle).getByText(/0\.6초.*숫자가 작을수록/)).toBeInTheDocument();
  userEvent.click(within(throttle).getByRole('button',{name:'계산 조건과 성향 이름 기준'}));
  expect(within(throttle).getByText(/8초.*오차를 빼도 0\.6초.*오차를 더해도 0\.35초/)).toBeInTheDocument();
  expect(within(throttle).getByText(/좋고 나쁨을 정하지 않습니다/)).toBeInTheDocument();
  userEvent.keyboard('{Escape}');
  await waitFor(()=>expect(screen.getByRole('button',{name:'운전 성향 자세히'})).toHaveFocus());
});

test('style reading guide explains record scope, equal weighting, repeat evidence and native timing precision',()=>{
  render(<DriverStyleCard style={{items:[{...input,status:'REPEATED',title:'제동 연결형'},
    {...input,id:'throttle',axis:'throttle',status:'REPEATED',title:'점진 가속형',unit:'s'}]}}/>);
  expect(screen.getByRole('region',{name:'제동·조향 겹침의 뜻과 계산 기준'})).toBeInTheDocument();
  expect(screen.queryByRole('region',{name:'스로틀 전개의 뜻과 계산 기준'})).not.toBeInTheDocument();
  userEvent.click(screen.getByRole('tab',{name:'수치 읽는 법'}));
  const guide=screen.getByRole('region',{name:'성향 수치와 표시 읽는 법'});
  within(guide).getAllByRole('button').forEach(button=>{
    expect(button).toHaveAttribute('aria-expanded','false');
    userEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded','true');
  });
  expect(within(guide).getByText(/랩·주행·날짜별 중앙값.*같은 비중으로 평균/)).toBeInTheDocument();
  expect(within(guide).getByText(/세션을 확인하지 못한 기록도 측정값은 표시/)).toBeInTheDocument();
  expect(within(guide).getByText(/2일 이상.*3개 이상.*5회 이상.*70%/)).toBeInTheDocument();
  expect(within(guide).getByText(/연료·날씨·타이어·셋업까지 같은 조건이라는 뜻은 아니며/)).toBeInTheDocument();
  expect(within(guide).getByText(/60Hz.*0\.033초.*LMU.*원본 채널 주기/)).toBeInTheDocument();
  expect(within(guide).getByText(/시속 18km.*10Hz.*부분 관측/)).toBeInTheDocument();
});

test('unknown input types and missing measurements retain general guidance without borrowing an axis explanation',()=>{
  const view=render(<DriverStyleCard style={{items:[{...input,id:'future',axis:'future',title:'다른 입력'}]}}/>);
  expect(screen.queryByRole('region',{name:'제동·조향 겹침의 뜻과 계산 기준'})).not.toBeInTheDocument();
  expect(screen.queryByRole('region',{name:'스로틀 전개의 뜻과 계산 기준'})).not.toBeInTheDocument();
  view.rerender(<DriverStyleCard style={{items:[],reason:'측정 가능한 입력이 없습니다.'}}/>);
  expect(screen.getByText('측정 가능한 입력이 없습니다.')).toBeInTheDocument();
  expect(screen.getByRole('region',{name:'성향 수치와 표시 읽는 법'})).toBeInTheDocument();
  expect(screen.queryByText('0%')).not.toBeInTheDocument();
});

const styleAxes=['brake_steer','throttle','brake_application','brake_release','throttle_modulation',
  'steering_application','steering_corrections','coasting','shift_consistency'];
const fullStyle=()=>({items:styleAxes.map((axis,index)=>({...input,id:axis,axis,title:`조작 관측 ${index+1}`,
  short_label:`조작 종류 ${index+1}`,value:index,meaning:`입력 의미 ${index+1}`,example:`입력 예시 ${index+1}`,
  calculation:`입력 계산 조건 ${index+1}`,interpretation:`입력 해석 한계 ${index+1}`,names:[`성향 A${index+1}`,`성향 B${index+1}`]}))});

test('all nine axes stay in three labeled groups with scope range and stable order when evidence changes',()=>{
  const style=fullStyle();style.items[7].status='REPEATED';
  style.items=style.items.map((item,index)=>({...item,scope_count:index===8?44:61}));
  const view=render(<DriverStyleCard compact style={style}/>);
  expect(screen.getByText('측정 9 / 전체 9종')).toBeInTheDocument();
  const shown=screen.getByLabelText('운전 성향 전체 항목');
  expect(within(shown).getAllByRole('button')).toHaveLength(9);
  expect(within(shown).getByRole('button',{name:/조작 종류 8 · .* · 반복 확인 ·/})).toBeInTheDocument();
  expect(within(shown).getByText('조작 종류 9')).toBeInTheDocument();
  for(const label of ['제동','가속·타력','조향·변속'])expect(within(within(shown).getByRole('region',{name:label})).getAllByRole('button')).toHaveLength(3);
  expect(screen.getByText('항목별 관측 범위 · 44–61개 차량·코스 조합')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'다음 성향 3개'})).not.toBeInTheDocument();
  const order=within(shown).getAllByRole('button').map(button=>button.getAttribute('aria-label').split(' · ')[0]);
  style.items[0].status='REPEATED';
  view.rerender(<DriverStyleCard compact style={style}/>);
  expect(within(shown).getAllByRole('button').map(button=>button.getAttribute('aria-label').split(' · ')[0])).toEqual(order);
  view.rerender(<DriverStyleCard compact style={{items:style.items.slice(0,2)}}/>);
  expect(within(shown).getAllByRole('button')).toHaveLength(2);
});

test('nine-axis details select one meaning at a time and use backend examples and calculation metadata',async()=>{
  render(<DriverStyleCard compact style={fullStyle()}/>);
  const trigger=screen.getByRole('button',{name:'운전 성향 자세히'});
  userEvent.click(trigger);
  const dialog=await screen.findByRole('dialog',{name:'운전 성향 자세히'});
  const selector=within(dialog).getByRole('combobox',{name:'설명할 성향 항목'});
  expect(within(selector).getAllByRole('option')).toHaveLength(9);
  expect(within(dialog).getAllByRole('tab')).toHaveLength(2);
  for(const [index,axis] of styleAxes.entries()) {
    userEvent.selectOptions(selector,axis);
    expect(within(dialog).getByText(`입력 의미 ${index+1}`)).toBeInTheDocument();
    expect(within(dialog).getByText(`입력 예시 ${index+1}`)).toBeInTheDocument();
    expect(within(dialog).getByText(`입력 해석 한계 ${index+1}`)).toBeInTheDocument();
    expect(within(dialog).queryByText(`입력 의미 ${index===0?2:1}`)).not.toBeInTheDocument();
    userEvent.click(within(dialog).getByRole('button',{name:'계산 조건과 성향 이름 기준'}));
    expect(within(dialog).getByText(`입력 계산 조건 ${index+1}`)).toBeInTheDocument();
    expect(within(dialog).getByText(new RegExp(`성향 A${index+1} 또는 성향 B${index+1}`))).toBeInTheDocument();
  }
  userEvent.click(within(dialog).getByRole('tab',{name:'수치 읽는 법'}));
  expect(within(dialog).getByRole('region',{name:'성향 수치와 표시 읽는 법'})).toBeInTheDocument();
  userEvent.keyboard('{Escape}');
  await waitFor(()=>expect(trigger).toHaveFocus());
});

test('unavailable axes remain visible and open their own explanation with focus restored',async()=>{
  const style=fullStyle();
  style.items=style.items.map((item,index)=>index<2?item:{...item,status:'UNAVAILABLE',value:null,event_count:0,reason:`입력 채널 부족 ${index+1}`});
  style.input_coverage={source_count:7,current_source_count:2,pending_source_count:5,version:'driver-inputs-v2',reason:'기존 기록의 새 성향 항목을 백그라운드에서 한 번 보완합니다.'};
  render(<DriverStyleCard compact style={style}/>);
  expect(screen.getByText('측정 2 / 전체 9종')).toBeInTheDocument();
  expect(screen.getByText('기존 기록 5개 보완 대기')).toBeInTheDocument();
  expect(screen.queryByText('조작 관측 3')).not.toBeInTheDocument();
  const grid=screen.getByLabelText('운전 성향 전체 항목');
  expect(within(grid).getAllByRole('button')).toHaveLength(9);
  expect(within(grid).getAllByText('측정 대기')).toHaveLength(7);
  const trigger=within(grid).getByRole('button',{name:/조작 종류 9 ·/});
  userEvent.click(trigger);
  const dialog=await screen.findByRole('dialog',{name:'운전 성향 자세히'});
  const selector=within(dialog).getByRole('combobox',{name:'설명할 성향 항목'});
  expect(within(selector).getAllByRole('option')).toHaveLength(9);
  expect(within(dialog).getByText(style.input_coverage.reason)).toBeInTheDocument();
  expect(selector).toHaveValue('shift_consistency');
  expect(within(dialog).getByText('입력 채널 부족 9')).toBeInTheDocument();
  expect(within(dialog).getByText('입력 의미 9')).toBeInTheDocument();
  expect(within(dialog).getByText('—')).toBeInTheDocument();
  expect(within(dialog).queryByText('—%')).not.toBeInTheDocument();
  userEvent.keyboard('{Escape}');
  await waitFor(()=>expect(trigger).toHaveFocus());
});
