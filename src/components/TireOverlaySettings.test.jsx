import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import TireOverlaySettings from './TireOverlaySettings';

const initial = {version:1,supported:true,enabled:false,fixed:false,running:false};
const response = data => ({ok:true,json:async()=>data});

function fixture() {
  let state={...initial};
  const fetcher=jest.fn(async (url,options={})=>{
    if (options.method==='PUT') state={...state,...JSON.parse(options.body)};
    state.running=state.enabled;
    return response({...state});
  });
  return fetcher;
}

test('additional window has independent enable, lock and position controls',async()=>{
  const fetcher=fixture();render(<TireOverlaySettings apiBase="http://local" fetcher={fetcher}/>);
  const enabled=screen.getByRole('switch',{name:'타이어 오버레이 표시'});
  await waitFor(()=>expect(enabled).toBeEnabled());
  fireEvent.click(enabled);
  await waitFor(()=>expect(enabled).toBeChecked());
  fireEvent.click(screen.getByRole('switch',{name:'타이어 창 위치 잠금'}));
  await waitFor(()=>expect(screen.getByRole('switch',{name:'타이어 창 위치 잠금'})).toBeChecked());
  fireEvent.click(screen.getByRole('button',{name:'타이어 창 위치 초기화'}));
  await waitFor(()=>expect(fetcher).toHaveBeenCalledWith('http://local/api/tire-overlay/reset-position',expect.objectContaining({method:'POST'})));
  await waitFor(()=>expect(enabled).toBeEnabled());fireEvent.click(enabled);
  await waitFor(()=>expect(enabled).not.toBeChecked());
  expect(fetcher.mock.calls.every(([url])=>url.includes('/api/tire-overlay'))).toBe(true);
  expect(screen.getByText(/탑승·그리드 진입 10초/)).toHaveTextContent('1초마다');
});

test('LMU has independent game settings and voice-only controls',async()=>{
  const fetcher=fixture();render(<TireOverlaySettings game="lmu" fetcher={fetcher}/>);
  const voice=screen.getByRole('switch',{name:'타이어 음성 안내'});
  await waitFor(()=>expect(voice).toBeEnabled());fireEvent.click(voice);
  await waitFor(()=>expect(voice).toBeChecked());
  expect(screen.getByRole('switch',{name:'타이어 오버레이 표시'})).not.toBeChecked();
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining('?game=lmu'),expect.objectContaining({method:'PUT',body:JSON.stringify({voice_enabled:true})}));
  expect(screen.getByText(/공유 메모리에서/)).toBeVisible();
});

