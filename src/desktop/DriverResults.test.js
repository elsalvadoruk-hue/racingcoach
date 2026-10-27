import React from 'react';
import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import DriverResults from './DriverResults';

const payload=(game='iracing',period=0)=>({game,period_days:period,aggregate:{recorded_results:2,official_races:1,observation_results:1,finish_observations:0,unconfirmed_results:1,wins:{value:1},podiums:{value:1},average_position:{value:1}},results:[
  {id:'final',status:'OFFICIAL',recorded_at:'2026-10-26T12:00:00Z',class_position:1,finish_status:'CLASSIFIED',labels:{car:'GT3 final',track:'Fixture'}},
  {id:'observation',status:'OBSERVATION',recorded_at:null,class_position:null,finish_status:'DNF',labels:{car:'확인 중',track:'Fixture'},reason:'최종 순위 미확인'}]});
const response=data=>({ok:true,json:async()=>data});
let previous;
beforeEach(()=>{previous=global.fetch;global.fetch=jest.fn().mockResolvedValue(response(payload()));});
afterEach(()=>{global.fetch=previous;});

test('an initial compact read failure shows retry without claiming there are no stored results',async()=>{
  fetch.mockRejectedValueOnce(new TypeError('결과 연결 실패'));
  render(<DriverResults base="" game="iracing" period="0" compact/>);
  await screen.findByText('경기 결과 조회 실패');
  expect(screen.queryByText('아직 보관한 최종 결과가 없어요')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'경기 결과 다시 확인'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'경기 결과 상세'}));
  const dialog=await screen.findByRole('dialog');
  expect(within(dialog).queryByText('아직 보관한 최종 결과가 없어요')).not.toBeInTheDocument();
});

test('unverified results remain visible as result counts without presenting official absence as zero races',async()=>{
  const data=payload();
  data.aggregate={recorded_results:7,official_races:0,observation_results:7,finish_observations:1,unconfirmed_results:6,
    wins:{value:null},podiums:{value:null},average_position:{value:null}};
  data.results=[{...data.results[1],authority:'IRACING_RESULT_TABLE',finish_status:'UNKNOWN',class_position:14}];
  fetch.mockResolvedValue(response(data));
  render(<DriverResults base="" game="iracing" period="0" compact/>);
  expect(await screen.findByText('7개')).toBeInTheDocument();
  expect(screen.getByText('보관한 경기 결과')).toBeInTheDocument();
  expect(screen.getByText('종료 미확인 · 관측 클래스 14위')).toBeInTheDocument();
  expect(screen.getByText(/공식 미확인/)).toBeInTheDocument();
  expect(screen.queryByText('0회')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'경기 결과 상세'}));
  const dialog=await screen.findByRole('dialog');
  expect(within(dialog).getByText('공식 결과 미확인 · 종료 관측 1개 · 결과 미확정 6개')).toBeInTheDocument();
  expect(within(dialog).getAllByText('—')).toHaveLength(3);
});

test('official finals and unconfirmed observations remain distinct with unavailable positions',async()=>{
  render(<DriverResults base="" game="iracing" period="0"/>);
  expect(await screen.findByText('공식 최종 결과')).toBeInTheDocument();
  expect(screen.getByText('결과 관측')).toBeInTheDocument();
  expect(screen.getByText('분류 완료 · 클래스 1위')).toBeInTheDocument();
  expect(screen.getByText('리타이어')).toBeInTheDocument();
  expect(screen.queryByText(/클래스 0위/)).not.toBeInTheDocument();
});

test('period and game changes cannot display a late prior-game result',async()=>{
  let resolve;
  fetch.mockImplementationOnce(()=>new Promise(done=>{resolve=done;})).mockResolvedValue(response({...payload('lmu',30),results:[]}));
  const view=render(<DriverResults base="" game="iracing" period="0"/>);
  view.rerender(<DriverResults base="" game="lmu" period="30"/>);
  await screen.findByText('아직 보관한 최종 결과가 없어요');
  await act(async()=>resolve(response(payload())));
  expect(screen.queryByText('GT3 final · Fixture')).not.toBeInTheDocument();
  expect(fetch.mock.calls[1][0]).toBe('/api/driver-results?game=lmu&period_days=30');
});

