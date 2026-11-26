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