test('failed enable leaves saved toggle unchanged and a retry recovers',async()=>{
  const fetcher=fixture();fetcher.mockImplementationOnce(async()=>response({...initial}));
  fetcher.mockImplementationOnce(async()=>({ok:false,status:503,json:async()=>({detail:'OCR을 시작하지 못했습니다.'})}));
  render(<TireOverlaySettings fetcher={fetcher}/>);
  const toggle=screen.getByRole('switch',{name:'타이어 오버레이 표시'});
  await waitFor(()=>expect(toggle).toBeEnabled());fireEvent.click(toggle);
  expect(await screen.findByRole('alert')).toHaveTextContent('OCR을 시작하지 못했습니다.');
  expect(toggle).not.toBeChecked();fireEvent.click(toggle);
  await waitFor(()=>expect(toggle).toBeChecked());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('an old server gives restart instructions and no usable toggle',async()=>{
  const fetcher=jest.fn(async()=>({ok:false,status:404,json:async()=>({detail:'Not found'})}));
  render(<TireOverlaySettings fetcher={fetcher}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('앱과 실행창을 완전히 종료');
  expect(screen.getByRole('switch',{name:'타이어 오버레이 표시'})).toBeDisabled();
});

test('delayed polling cannot overwrite a completed mutation',async()=>{
  jest.useFakeTimers();
  try {
    const fetcher=fixture();let resolvePoll;
    render(<TireOverlaySettings fetcher={fetcher}/>);
    await waitFor(()=>expect(screen.getByRole('switch',{name:'타이어 오버레이 표시'})).toBeEnabled());
    fetcher.mockImplementationOnce(()=>new Promise(resolve=>{resolvePoll=resolve;}));
    await act(async()=>{jest.advanceTimersByTime(1000);});
    fireEvent.click(screen.getByRole('switch',{name:'타이어 오버레이 표시'}));
    await waitFor(()=>expect(screen.getByRole('switch',{name:'타이어 오버레이 표시'})).toBeChecked());
    await act(async()=>{resolvePoll(response({...initial}));});
    expect(screen.getByRole('switch',{name:'타이어 오버레이 표시'})).toBeChecked();
  } finally {jest.useRealTimers();}
});

test('a crashed child can restart',async()=>{
  const fetcher=fixture();fetcher.mockImplementationOnce(async()=>response({...initial,enabled:true}));
  const view=render(<TireOverlaySettings fetcher={fetcher}/>);
  const restart=await screen.findByRole('button',{name:'타이어 창 다시 켜기'});
  fireEvent.click(restart);await waitFor(()=>expect(screen.queryByRole('button',{name:'타이어 창 다시 켜기'})).not.toBeInTheDocument());
  view.unmount();
});

test('unmount aborts an outstanding status request',()=>{
  const fetcher=jest.fn(()=>new Promise(()=>{}));
  const view=render(<TireOverlaySettings fetcher={fetcher}/>);
  const signal=fetcher.mock.calls[0][1].signal;
  view.unmount();expect(signal.aborted).toBe(true);
});

test('appearance sliders merge rapid changes and save the latest values',async()=>{
  const fetcher=fixture();render(<TireOverlaySettings fetcher={fetcher}/>);
  const background=screen.getByRole('slider',{name:'배경 불투명도'});
  await waitFor(()=>expect(background).toBeEnabled());
  fireEvent.change(background,{target:{value:'20'}});
  fireEvent.change(background,{target:{value:'35'}});
  fireEvent.change(screen.getByRole('slider',{name:'표시 불투명도'}),{target:{value:'60'}});
  fireEvent.change(screen.getByRole('slider',{name:'모서리 둥글기 (R)'}),{target:{value:'16'}});
  await waitFor(()=>expect(fetcher).toHaveBeenCalledWith(expect.stringContaining('/settings'),expect.objectContaining({
    method:'PUT',body:JSON.stringify({background_opacity:35,content_opacity:60,radius:16}),
  })));
  expect(background).toHaveValue('35');
  expect(screen.getByRole('slider',{name:'모서리 둥글기 (R)'})).toHaveValue('16');
});

test('appearance can recover after a failed save without leaving controls busy',async()=>{
  const fetcher=fixture();render(<TireOverlaySettings fetcher={fetcher}/>);
  const background=screen.getByRole('slider',{name:'배경 불투명도'});
  await waitFor(()=>expect(background).toBeEnabled());
  fetcher.mockImplementationOnce(async()=>({ok:false,status:503,json:async()=>({detail:'설정 적용 실패'})}));
  fireEvent.change(background,{target:{value:'25'}});
  expect(await screen.findByRole('alert')).toHaveTextContent('설정 적용 실패');
  expect(background).toHaveValue('85');
  fireEvent.change(background,{target:{value:'40'}});
  await waitFor(()=>expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(background).toHaveValue('40');
  expect(screen.getByRole('switch',{name:'타이어 오버레이 표시'})).toBeEnabled();
});

test('dragging while a save is delayed sends the final value sequentially',async()=>{
  const fetcher=fixture();render(<TireOverlaySettings fetcher={fetcher}/>);
  const background=screen.getByRole('slider',{name:'배경 불투명도'});
  await waitFor(()=>expect(background).toBeEnabled());
  let finish;
  fetcher.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  fireEvent.change(background,{target:{value:'20'}});
  await waitFor(()=>expect(finish).toBeDefined());
  fireEvent.change(background,{target:{value:'35'}});
  await act(async()=>finish(response({...initial,background_opacity:20})));
  await waitFor(()=>expect(fetcher).toHaveBeenCalledWith(expect.stringContaining('/settings'),expect.objectContaining({body:JSON.stringify({background_opacity:35})})));
  await waitFor(()=>expect(screen.getByRole('switch',{name:'타이어 오버레이 표시'})).toBeEnabled());
  expect(background).toHaveValue('35');
});

test('without English Windows OCR the user can request it and recheck after installing',async()=>{
  let state={...initial,enabled:true,running:true,ocr_language:'ko',ocr_english:false,ocr_install:null};
  const fetcher=jest.fn(async(url,options={})=>{
    if (url.includes('/ocr-language/install')) state={...state,ocr_install:'requested'};
    if (url.includes('/ocr-language/recheck')) state={...state,ocr_english:true,ocr_install:null};
    return response({...state});
  });
  render(<TireOverlaySettings fetcher={fetcher}/>);
  expect(await screen.findByText(/영어 Windows OCR이 없어 현재 언어\(ko\)로/)).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'영어 OCR 설치 (관리자 승인)'}));
  await waitFor(()=>expect(fetcher).toHaveBeenCalledWith(expect.stringContaining('/ocr-language/install'),expect.objectContaining({method:'POST'})));
  fireEvent.click(await screen.findByRole('button',{name:'다시 확인'}));
  await waitFor(()=>expect(screen.queryByText(/영어 Windows OCR이 없어/)).not.toBeInTheDocument());
});

test('LMU never offers Windows OCR installation',async()=>{
  const fetcher=jest.fn(async()=>response({...initial,ocr_english:false}));
  render(<TireOverlaySettings game="lmu" fetcher={fetcher}/>);
  await waitFor(()=>expect(screen.getByRole('switch',{name:'타이어 음성 안내'})).toBeEnabled());
  expect(screen.queryByRole('button',{name:'영어 OCR 설치 (관리자 승인)'})).not.toBeInTheDocument();
});

test('a car without dash colors asks for ranges before voice can judge',async()=>{
  const fetcher=jest.fn(async()=>response({...initial,voice_enabled:true,running:true,car_path:'bmwm4gt3',
    car_name:'BMW M4 GT3',targets:{},state_source:null}));
  render(<TireOverlaySettings fetcher={fetcher}/>);
  expect(await screen.findByText(/BMW M4 GT3은 대시 색 정보가 없어/)).toBeVisible();
  expect(screen.getByLabelText('압력 하한 (bar)')).toBeVisible();
});

test('local digit learning progress and reset are shown for iRacing',async()=>{
  let state={...initial,enabled:true,running:true,car_path:'mclaren720sgt3',state_source:'dash_color',
    digit_reader:{mode:'learning',compared:120,needed:500,digits_learned:7,values_seen:3,values_needed:8,agreement:.99}};
  const fetcher=jest.fn(async(url)=>{
    if (url.includes('/digits/reset')) state={...state,digit_reader:{...state.digit_reader,compared:0,digits_learned:0,values_seen:0}};
    return response({...state});
  });
  render(<TireOverlaySettings fetcher={fetcher}/>);
  expect(await screen.findByText(/Windows OCR로 학습 중 · 비교 120\/500 · 숫자 7\/10 · 서로 다른 값 3\/8/)).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'현재 차량 숫자 학습 초기화'}));
  expect(await screen.findByText(/비교 0\/500 · 숫자 0\/10/)).toBeVisible();
  state={...state,digit_reader:{mode:'self',agreement:.998}};
  expect(await screen.findByText(/자체 인식 사용 중 · 학습 때 일치율 99.8%/,{},{timeout:3000})).toBeVisible();
});