test('a failed same-scope refresh preserves previous results and supports retry',async()=>{
  const view=render(<DriverResults base="" game="iracing" period="0"/>);
  await screen.findByText('공식 최종 결과');
  fetch.mockRejectedValueOnce(new TypeError('연결 실패'));
  view.rerender(<DriverResults base="" game="iracing" period="0" revision={1}/>);
  fireEvent.click(await screen.findByRole('button',{name:'경기 결과 다시 확인'}));
  await waitFor(()=>expect(screen.queryByText('경기 결과 조회 실패')).not.toBeInTheDocument());
  expect(screen.getByText('GT3 final · Fixture')).toBeInTheDocument();
});

test('compact results show four totals and only the latest result until details open',async()=>{
  render(<DriverResults base="" game="iracing" period="0" compact/>);
  await screen.findByText('공식 최종 결과');
  expect(screen.getAllByRole('definition')).toHaveLength(4);
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(screen.getByText('분류 완료 · 클래스 1위')).toBeInTheDocument();
  expect(screen.queryByText('결과 관측')).not.toBeInTheDocument();
  expect(screen.queryByText('확인 중 · Fixture')).not.toBeInTheDocument();
  const trigger=screen.getByRole('button',{name:'경기 결과 상세'});
  trigger.focus();
  fireEvent.click(trigger);
  const dialog=await screen.findByRole('dialog',{name:'보관한 경기 결과 상세'});
  expect(within(dialog).getByText('결과 관측')).toBeInTheDocument();
  expect(within(dialog).getByText('최종 순위 미확인')).toBeInTheDocument();
  expect(within(dialog).getByText(/날짜는 UTC 기준/)).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(within(dialog).getAllByRole('button',{name:'닫기'})[0]);
  await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('compact details retain up to 200 results and disclose the aggregate and list ranges',async()=>{
  const data=payload();
  data.results=Array.from({length:200},(_,index)=>({...data.results[index%2],id:`result-${index}`}));
  data.coverage={source_count:240,result_count:230,period_result_count:230,undated_result_count:3};
  fetch.mockResolvedValue(response(data));
  render(<DriverResults base="" game="iracing" period="0" compact/>);
  await screen.findByText('공식 최종 결과');
  fireEvent.click(screen.getByRole('button',{name:'경기 결과 상세'}));
  const dialog=await screen.findByRole('dialog');
  expect(within(dialog).getAllByRole('article')).toHaveLength(200);
  expect(within(dialog).getByText('연결 원본 240개 · 전체 보관 결과 230개 · 날짜 미확인 3개')).toBeInTheDocument();
  expect(within(dialog).getByText('합계는 선택 기간의 전체 보관 결과로 계산하며 목록은 최근 200개를 표시합니다.')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('compact details close on game changes and cannot retain the previous game result',async()=>{
  const view=render(<DriverResults base="" game="iracing" period="0" compact/>);
  await screen.findByText('공식 최종 결과');
  fireEvent.click(screen.getByRole('button',{name:'경기 결과 상세'}));
  await screen.findByRole('dialog');
  fetch.mockResolvedValue(response({...payload('lmu',30),results:[]}));
  view.rerender(<DriverResults base="" game="lmu" period="30" compact/>);
  await screen.findByText('아직 보관한 최종 결과가 없어요');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('GT3 final · Fixture')).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('compact errors preserve the last result while details expose the reason and retry',async()=>{
  const view=render(<DriverResults base="" game="iracing" period="0" compact/>);
  await screen.findByText('공식 최종 결과');
  fetch.mockRejectedValueOnce(new TypeError('결과 연결 실패'));
  view.rerender(<DriverResults base="" game="iracing" period="0" revision={1} compact/>);
  await screen.findByText('경기 결과 조회 실패');
  expect(screen.getByText('GT3 final · Fixture')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'경기 결과 상세'}));
  const dialog=await screen.findByRole('dialog');
  expect(within(dialog).getByText('결과 연결 실패')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button',{name:'경기 결과 다시 확인'}));
  await waitFor(()=>expect(within(dialog).queryByText('경기 결과 조회 실패')).not.toBeInTheDocument());
  expect(within(dialog).getByText('GT3 final · Fixture')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(3);
});
