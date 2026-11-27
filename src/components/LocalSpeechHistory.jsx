import React,{useState} from 'react';
import {Button} from './ui/button';
import {Input} from './ui/input';
import LocalSpeechCorrection from './LocalSpeechCorrection';
export const QUESTION_RESULTS = {
  recognized:'인식 완료', matched:'질문 연결',
  prepared: '답변 음성 준비', text_only: '텍스트 답변', no_speech: '목소리 없음',
  prepared_partial: '일부 답변 음성 준비', text_partial: '일부 텍스트 답변',
  uncertain_audio: '인식 불확실', unrecognized: '인식 실패', recognition_failed: '인식 처리 실패',
  unsupported: '지원 질문 미확인', ambiguous: '질문 불명확', unsupported_game: '다른 게임 전용',
  data_unavailable: '주행 데이터 부족', audio_unavailable: '답변 음원 없음', stale: '상황 변경으로 취소',
  not_connected: '인식 완료 · 차량 연결 없음',
  clarification_pending:'확인 응답 대기', clarification_cancelled:'확인 대기 취소',
  clarification_expired:'확인 대기 시간 초과', context_expired:'이전 질문 시간 초과',
  clarification_needed:'확인 응답 대기',clarification_rejected:'질문 다시 말하기',
  dialogue_expired:'이어 묻기 시간 초과',dialogue_missing:'이어 답할 질문 없음',
  dialogue_cancelled:'이어 묻기 취소',dialogue_target_changed:'비교 대상 변경',
  command_pending: '명령 처리 결과 미확인 · 자동 재전송 안 함', command_applied:'게임 설정 반영 확인',
  command_unconfirmed:'명령 전송 결과 확인 필요', command_blocked:'명령 전송 안 함', command_disabled:'피트 명령 꺼짐',
};

export default function LocalSpeechHistory({notes=[]}) {
const [page,setPage]=useState(0),[search,setSearch]=useState('');
const filtered=notes.filter(note=>note.context?.local_question && [note.text,note.reply,note.context.local_question.topic_name].join(' ').includes(search));
const currentPage=Math.min(page,Math.max(0,Math.ceil(filtered.length/10)-1));
const rows=filtered.slice(currentPage*10,currentPage*10+10);
return <><Input aria-label="음성 인식 기록 검색" placeholder="말한 문장·질문·답변 검색" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}}/>        <section aria-label="로컬 음성 인식 기록"><p>로컬 음성 인식 기록 · {filtered.length}건</p>
          <p className="text-sm text-muted-foreground">인식 원문·보정·연결 질문·답변과 인식 테스트를 확인합니다. 뒷차·뒤 차량 같은 표현을 통일하고, 등록된 오인식은 레이스 문맥이 맞을 때만 보정합니다. 재생 완료 여부는 세션 무전 이력에서 확인하세요. 로그 보내기 동의 후 기록은 개선용으로 자동 전송합니다. 원음은 저장하지 않습니다.</p>
          {!rows.length && <p>아직 로컬 질문 기록이 없습니다.</p>}
          <ol className="flex flex-col gap-4">{rows.map(note => {
            const log = note.context.local_question;
            return <li key={note.id}><time>{new Date(note.created_at * 1000).toLocaleTimeString('ko-KR')}</time>
              <p>인식: {note.text || '인식된 문장 없음'}</p><p>질문: {log.test ? "인식 테스트" : log.topic_name || '선택하지 못함'}</p>
              <LocalSpeechCorrection detail={log}/>
              {log.dialogue?.resolved_text && <p>이해한 질문: {log.dialogue.resolved_text}</p>}
              {log.dialogue?.action && log.dialogue.action!=='standalone' && <p>대화 처리: {({confirmed:'확인 응답',corrected:'질문 정정',followup:'후속 질문',explicit_followup:'추가 질문',repeat:'다시 듣기',short:'짧게 듣기',explain:'자세히 듣기',switch_target:'앞뒤 차량 전환',clarified:'질문 선택',clarification:'확인 질문',rejected:'확인 내용 부정',orphan_response:'이어 답할 질문 없음',target_changed:'비교 대상 변경',cancelled:'대기 취소',expired:'대기 시간 초과',unrecognized:'질문 연결 미확인'})[log.dialogue.action] || '추가 질문 처리'}</p>}
              {log.dialogue_outcome?.status && <p>후속 응답: {({timeout:'시간 초과로 대기 종료',cancelled:'사용자가 취소함',state_changed:'상태 변경으로 대기 종료',recording_failed:'녹음 실패로 대기 종료',playback_failed:'재생 실패로 대기 종료',disabled:'음성 인식 종료',confirmed:'사용자가 확인함',rejected:'사용자가 부정함',corrected:'사용자가 질문을 정정함',clarified:'사용자가 질문을 선택함',repeat:'다시 듣기 요청',short:'짧게 듣기 요청',explain:'추가 설명 요청',switch_target:'앞뒤 차량 변경 질문',followup:'이어 질문함',explicit_followup:'새 질문을 덧붙임'})[log.dialogue_outcome.status] || '이어지는 요청 있음'}</p>}
              <p>답변: {note.reply || '없음'}</p><p>{QUESTION_RESULTS[log.result] || log.result} · 처리 {(log.total_ms / 1000).toFixed(2)}초</p>
              {note.service_error && <p>{note.service_error}</p>}
            </li>;
          })}</ol>
        <div className="flex items-center gap-3"><Button variant="outline" disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}>이전</Button><span>{currentPage+1} / {Math.max(1,Math.ceil(filtered.length/10))}</span><Button variant="outline" disabled={(currentPage+1)*10>=filtered.length} onClick={()=>setPage(currentPage+1)}>다음</Button></div></section></>;
}
