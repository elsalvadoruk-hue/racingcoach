import React from 'react';
import {fireEvent,render,screen} from '@testing-library/react';
import LocalSpeechDialogue from './LocalSpeechDialogue';

test('confirmation shows its question, PTT requirement and cancellation without starting the microphone',()=>{
  const radio={dialogue:{prompt:'경기 남은 시간 말씀이신가요?',remaining_ms:8000},cancelDialogue:jest.fn(),start:jest.fn()};
  const view=render(<LocalSpeechDialogue radio={radio}/>);
  expect(screen.getByLabelText('음성 질문 확인 대기')).toHaveTextContent('8초 안에 질문 단축키');
  expect(screen.getByText(/마이크는 단축키나 녹음 버튼/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'확인 대기 취소'}));
  expect(radio.cancelDialogue).toHaveBeenCalledTimes(1);expect(radio.start).not.toHaveBeenCalled();
  view.rerender(<LocalSpeechDialogue radio={{dialogueNotice:'응답이 없어 확인 대기를 취소했습니다.'}}/>);
  expect(screen.queryByLabelText('음성 질문 확인 대기')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('취소');
});
