import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import OverlayStudio from './OverlayStudio';
import { initialConfig, selectModules } from './overlayCatalog';
import { useGame } from '../lib/gameContext';

jest.mock('../lib/gameContext', () => ({ useGame: jest.fn(() => ({ selectedGame: 'iracing' })) }));
jest.mock('../components/OverlaySettings', () => ({ visible }) => <div data-testid="window-settings" data-active={visible}>창 설정 내용</div>);

beforeEach(() => { useGame.mockReturnValue({ selectedGame: 'iracing' }); });

function fixture() {
  let saved = initialConfig();
  const fetcher = jest.fn(async (url, options = {}) => {
    if(url.includes('/studio/render')) {
      const body=JSON.parse(options.body), ids=selectModules(body.config,body.session,body.scene,body.availability).ids;
      return {ok:true,json:async()=>({renderer:'native-tk-v1',svg:'<svg xmlns="http://www.w3.org/2000/svg"/>',width:390,height:150,ids,
        texts:ids.length?['Garage61 기준 랩','1:37.607','초 · 기준보다 빠름']:['표시할 항목을 선택하세요']})};
    }
    if (options.method === 'PUT' && !url.includes('/session')) saved = JSON.parse(options.body).config;
    return { ok: true, json: async () => ({ studio: saved, game_default: saved,
      settings_target: {game: 'iracing', car_key: null}, reference: null, diagnostics_version: 1, automatic_version: 1 }) };
  });
  return {fetcher};
}

test('window settings clearly expose open and closed states through a keyboard-accessible button', async () => {
  const {fetcher} = fixture(); render(<OverlayStudio fetcher={fetcher}/>);
  const toggle = screen.getByRole('button', {name: /창·표시 설정.*설정 열기/});
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByTestId('window-settings')).toHaveAttribute('data-active', 'false');
  expect(screen.getByText('창 설정 내용')).not.toBeVisible();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByTestId('window-settings')).toHaveAttribute('data-active', 'true');
  expect(toggle).toHaveTextContent('설정 접기');
  expect(screen.getByText('창 설정 내용')).toBeVisible();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await waitFor(() => expect(screen.getByRole('button', {name:'저장·적용'})).toBeEnabled());
});

test('straight scheduling is explained and comparison lap has its own visible value', async () => {
  const {fetcher}=fixture(); render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('button',{name:'저장·적용'})).toBeEnabled());
  expect(screen.getByText(/연속 코너의 결과를 모아 다음 직선에서/)).toHaveTextContent('6초간 고정');
  fireEvent.change(screen.getByRole('combobox',{name:'미리보기 샘플 상황'}),{target:{value:'g61'}});
  fireEvent.click(screen.getByRole('radio',{name:'통과 결과'}));
  const hud=screen.getByTestId('hud-practice');
  await waitFor(()=>expect(within(hud).getByRole('img')).toHaveAttribute('data-renderer','native-tk-v1'));
  expect(within(hud).getByRole('img')).toHaveAccessibleName(/Garage61 기준 랩 · 1:37.607/);
});

test('multiple checked cards survive save and reload and switch with the preview situation', async () => {
  const {fetcher} = fixture(); const first=render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('button', {name:'저장·적용'})).toBeEnabled());
  fireEvent.click(screen.getByRole('checkbox', {name:'다음 코너 연습 목표'}));
  fireEvent.click(screen.getByRole('checkbox', {name:'이번 주행 개선 요약'}));
  expect(screen.getByRole('checkbox', {name:'직전 코너 성적표'})).toBeChecked();
  expect(screen.getByRole('checkbox', {name:'이번 주행 개선 요약'})).toBeChecked();
  expect(screen.getByRole('checkbox', {name:'다음 코너 연습 목표'})).toBeChecked();
  fireEvent.click(screen.getByRole('button', {name:'저장·적용'}));
  await screen.findByText('구성을 저장했습니다. 주행 오버레이와 창 미리보기에 적용됩니다.');
  first.unmount(); render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('checkbox', {name:'이번 주행 개선 요약'})).toBeChecked());
  expect(screen.getByRole('checkbox', {name:'이번 주행 개선 요약'})).toBeChecked();
  const hud=screen.getByTestId('hud-practice');
  fireEvent.click(screen.getByRole('radio', {name:'코너 접근'}));
  await waitFor(()=>expect(hud.querySelector('[data-module="goal"]')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('radio', {name:'통과 결과'}));
  await waitFor(()=>expect(hud.querySelector('[data-module="score"]')).toBeInTheDocument());
  expect(hud.querySelector('[data-module="goal"]')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('radio', {name:'랩 종료'}));
  await waitFor(()=>expect(hud.querySelector('[data-module="summary"]')).toBeInTheDocument());
});

