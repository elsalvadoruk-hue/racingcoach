import React from 'react';
import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {DriverFeelingCard,DriverChallengeCard} from './DriverJournal';
import SelectedDriveReflection from './SelectedDriveReflection';
import useDriverJournal from './useDriverJournal';

const A='a'.repeat(64), B='b'.repeat(64), C='c'.repeat(64);
const drive=(id,game='iracing')=>({drive_id:id,game,recorded_at:'2026-10-26T12:00:00Z',labels:{car:id===A?'이전 GT3':'최근 GT3',track:'Fixture'}});
const context=(game='iracing',id=A)=>({game,selected_drive_id:id,feeling:null,drives:[drive(A,game),drive(B,game)],candidates:[],active_challenge:null,challenge_history:[]});
const response=payload=>({ok:true,json:async()=>payload});
function Harness({game='iracing',fixed='',revision=0,compact=false,onNavigate}) {const journal=useDriverJournal('',game,revision,fixed);return <><DriverFeelingCard journal={journal} fixed={!!fixed} compact={compact}/><DriverChallengeCard journal={journal} compact={compact} onNavigate={onNavigate}/></>;}
let previous;
beforeEach(()=>{previous=global.fetch;global.fetch=jest.fn();});
afterEach(()=>{global.fetch=previous;});

test('a feeling is saved, restored on remount, updated with difficulty and deleted',async()=>{
  let feeling=null;
  fetch.mockImplementation(async(url,options={})=>{
    if(options.method==='PUT') feeling=JSON.parse(options.body);
    if(options.method==='DELETE') feeling=null;
    return response({...context(),feeling});
  });
  let view=render(<Harness/>);
  const difficult=await screen.findByRole('radio',{name:'어려웠어요'});
  expect(difficult).toHaveAttribute('aria-checked','false');
  fireEvent.click(difficult);
  await screen.findByRole('radio',{name:'브레이크 밟을 때'});
  fireEvent.click(screen.getByRole('radio',{name:'브레이크 밟을 때'}));
  await waitFor(()=>expect(feeling).toEqual({response:'DIFFICULT',difficulty:'BRAKING'}));
  await waitFor(()=>expect(screen.getByRole('radio',{name:'브레이크 밟을 때'})).toHaveAttribute('aria-checked','true'));
  view.unmount();view=render(<Harness/>);
  expect(await screen.findByRole('radio',{name:'어려웠어요'})).toHaveAttribute('aria-checked','true');
  fireEvent.click(screen.getByRole('button',{name:'체감 기록 지우기'}));
  await waitFor(()=>expect(screen.getByRole('radio',{name:'어려웠어요'})).toHaveAttribute('aria-checked','false'));
  expect(feeling).toBeNull();
  expect(fetch.mock.calls.find(([,opt])=>opt.method==='PUT')[0]).toContain(`/feelings/${A}?game=iracing&drive_id=${A}`);
});

test('an older selected analysis always reads and saves its own drive, without falling back to the latest',async()=>{
  fetch.mockImplementation(async(url,options={})=>response({...context('iracing',B),feeling:options.method==='PUT'?JSON.parse(options.body):null}));
  const view=render(<SelectedDriveReflection base="" game="iracing" link={{drive_ids:[B]}}/>);
  fireEvent.click(await screen.findByRole('radio',{name:'편했어요'}));
  await screen.findByText('체감을 저장했습니다.');
  expect(fetch.mock.calls.every(([url])=>url.includes(`drive_id=${B}`))).toBe(true);
  expect(screen.queryByRole('button',{name:'체감 남길 주행 선택'})).not.toBeInTheDocument();
  view.rerender(<SelectedDriveReflection base="" game="iracing" link={{drive_ids:[]}}/>);
  expect(screen.queryByRole('radio',{name:'편했어요'})).not.toBeInTheDocument();
});

test('a multi-drive file requires explicit choice and a game change discards a late response',async()=>{
  let finish;
  fetch.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  const view=render(<SelectedDriveReflection base="" game="iracing" link={{drive_ids:[A,B]}}/>);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'주행 1 · 기록 확인'}));
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
  const delayed=finish;
  view.rerender(<SelectedDriveReflection base="" game="lmu" link={{drive_ids:[C]}}/>);
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));
  await act(async()=>{delayed(response(context()));finish(response({...context('lmu',C),drives:[drive(C,'lmu')]}));});
  expect(await screen.findByRole('radio',{name:'편했어요'})).toHaveAttribute('aria-checked','false');
  expect(screen.queryByText(/이전 GT3/)).not.toBeInTheDocument();
});

