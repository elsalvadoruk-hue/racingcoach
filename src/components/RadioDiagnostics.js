import { Button } from './ui/button';
import React, { useState } from 'react';

const labels = { started: '재생 시작', completed: '재생 완료', interrupted: '중단', expired: '기한 만료', cancelled: '취소', failed: '실패', muted: '문자만 전달' };
const reasons = { preempted: '더 우선인 무전으로 전환', nearby_car: '옆 차량 안내 우선', realtime_state_changed: '실시간 상황 변경',
  driver_or_radio_pause: '질문 또는 발화 중지', output_removed: '출력장치 연결 끊김', output_changed: '출력장치 변경', page_closed: '페이지 종료',
  media_error: '오디오 재생 오류', output_routing: '출력장치 연결 실패', no_playback_window: '전달 구간을 찾기 전에 기한 만료',
  condition_or_session_changed: '상황 조건 또는 세션 변경', spotter_disabled: '스포터 중지', connection_lost: '실시간 연결 끊김',
  volume_or_mute: '음소거 또는 볼륨 0', text_only: '문자 전달 설정', audio_preparation: '음성 준비 실패', stopped: '재생 중지' };
export default function RadioDiagnostics({ apiBase }) {
  const [events, setEvents] = useState([]), [error, setError] = useState('');
  const load = async () => {
    try {
      const response = await fetch(`${apiBase}/api/live-coach/radio-delivery?limit=100`);
      if (!response.ok) throw new Error('무전 기록을 읽지 못했습니다.');
      const data = await response.json(); setEvents(data.events || []); setError('');
    } catch (failure) { setError(failure.message); }
  };
  return <details className="coach-panel mt-4">
    <summary className="cursor-pointer font-semibold text-foreground">무전 전달 기록 · 경기 후 확인</summary>
    <p className="mt-3 text-xs text-muted-foreground">스포터·엔지니어의 재생 시작, 완료, 중단과 누락 이유를 저장합니다. 음성 원본은 저장하지 않습니다.</p>
    <Button variant="outline" type="button" onClick={load} className="mt-3  border  px-3 py-2 text-sm ">전달 기록 불러오기</Button>
    <a href={`${apiBase}/api/live-coach/radio-delivery?limit=5000`} target="_blank" rel="noreferrer" className="ml-4 text-sm text-primary underline">상세 기록 JSON 열기</a>
    {error && <p role="alert" className="mt-3 text-destructive">{error}</p>}
    <div className="mt-3 max-h-80 flex flex-col gap-2 overflow-y-auto">{events.map((event) => <div key={event.id} className="rounded-lg bg-background/50 p-3 text-xs">
      <p className="text-muted-foreground">{new Date((event.at || event.recorded_at) * 1000).toLocaleTimeString('ko-KR')} · {labels[event.status] || event.status}{event.lap_number != null ? ` · ${event.lap_number}랩` : ''}</p>
      <p className="mt-1 text-foreground">{event.text || event.key}</p>{event.reason && <p className="mt-1 text-muted-foreground">{reasons[event.reason] || event.reason}</p>}
    </div>)}</div>
  </details>;
}
