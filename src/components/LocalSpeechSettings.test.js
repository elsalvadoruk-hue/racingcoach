import LocalSpeechHistory from './LocalSpeechHistory';
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import LocalSpeechSettings from './LocalSpeechSettings';

const originalFetch = global.fetch;
const controller = () => ({ mode: 'local', model: 'base', owner: 'test-owner', changeMode: jest.fn(), setModel: jest.fn() });
beforeEach(() => {
  global.fetch = jest.fn(async url => ({ ok: true, json: async () => url.endsWith('/questions') ? {
    questions: [{ id: 'fuel_range', name: '주행 가능 랩', examples: ['기름으로 몇 바퀴 더 타'], game: 'both' }],
  } : { installed: { base: true }, state: 'off', setup: 'idle' } }));
});
afterEach(() => { global.fetch = originalFetch; });

test('noise addon installs explicitly without switching or reinstalling the speech model',async()=>{
  global.fetch=jest.fn(async url=>({ok:true,json:async()=>url.endsWith('/questions')?{questions:[]}:
    {installed:{sensevoice:true},enhancement_installed:false,setup:'idle'}}));
  const control={...controller(),model:'sensevoice'};
  render(<LocalSpeechSettings apiBase="http://test" controller={control}/>);
  fireEvent.click(await screen.findByRole('button',{name:'소음 재인식 준비 · 약 0.6MB'}));
  await waitFor(()=>expect(global.fetch.mock.calls.some(([url])=>url.endsWith('/install'))).toBe(true));
  expect(JSON.parse(global.fetch.mock.calls.find(([url])=>url.endsWith('/install'))[1].body)).toMatchObject({model:'sensevoice',enhancement_only:true});
  expect(control.setModel).not.toHaveBeenCalled();
});

test('question search includes report contents and partial answers explain missing items', async () => {
  global.fetch.mockImplementation(async url => ({ok: true, json: async () => url.endsWith('/questions') ? {questions: [
    {id:'report_pit',name:'피트 전략 보고',group:'종합 보고',examples:['피트 상황 요약해줘'],includes:['이번 피트 추가 급유량','피트 통과 손실']},
    {id:'ahead',name:'앞차 간격',group:'경쟁차',examples:['앞차 몇 초야']},
  ]} : {installed:{base:true}}}));
  render(<LocalSpeechHistory notes={[{
    id:'partial',created_at:1,reply:'현재 클래스 9위입니다.',service_error:'주행 데이터 부족: 경기 남은 시간',
    context:{local_question:{result:'prepared_partial',total_ms:100}},
  }]}/>);
  render(<LocalSpeechSettings apiBase="http://test" controller={controller()}/>);
  await screen.findByText('질문 예시 · 2가지');
  fireEvent.change(screen.getByLabelText('질문 찾기'), {target:{value:'급유'}});
  expect(screen.getByText('피트 전략 보고')).toBeInTheDocument();
  expect(screen.queryByText('앞차 간격')).not.toBeInTheDocument();
  expect(screen.getByText('보고 항목: 이번 피트 추가 급유량 → 피트 통과 손실')).toBeInTheDocument();
  expect(screen.getByText('일부 답변 음성 준비 · 처리 0.10초')).toBeInTheDocument();
  expect(screen.getByText('주행 데이터 부족: 경기 남은 시간')).toBeInTheDocument();
});

test('installed model exposes preparation status, examples and failed questions', async () => {
  render(<LocalSpeechHistory notes={[{
    id: 'one', created_at: 1, text: '알 수 없는 질문', reply: '',
    context: { local_question: { result: 'unsupported', total_ms: 500 } },
  }]} />);
  render(<LocalSpeechSettings apiBase="http://test" controller={controller()}/>);
  await screen.findByText('설치 완료 · 음성 인식을 켜면 모델을 불러옵니다.');
  expect(screen.getByText('기름으로 몇 바퀴 더 타')).toBeInTheDocument();
  expect(screen.getByText('인식: 알 수 없는 질문')).toBeInTheDocument();
  expect(screen.getByText('지원 질문 미확인 · 처리 0.50초')).toBeInTheDocument();
  expect(global.fetch.mock.calls.every(([url]) => !url.endsWith('/install'))).toBe(true);
});

test('installation is explicit and failures have a retry action', async () => {
  global.fetch.mockImplementation(async url => ({ ok: !url.endsWith('/install'), json: async () =>
    url.endsWith('/install') ? { detail: '네트워크 연결 확인' } : { installed: {}, questions: [] } }));
  render(<LocalSpeechSettings apiBase="http://test" controller={controller()} />);
  fireEvent.click(screen.getByRole('button', { name: '로컬 인식기 받기' }));
  await screen.findByText('네트워크 연결 확인');
  expect(screen.getByRole('button', { name: '로컬 인식기 받기' })).toBeEnabled();
  expect(JSON.parse(global.fetch.mock.calls.find(([url]) => url.endsWith('/install'))[1].body).model).toBe('base');
});

test('model and engine cannot change while microphone is armed', async () => {
  const control = controller();
  const view = render(<LocalSpeechSettings apiBase="http://test" controller={control} armed />);
  expect(screen.queryByLabelText('인식 방식')).not.toBeInTheDocument();
  expect(screen.getByLabelText('인식 모델')).toBeDisabled();
  view.rerender(<LocalSpeechSettings apiBase="http://test" controller={control} />);
  fireEvent.change(screen.getByLabelText('인식 모델'), { target: { value: 'sensevoice' } });
  expect(control.setModel).toHaveBeenCalledWith('sensevoice');
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
});

test('history shows corrections and withheld explanations without changing original text',async()=>{
 render(<LocalSpeechHistory notes={[
  {id:'one',created_at:1,text:'기차 간격',context:{local_question:{result:'not_connected',total_ms:50,
   normalized_text:'뒤차 간격',corrections:[{from:'기차',to:'뒤차',reason:'레이스 문맥 일치'}]}}},
  {id:'two',created_at:2,text:'기차 시간',context:{local_question:{result:'unsupported',total_ms:40,
   corrections:[],correction_note:'레이스 문맥이 부족해 보류했습니다.'}}}
 ]}/>);
 expect(screen.getByText('인식: 기차 간격')).toBeInTheDocument();
 expect(screen.getByText('보정 문장: 뒤차 간격')).toBeInTheDocument();
 expect(screen.getByText('레이스 문맥이 부족해 보류했습니다.')).toBeInTheDocument();
 expect(screen.queryByText('보정 문장: 뒤차 시간')).not.toBeInTheDocument();
});