test('deselecting all cards shows an empty state instead of restoring unchecked cards', async () => {
  const {fetcher} = fixture(); render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('button', {name:'저장·적용'})).toBeEnabled());
  for (const checkbox of screen.getAllByRole('checkbox')) if (checkbox.checked) fireEvent.click(checkbox);
  await waitFor(()=>expect(within(screen.getByTestId('hud-practice')).getByText('표시할 항목을 선택하세요')).toBeInTheDocument());
  expect(screen.getByRole('checkbox', {name:'반복 조작 패턴'})).toBeEnabled();
});

test('an older running backend shows restart guidance before editing or saving new selections', async () => {
  const old=initialConfig(); delete old.selected;
  const fetcher=jest.fn(async()=>({ok:true,json:async()=>({studio:old,game_default:old,settings_target:{game:'iracing',car_key:null}})}));
  render(<OverlayStudio fetcher={fetcher}/>);
  await screen.findByText('오버레이 복수 선택을 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.');
  expect(screen.getByRole('button',{name:'저장·적용'})).toBeDisabled();
});

test('the five diagnostics can be selected and saved and previewed in the appropriate situation', async () => {
  const {fetcher}=fixture(); render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('button',{name:'저장·적용'})).toBeEnabled());
  for (const checkbox of screen.getAllByRole('checkbox')) if (checkbox.checked) fireEvent.click(checkbox);
  for (const name of ['처음 달라진 조작','반복 조작 패턴','진입·회전·탈출 손익','빠른 통과의 공통 패턴']) {
    const choice=screen.getByRole('checkbox',{name}); expect(choice).toBeEnabled(); fireEvent.click(choice);
  }
  fireEvent.click(screen.getByRole('radio',{name:'통과 결과'}));
  const hud=screen.getByTestId('hud-practice');
  await waitFor(()=>expect(hud.querySelector('[data-module="divergence"]')).toBeInTheDocument());
  expect(hud.querySelector('[data-module="phases"]')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'저장·적용'}));
  await screen.findByText('구성을 저장했습니다. 주행 오버레이와 창 미리보기에 적용됩니다.');
  expect(fetcher.mock.calls.filter(([,options])=>options?.method==='PUT' && options.body?.includes('divergence')).length).toBeGreaterThan(0);
  expect(screen.queryByText('준비 중')).not.toBeInTheDocument();
});

test('a backend without diagnostics asks for a restart before saving unsupported choices', async () => {
  const fetcher=jest.fn(async()=>({ok:true,json:async()=>({studio:initialConfig(),settings_target:{game:'iracing',car_key:null}})}));
  render(<OverlayStudio fetcher={fetcher}/>);
  await screen.findByText('새 코너 진단을 사용하려면 앱과 검은 실행창을 완전히 종료한 뒤 다시 실행하세요.');
  expect(screen.getByRole('button',{name:'저장·적용'})).toBeDisabled();
});

test('default automatic coaching works without a preview reference and explains optional preparation', async () => {
  const {fetcher}=fixture(); render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('button',{name:'저장·적용'})).toBeEnabled());
  expect(screen.getByRole('checkbox',{name:'자동 주행 코칭'})).toBeChecked();
  expect(screen.getByRole('checkbox',{name:'다음 코너 연습 목표'})).not.toBeChecked();
  fireEvent.change(screen.getByRole('combobox',{name:'미리보기 샘플 상황'}),{target:{value:'none'}});
  await waitFor(()=>expect(screen.getByTestId('hud-practice').querySelector('[data-module="live"]')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button',{name:'내용 표시 방법'}));
  const guide=screen.getByRole('dialog',{name:'오버레이 내용 표시 방법'});
  expect(within(guide).getByRole('table')).toBeInTheDocument();
  expect(within(guide).getByText(/연습 목표·성공률은 선택 기능/)).toBeInTheDocument();
});

