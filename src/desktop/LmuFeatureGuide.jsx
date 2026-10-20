import React from 'react';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '../components/ui/table';

// LMU recordings run through the iRacing analysis, and the live coach reads LMU
// through the same service, so most rows are simply "same as iRacing".
export const lmuFeatures = [
  ['최근 주행·파일·랩 선택', '사용 가능', 'summary', '최근 종료 기록 자동 열기, 다른 파일의 기준 랩 선택'],
  ['오늘의 연습 과제·다음 주행 평가', 'iRacing과 같음', 'summary', '코너 원인(제동·릴리즈·풀스로틀·ABS·재리프트)과 바꿀 조작, 다음 기록 분석 때 입력·시간 확인'],
  ['코너 비교·비교 포인트·ABS 표시', 'iRacing과 같음', 'corners', '같은 거리에서 내 랩과 기준 랩, 자동 구간과 확정 코너 번호'],
  ['주행선·고스트', '일부 지원', 'corners', 'LMU 기록 좌표는 실제 지도와 맞지 않아 위성 배경 없이 개략도로 표시'],
  ['코너 번호 확정', 'iRacing과 같음', 'corners', '몬차·르망·스파·이몰라·포르티망·세브링·COTA·인터라고스·후지·실버스톤은 iRacing 공식 지도로 자동 제안'],
  ['랩별 시간 쓰임새·코너별 일관성·랩 기록', 'iRacing과 같음', 'laps', '제동·부분·코스팅·풀스로틀, 코너별 분포, 최적 랩'],
  ['여러 랩·여러 파일 겹침', 'iRacing과 같음', 'expert', '속도·페달·조향·기어·시간 차이'],
  ['차량 상태(타이어·서스펜션)', 'LMU 전용', 'expert', '기록된 차량 채널 통계와 두 랩 비교'],
  ['셋업 조회·A/B 변경 항목', 'LMU 전용', 'setup', '기록에 저장된 셋업 값과 두 파일의 변경 항목'],
  ['셋업 추천·효과 학습', '지원 안 함', 'setup', 'LMU 셋업 항목의 의미와 범위가 iRacing 셋업 분석과 달라 연결하지 않음'],
  ['주행 분석 저장·검색·다시 열기', '사용 가능', 'history', '파일·내 랩·기준 랩과 메모 저장'],
  ['스포터(좌우·클리어·접근)', 'iRacing과 같음', 'live', '주변 차 좌표로 좌우 겹침을 계산해 iRacing 스포터에 전달. 차량 크기는 공통 근사값'],
  ['피트월·포메이션 브리핑·깃발·순위·연료', 'iRacing과 같음', 'live', '급유 설정값·수리 시간·인시던트 점수처럼 LMU가 공개하지 않는 값의 안내는 나오지 않음'],
  ['실시간 코너 코칭·구간 델타', 'iRacing과 같음', 'live', '같은 차·트랙 기록을 한 번 분석하면 코너 정의가 저장되어 사용'],
  ['F8 질문·AI 답변', 'iRacing과 같음', 'live', 'Google 음성 키가 있으면 같은 방식으로 동작'],
  ['오버레이(페달 그래프·카드)', 'iRacing과 같음', 'settings', 'Garage61 기준 그래프는 iRacing 전용이라 LMU에서는 속도·페달 표시'],
  ['구간 반복 연습', '일부 지원', 'summary', '측정·비프는 같음. iRacing Active Reset 같은 되감기 기능이 LMU에 없어 매 랩 통과로 측정'],
  ['Garage61 외부 기준', '지원 안 함', 'settings', 'Garage61은 iRacing 랩만 제공'],
];

export default function LmuFeatureGuide({ onNavigate }) {
  return <><h3>기능별 지원</h3><p>LMU 기록은 iRacing과 같은 분석을 거칩니다. 실시간 음성 코치도 같은 서비스가 LMU를 읽습니다.</p>
    <Table><TableHeader><TableRow><TableHead>기능</TableHead><TableHead>지원</TableHead><TableHead>설명</TableHead><TableHead>화면</TableHead></TableRow></TableHeader><TableBody>
      {lmuFeatures.map(([name, status, destination, note]) => <TableRow key={name}><TableCell>{name}</TableCell><TableCell><Badge variant={status === '지원 안 함' ? 'outline' : 'secondary'}>{status}</Badge></TableCell><TableCell>{note}</TableCell><TableCell>{onNavigate && <Button variant="ghost" size="sm" onClick={() => onNavigate(destination)}>{name} 열기</Button>}</TableCell></TableRow>)}
    </TableBody></Table></>;
}
