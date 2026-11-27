import React from 'react';
import {render,screen,fireEvent} from '@testing-library/react';
import LocalSpeechHistory from './LocalSpeechHistory';
test('history shows ten rows per page, searches and recovers after a shorter refresh',()=>{
 const notes=Array.from({length:25},(_,i)=>({id:String(i),created_at:i,text:`질문 ${i}`,reply:'답변',context:{local_question:{result:'prepared',total_ms:100}}}));
 const view=render(<LocalSpeechHistory notes={notes}/>);
 expect(screen.getAllByRole('listitem')).toHaveLength(10);
 fireEvent.click(screen.getByRole('button',{name:'다음'}));
 expect(screen.getByText('인식: 질문 10')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'다음'}));
 expect(screen.getAllByRole('listitem')).toHaveLength(5);
 expect(screen.getByRole('button',{name:'다음'})).toBeDisabled();
 fireEvent.change(screen.getByLabelText('음성 인식 기록 검색'),{target:{value:'질문 24'}});
 expect(screen.getAllByRole('listitem')).toHaveLength(1);
 expect(screen.getByText('1 / 1')).toBeInTheDocument();
 view.rerender(<LocalSpeechHistory notes={[]}/>);
 expect(screen.getByText('아직 로컬 질문 기록이 없습니다.')).toBeInTheDocument();
});

test('history distinguishes timed-out clarification from corrected and repeated requests',()=>{
 const notes=[
  {id:'one',created_at:1,text:'얼마나 남았어',reply:'경기 남은 시간 말씀이신가요?',context:{local_question:{result:'clarification_needed',total_ms:100,dialogue:{action:'clarification'},dialogue_outcome:{status:'timeout',confirmed:null}}}},
  {id:'two',created_at:2,text:'아니 뒤차 간격',reply:'뒤차 2초입니다.',context:{local_question:{result:'prepared',total_ms:100,dialogue:{action:'corrected',resolved_text:'뒤차간격'},dialogue_outcome:{status:'repeat'}}}},
 ];
 render(<LocalSpeechHistory notes={notes}/>);
 expect(screen.getByText('후속 응답: 시간 초과로 대기 종료')).toBeInTheDocument();
 expect(screen.getByText('대화 처리: 질문 정정')).toBeInTheDocument();
 expect(screen.getByText('이해한 질문: 뒤차간격')).toBeInTheDocument();
 expect(screen.getByText('후속 응답: 다시 듣기 요청')).toBeInTheDocument();
});