test('formation selections persist independently and fixed grid cannot be disabled', async () => {
  const {fetcher}=fixture(); render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('button',{name:'저장·적용'})).toBeEnabled());
  fireEvent.mouseDown(screen.getByRole('tab',{name:'포메이션'}),{button:0,ctrlKey:false});
  const choices=await screen.findByLabelText('포메이션 표시 항목');
  expect(within(choices).getByRole('checkbox',{name:'공식 출발 순위'})).toBeDisabled();
  expect(within(choices).getByRole('checkbox',{name:'바퀴별 온도'})).toBeDisabled();
  const fuel=within(choices).getByRole('checkbox',{name:'현재 연료'});
  expect(fuel).toBeChecked(); fireEvent.click(fuel);
  fireEvent.click(screen.getByRole('button',{name:'저장·적용'}));
  await screen.findByText('구성을 저장했습니다. 주행 오버레이와 창 미리보기에 적용됩니다.');
  const write=fetcher.mock.calls.find(([url, options])=>options?.method==='PUT' && !url.includes('/session'));
  const config=JSON.parse(write[1].body).config;
  expect(config.formation_items).not.toContain('8');
  expect(config.graph.window).toBe(10);
  expect(config.selected.formation).toEqual(['reference','live']);
  expect(screen.getByText(/무전 OFF·음소거·브리핑 0개/)).toBeInTheDocument();
});

test('LMU disables unavailable native grid and qualifying choices without deleting other game preferences', async () => {
  useGame.mockReturnValue({ selectedGame: 'lmu' });
  const {fetcher}=fixture(); render(<OverlayStudio fetcher={fetcher}/>);
  await waitFor(() => expect(screen.getByRole('button',{name:'저장·적용'})).toBeEnabled());
  fireEvent.mouseDown(screen.getByRole('tab',{name:'포메이션'}),{button:0,ctrlKey:false});
  const choices=await screen.findByLabelText('포메이션 표시 항목');
  for (const name of ['공식 정렬 안내','클래스 필드','그리드 앞뒤 차량','해당 경기 퀄리','iRating 상승 기준 추정']) {
    expect(within(choices).getByRole('checkbox',{name})).toBeDisabled();
  }
  expect(within(choices).getByRole('checkbox',{name:'바퀴별 온도'})).toBeEnabled();
  expect(within(choices).getByRole('checkbox',{name:'가상 에너지'})).toBeEnabled();
  fireEvent.click(within(choices).getByRole('radio',{name:'지난번 돌아보기'}));
  expect(within(choices).queryByRole('checkbox',{name:'현재 연료'})).not.toBeInTheDocument();
  expect(within(choices).getAllByRole('checkbox')).toHaveLength(5);
});

test('slow polling keeps a single request in flight and aborts it on unmount', async () => {
  jest.useFakeTimers();
  try {
    const fetcher=jest.fn(()=>new Promise(()=>{}));
    const view=render(<OverlayStudio fetcher={fetcher}/>);
    const reads=()=>fetcher.mock.calls.filter(([url])=>!url.includes('/render'));
    expect(reads()).toHaveLength(1);
    fireEvent.click(screen.getByRole('radio',{name:'네 세션 비교'}));
    jest.advanceTimersByTime(6000);
    expect(reads()).toHaveLength(1);
    const signal=reads()[0][1].signal;
    view.unmount();
    expect(signal.aborted).toBe(true);
  } finally { jest.useRealTimers(); }
});

test('session editing waits for initial server preferences rather than accepting a tab that will be reset', async () => {
  const {fetcher}=fixture();
  let ready;
  const pending=new Promise(resolve=>{ready=resolve;});
  const waiting=(url,options)=>url.includes('/render')?fetcher(url,options):pending.then(()=>fetcher(url,options));
  render(<OverlayStudio fetcher={waiting}/>);
  const formation=screen.getByRole('tab',{name:'포메이션'});
  expect(formation).toBeDisabled();
  ready();
  await waitFor(()=>expect(formation).toBeEnabled());
  fireEvent.mouseDown(formation,{button:0,ctrlKey:false});
  expect(await screen.findByLabelText('포메이션 표시 항목')).toBeInTheDocument();
});
