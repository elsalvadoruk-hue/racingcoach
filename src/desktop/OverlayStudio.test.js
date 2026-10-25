import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import OverlayStudio from './OverlayStudio';
import { initialConfig, selectModules } from './overlayCatalog';

jest.mock('../lib/gameContext', () => ({ useGame: () => ({ selectedGame: 'iracing' }) }));
jest.mock('../components/OverlaySettings', () => () => <div>창 설정 내용</div>);

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
  expect(screen.getByText('창 설정 내용')).not.toBeVisible();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
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
