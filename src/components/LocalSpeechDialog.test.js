import React from 'react';
import {fireEvent,render,screen} from '@testing-library/react';
import LocalSpeechDialog from './LocalSpeechDialog';
jest.mock('./LocalSpeechSettings',()=>()=> <div>모델 설치 설정</div>);
jest.mock('./SpeechShortcutField',()=>()=> <div>로컬 단축키</div>);

test('pit commands are explicit, locked while busy and unavailable in LMU',()=>{
 const radio={engine:{},phase:'idle',game:'iracing',pitCommands:false,setPitCommands:jest.fn(),speak:true};
 const view=render(<LocalSpeechDialog apiBase="http://test" radio={radio}/>);
 fireEvent.click(screen.getByRole('button',{name:'음성 인식',exact:true}));
 fireEvent.click(screen.getByText('피트 명령·마이크 상세 설정'));
 const toggle=screen.getByRole('switch',{name:'iRacing 피트 명령 보내기'});
 expect(toggle).not.toBeChecked();expect(toggle).toBeEnabled();
 fireEvent.click(toggle);expect(radio.setPitCommands).toHaveBeenCalledWith(true);
 view.rerender(<LocalSpeechDialog apiBase="http://test" radio={{...radio,busy:true}}/>);
 expect(toggle).toBeDisabled();
 view.rerender(<LocalSpeechDialog apiBase="http://test" radio={{...radio,game:'lmu'}}/>);
 expect(toggle).toBeDisabled();
});

test('local echo cancellation is explicit, reports the applied value and is disabled while busy',()=>{
 const radio={engine:{},phase:'idle',enabled:true,shortcut:{},speak:true,echoCancellation:false,setEchoCancellation:jest.fn(),
 inputProcessing:{effective:{echoCancellation:false}},cancelTest:jest.fn()};
 const view=render(<LocalSpeechDialog apiBase="http://test" radio={radio}/>);
 fireEvent.click(screen.getByRole('button',{name:'음성 인식',exact:true}));
 fireEvent.click(screen.getByText('피트 명령·마이크 상세 설정'));
 const toggle=screen.getByRole('switch',{name:'로컬 마이크 에코 제거'});
 expect(toggle).not.toBeChecked();
 expect(screen.getByText('브라우저 적용값: 에코 제거 꺼짐')).toBeInTheDocument();
 fireEvent.click(toggle);
 expect(radio.setEchoCancellation).toHaveBeenCalledWith(true);
 view.rerender(<LocalSpeechDialog apiBase="http://test" radio={{...radio,busy:true}}/>);
 expect(toggle).toBeDisabled();
});
test('local recognition opens separately and exposes an offline microphone test',()=>{
 const radio={engine:{},phase:'idle',enabled:true,shortcut:{},speak:true,prepare:jest.fn(),start:jest.fn(),startTest:jest.fn(),cancelTest:jest.fn(),
 testResult:{text:'연료 얼마 남았어',topic_name:'현재 연료',total_ms:300}};
 render(<LocalSpeechDialog apiBase="http://test" radio={radio}/>);
 expect(screen.queryByText('모델 설치 설정')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'음성 인식',exact:true}));
 expect(screen.getByRole('dialog')).toHaveTextContent('Google 질문은 별도입니다.');
 fireEvent.click(screen.getByRole('button',{name:'인식 테스트 시작'}));
 expect(radio.startTest).toHaveBeenCalledTimes(1);
 expect(screen.getByLabelText('음성 인식 테스트 결과')).toHaveTextContent('연료 얼마 남았어');
 fireEvent.click(screen.getByRole('button',{name:'닫기'}));
 expect(radio.cancelTest).toHaveBeenCalledTimes(1);
});

test('uncertain audio is distinguished from an unsupported question in the test result',()=>{
 const radio={engine:{},phase:'idle',enabled:true,shortcut:{},speak:true,cancelTest:jest.fn(),
 testResult:{text:'',topic_name:'',audio_reason:'uncertain_audio',total_ms:410}};
 render(<LocalSpeechDialog apiBase="http://test" radio={radio}/>);
 fireEvent.click(screen.getByRole('button',{name:'음성 인식',exact:true}));
 expect(screen.getByLabelText('음성 인식 테스트 결과')).toHaveTextContent('음성 인식 불확실');
 expect(screen.getByLabelText('음성 인식 테스트 결과')).not.toHaveTextContent('지원 질문 미확인');
});

test('test keeps recognition text visible beside the corrected question and reason',()=>{
 const radio={engine:{},phase:'idle',enabled:true,shortcut:{},speak:true,
 testResult:{text:'기차 간격',normalized_text:'뒤차 간격',topic_name:'뒤차 간격',total_ms:300,
 corrections:[{from:'기차',to:'뒤차',reason:'뒤차 문맥 일치'}]}};
 render(<LocalSpeechDialog apiBase="http://test" radio={radio}/>);
 fireEvent.click(screen.getByRole('button',{name:'음성 인식',exact:true}));
 const result=screen.getByLabelText('음성 인식 테스트 결과');
 expect(result).toHaveTextContent('인식: 기차 간격');
 expect(result).toHaveTextContent('보정 문장: 뒤차 간격');
 expect(result).toHaveTextContent('기차 → 뒤차 · 뒤차 문맥 일치');
});