test('pressure advice shows numbers with their basis, or why it is withheld',async()=>{
  let state={...initial,enabled:true,running:true,voice_enabled:true,advice_audio_ready:false,advice:{kind:'garage',status:'ready',
    track_temp_c:31.4,target_kpa:186,target_source:'dash_color',forecast:{basis:'forecast_with_current_offset',minutes_ahead:30,
      track_c:31.4,track_now_c:28,recent_misses_c:[1,-2]},wheels:{
      FL:{cold_kpa:158.6,model:'fitted',stints:4},FR:{cold_kpa:160,model:'fitted',stints:4},
      RL:{cold_kpa:150,model:'fitted',stints:3,warning:'below_lowest_used'},RR:{cold_kpa:null,reason:'no_evidence'}}}};
  const fetcher=jest.fn(async()=>response({...state}));
  render(<TireOverlaySettings fetcher={fetcher}/>);
  expect(await screen.findByText('출발 냉간 압력 추천')).toBeVisible();
  expect(screen.getByText(/1\.59 bar · 23\.0 psi/)).toBeVisible();
  expect(screen.getByText(/노면 31\.4°C 기준 · 목표 열간 1\.86 bar \(대시 색 학습\) · 근거 스틴트 4개 · 여러 스틴트 회귀/)).toBeVisible();
  expect(screen.getByText('근거 없음')).toBeVisible();
  expect(screen.getByText(/Weather 예보 기준 약 30분 뒤 노면 31\.4°C \(지금 28\.0°C\) · 지금의 노면-기온 차이로 추정 · 최근 예보 오차 평균 1\.5°C/)).toBeVisible();
  expect(screen.getByRole('note')).toHaveTextContent('셋업 하한');
  expect(screen.getByText(/추천 안내 음성은 아직 준비되지 않았습니다/)).toBeVisible();
  state={...state,advice:{kind:'next_pit',status:'withheld',reason:'fixed_or_unknown_setup'}};
  expect(await screen.findByText(/고정 셋업이거나/,{},{timeout:3000})).toBeVisible();
  expect(screen.getByText('다음 피트 냉간 압력 추천')).toBeVisible();
});

test('missing tire clips are stated and ranges can be set only for the measured car',async()=>{
  const fetcher=fixture();fetcher.mockImplementationOnce(async()=>response({...initial,voice_enabled:true,
    voice_audio_ready:false,car_path:'bmwm4gt3',car_name:'BMW M4 GT3',targets:{}}));
  render(<TireOverlaySettings fetcher={fetcher}/>);
  expect(await screen.findByText('타이어 안내 음원이 아직 준비되지 않았습니다.')).toBeVisible();
  fireEvent.click(screen.getByText(/차량별 음성 판단 범위/));
  expect(screen.getByLabelText('온도 하한 (°C)')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('압력 하한 (bar)'),{target:{value:'1.6'}});
  fireEvent.change(screen.getByLabelText('압력 상한 (bar)'),{target:{value:'1.8'}});
  fireEvent.click(screen.getByRole('button',{name:'현재 차량 범위 저장'}));
  await waitFor(()=>expect(fetcher).toHaveBeenCalledWith(expect.stringContaining('/targets'),expect.objectContaining({
    body:JSON.stringify({car:'bmwm4gt3',pressure_low_bar:1.6,pressure_high_bar:1.8,temp_low_c:null,temp_high_c:null}),
  })));
});