test('challenge acceptance, verification and cancellation use explicit mutations and show persisted history',async()=>{
  const candidate={candidate_id:C,drive_id:A,title:'3랩 페이스',description:'같은 조합으로 3랩'};
  let active=null,history=[];
  fetch.mockImplementation(async(url,options={})=>{
    if(options.method==='POST'&&url.includes('/challenges?'))active={...candidate,id:'f'.repeat(32),status:'ACTIVE'};
    if(url.includes('/cancel?')){history=[{...active,status:'CANCELLED'}];active=null;}
    return response({...context(),candidates:active?[]:[candidate],active_challenge:active,challenge_history:history,coverage:{challenge_history:{total:74,achieved:73}}});
  });
  render(<Harness/>);
  fireEvent.click(await screen.findByRole('button',{name:'이 도전 선택 · 3랩 페이스'}));
  fireEvent.click(await screen.findByRole('button',{name:'달성 여부 확인'}));
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>url.includes('/reconcile?'))).toBe(true));
  await waitFor(()=>expect(screen.getByRole('button',{name:'도전 취소'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'도전 취소'}));
  await waitFor(()=>expect(active).toBeNull());
  await waitFor(()=>expect(screen.queryByRole('button',{name:'도전 취소'})).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button',{name:'도전·달성 기록'}));
  expect(await screen.findByText('취소')).toBeInTheDocument();
  expect(screen.getByText('달성 73개')).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole('dialog'),{key:'Escape'});
  await waitFor(()=>expect(screen.getByRole('button',{name:'도전·달성 기록'})).toHaveFocus());
});

test('a failed save keeps the prior answer and never claims success',async()=>{
  fetch.mockResolvedValue(response({...context(),feeling:{response:'NEUTRAL',difficulty:null}}));
  render(<Harness/>);
  await screen.findByRole('radio',{name:'보통이에요'});
  fetch.mockResolvedValue({ok:false,status:503,json:async()=>({detail:'잠시 후 다시 저장하세요.'})});
  fireEvent.click(screen.getByRole('radio',{name:'편했어요'}));
  await screen.findByRole('button',{name:'기록 다시 확인'});
  expect(screen.getByRole('radio',{name:'보통이에요'})).toHaveAttribute('aria-checked','true');
  expect(screen.queryByText('체감을 저장했습니다.')).not.toBeInTheDocument();
});

test('background revisions wait for an active save and then read the committed answer',async()=>{
  let complete,answer=null,saveSignal;
  fetch.mockImplementation(async(url,options={})=>{
    if(options.method==='PUT'){
      saveSignal=options.signal;
      await new Promise(resolve=>{complete=resolve;});
      answer=JSON.parse(options.body);
    }
    return response({...context(),feeling:answer});
  });
  const view=render(<Harness/>);
  fireEvent.click(await screen.findByRole('radio',{name:'편했어요'}));
  await waitFor(()=>expect(complete).toBeDefined());
  view.rerender(<Harness revision={1}/>);
  expect(saveSignal.aborted).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async()=>complete());
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(3));
  expect(screen.getByRole('radio',{name:'편했어요'})).toHaveAttribute('aria-checked','true');
});

test('compact feelings save all four answers and edit difficulty in the shared detailed card',async()=>{
  let feeling=null;
  fetch.mockImplementation(async(url,options={})=>{
    if(options.method==='PUT')feeling=JSON.parse(options.body);
    if(options.method==='DELETE')feeling=null;
    return response({...context(),feeling});
  });
  render(<Harness compact/>);
  for(const [label,value] of [['편했어요','COMFORTABLE'],['보통이에요','NEUTRAL'],['잘 모르겠어요','UNSURE'],['어려웠어요','DIFFICULT']]){
    fireEvent.click(await screen.findByRole('radio',{name:label}));
    await waitFor(()=>expect(screen.getByRole('radio',{name:label})).toHaveAttribute('aria-checked','true'));
    expect(feeling?.response).toBe(value);
  }
  expect(screen.queryByRole('radio',{name:'브레이크 밟을 때'})).not.toBeInTheDocument();
  const requestCount=fetch.mock.calls.length;
  fireEvent.click(screen.getByRole('button',{name:'체감 상세 보기'}));
  const details=await screen.findByRole('dialog',{name:'주행 체감 상세'});
  expect(fetch).toHaveBeenCalledTimes(requestCount);
  expect(within(details).getByRole('radiogroup',{name:'어려웠던 상황'})).toBeInTheDocument();
  fireEvent.click(within(details).getByRole('radio',{name:'브레이크 밟을 때'}));
  await waitFor(()=>expect(feeling).toEqual({response:'DIFFICULT',difficulty:'BRAKING'}));
  fireEvent.keyDown(details,{key:'Escape'});
  await waitFor(()=>expect(screen.getByRole('button',{name:'체감 상세 보기'})).toHaveFocus());
  expect(screen.getByRole('radio',{name:'어려웠어요'})).toHaveAttribute('aria-checked','true');
  fireEvent.click(screen.getByRole('button',{name:'체감 기록 지우기'}));
  await waitFor(()=>expect(feeling).toBeNull());
  await waitFor(()=>expect(screen.getByRole('radio',{name:'어려웠어요'})).toHaveAttribute('aria-checked','false'));
});

test('compact feeling skip and drive selection retain their explicit drive scope',async()=>{
  fetch.mockImplementation(async(url)=>response(context('iracing',new URL(url,'http://localhost').searchParams.get('drive_id')||A)));
  render(<Harness compact/>);
  fireEvent.click(await screen.findByRole('button',{name:'건너뛰기'}));
  expect(screen.queryByRole('radio',{name:'편했어요'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'체감 남기기'}));
  expect(screen.getByRole('radio',{name:'편했어요'})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'체감 남길 주행 선택'}));
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button',{name:/최근 GT3/}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'체감 남길 주행 선택'})).toHaveFocus());
  await screen.findByText(/2026\.10\.26 12:00 UTC · 최근 GT3 · Fixture/);
  expect(fetch.mock.calls.at(-1)[0]).toContain(`drive_id=${B}`);
  expect(screen.getAllByRole('radio')).toHaveLength(4);
});

test('compact challenges show exact numerical targets, preserve total achievements and open complete details',async()=>{
  const candidates=[{candidate_id:C,drive_id:A,title:'측정 페이스 3랩 재현',description:'같은 차량의 새 독립 주행에서 측정한 전체 목표 설명',target:{min_laps:3,pace_limit_s:120.123,mad_limit_pct:null}},
    {candidate_id:B,drive_id:A,title:'페이스와 편차 5랩 유지',description:'다섯 랩의 편차 조건을 확인합니다.',target:{min_laps:5,pace_limit_s:120.123,mad_limit_pct:0}}];
  let active=null;
  fetch.mockImplementation(async(url,options={})=>{
    if(options.method==='POST'&&url.includes('/challenges?'))active={...candidates[0],id:'f'.repeat(32),status:'ACTIVE',evaluation:{reason:'아직 새 독립 주행을 확인하지 못했습니다.'}};
    if(url.includes('/cancel?'))active=null;
    return response({...context(),candidates:active?[]:candidates,active_challenge:active,coverage:{challenge_history:{total:74,achieved:73}}});
  });
  render(<Harness compact onNavigate={()=>{}}/>);
  expect(await screen.findByText('3랩 이상 · 중앙 ≤ 120.123초')).toBeInTheDocument();
  expect(screen.getByText('5랩 이상 · 중앙 ≤ 120.123초 · 편차 ≤ 0.000%')).toBeInTheDocument();
  expect(screen.getByText('달성 73개')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'주행 분석에서 확인'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'도전 상세'}));
  const details=await screen.findByRole('dialog',{name:'도전 목표 상세'});
  expect(within(details).getByText(candidates[0].description)).toBeInTheDocument();
  expect(within(details).getByRole('button',{name:'주행 분석에서 확인'})).toBeInTheDocument();
  fireEvent.keyDown(details,{key:'Escape'});
  await waitFor(()=>expect(screen.getByRole('button',{name:'도전 상세'})).toHaveFocus());
  fireEvent.click(screen.getByRole('button',{name:'이 도전 선택 · 측정 페이스 3랩 재현'}));
  await screen.findByText('진행 중');
  fireEvent.click(screen.getByRole('button',{name:'달성 여부 확인'}));
  await waitFor(()=>expect(fetch.mock.calls.some(([url])=>url.includes('/reconcile?'))).toBe(true));
  await waitFor(()=>expect(screen.getByRole('button',{name:'도전 취소'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'도전 취소'}));
  await waitFor(()=>expect(screen.queryByRole('button',{name:'도전 취소'})).not.toBeInTheDocument());
});

test('compact journal surfaces a failed save and keeps retry available',async()=>{
  fetch.mockResolvedValue(response({...context(),feeling:{response:'NEUTRAL',difficulty:null}}));
  render(<Harness compact/>);
  await screen.findByRole('radio',{name:'보통이에요'});
  fetch.mockResolvedValue({ok:false,status:503,json:async()=>({detail:'잠시 후 다시 저장하세요.'})});
  fireEvent.click(screen.getByRole('radio',{name:'편했어요'}));
  expect(await screen.findAllByRole('button',{name:'기록 다시 확인'})).toHaveLength(2);
  expect(screen.getByRole('radio',{name:'보통이에요'})).toHaveAttribute('aria-checked','true');
  fetch.mockResolvedValue(response({...context(),feeling:{response:'NEUTRAL',difficulty:null}}));
  fireEvent.click(screen.getAllByRole('button',{name:'기록 다시 확인'})[0]);
  await waitFor(()=>expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});

test('an initial compact journal read failure shows retry without claiming there is no drive',async()=>{
  fetch.mockResolvedValue({ok:false,status:503,json:async()=>({detail:'기록 연결 실패'})});
  render(<Harness compact/>);
  await screen.findAllByRole('button',{name:'기록 다시 확인'});
  expect(screen.queryByText('남길 주행을 기다려요')).not.toBeInTheDocument();
  expect(screen.queryByText('주행 파일 분석이 끝나면 이곳에서 느낌을 선택할 수 있습니다.')).not.toBeInTheDocument();
});
