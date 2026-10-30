// Samples for the explicitly labelled preview. Live values are supplied by the backend.
export const sessions = {
  practice: { name: '연습', en: 'PRACTICE', title: '켜고 달리면, 조작 관측부터.', description: '과제 없이 자동 관측하고, 코너 비교 자료가 준비되면 결과도 함께 표시합니다.', preset: '자동 주행 코칭', icon: 'target', slots: ['live', 'score', 'pattern', 'consistency'] },
  qualify: { name: '퀄리', en: 'QUALIFYING', title: '한 랩을 완성하는 데 집중하세요.', description: '자동 조작 관측과 준비된 비교 기록으로 한 랩을 확인합니다.', preset: '한 랩 완성', icon: 'timer', slots: ['live', 'score', 'personal', 'loss'] },
  race: { name: '레이스', en: 'RACE', title: '빠른 한 번보다, 꾸준한 페이스.', description: '과제 없이 조작을 관측하고, 반복 통과와 접촉 전후 변화를 살펴봅니다.', preset: '꾸준한 페이스', icon: 'flag', slots: ['live', 'stint', 'consistency', 'impact'] },
  formation: { name: '포메이션', en: 'FORMATION', title: '출발 정보와 지난번 기록을 확인하세요.', description: '출발 순위는 고정하고 준비된 정보를 순환합니다. 음성·분석·과제 없이 현재 자료를 표시하며 그린에 종료합니다.', preset: '출발 준비', icon: 'flag', slots: ['reference', 'live', null, null] },
};

export const formationItems = [
  ['1', '공식 출발 순위', '현재 순위로 대체하지 않음 · 항상 표시'],
  ['6', '공식 정렬 안내', '게임의 공식 열·행·따라갈 차량 입력', 'iracing'],
  ['8', '현재 연료', '현재 관측 리터 · 0도 표시'],
  ['9', '현재 날씨', '노면·기온·젖음 관측과 게임 선언'],
  ['10', '바퀴별 온도', 'FL/FR/RL/RR 타이어·브레이크 실측 °C', 'lmu'],
  ['14', '지난 경기 기록', '지난번 돌아보기 · 날짜와 경기 베스트'],
  ['15', '클래스 필드', '유효 iRating이 모두 확인된 같은 클래스 SoF', 'iracing'],
  ['16', '그리드 앞뒤 차량', '공식 출발 슬롯의 차량·퀄리 기록'],
  ['17', '해당 경기 퀄리', '같은 경기 퀄리 · 레이스 베스트와 별도'],
  ['20', '경기 규정', '게임이 제공한 규정만 표시'],
  ['21', '지난 관측 구간', '지난번 돌아보기 · 저장된 분석의 근거 있는 구간'],
  ['22', '가상 에너지', '현재 유효 잔량 %', 'lmu'],
  ['23', 'iRating 상승 기준 추정', '유지 기준과 구분 · 공식 증감 보장 없음', 'iracing'],
  ['24', '내 유효 랩 기록', '지난번 돌아보기 · 개인 베스트와 조건이 맞는 최근 중앙값'],
  ['25', '출발부터 결과까지', '지난번 돌아보기 · 같은 경기·순위 기준·결과 상태'],
  ['27', '내 트랙 메모', '지난번 돌아보기 · 직접 쓴 문장과 날짜 · 수정·삭제 반영'],
].map(([id, name, desc, game]) => ({ id, name, desc, game }));
export const formationDefaults = ['8', '9', '10', '14', '15', '16', '17', '20', '21', '22', '23', '24', '25', '27'];

export const modules = [
  { id: 'live', number: 'AUTO', name: '자동 주행 코칭', desc: '과제·비교 기록 없이 조작 관측', group: '자동', sessions: ['practice', 'qualify', 'race', 'formation'], title: '직전 제동 해제 → 가속 연결', detail: '해제 5% 이하 → 가속 25% 이상 · 각 150ms 유지한 입력에서 관측', metric: '0.42', unit: 's', tone: 'neutral', stats: [['최근 무입력', '1.20s'], ['동시 입력', '0.18s']] },
  { id: 'score', number: '01', name: '직전 코너 성적표', desc: '시간·최저 속도·탈출 속도', group: '비교', sessions: ['practice', 'qualify', 'race'], title: 'C4 · 탈출에서 0.12초 개선', detail: '같은 트랙 위치의 기준 기록과 비교합니다.', metric: '−0.120', unit: 's', tone: 'good', stats: [['최저 속도', '+1.2 km/h'], ['탈출 속도', '+2.4 km/h']], ref: true },
  { id: 'loss', number: '02', name: '최대 손실 구간', desc: '다음에 집중할 코너 찾기', group: '비교', sessions: ['practice', 'qualify', 'race'], title: '다음 시도는 C8에 집중', detail: '이번 랩에서 가장 큰 관측 손실입니다.', metric: '+0.190', unit: 's', tone: 'warn', stats: [['진입', '+0.02 s'], ['탈출', '+0.17 s']], ref: true },
  { id: 'divergence', number: '03', name: '처음 달라진 조작', desc: '기준과 달라진 순서 보기', group: '비교', sessions: ['practice', 'qualify'], title: 'C4 · 제동 시작부터 차이 관측', detail: '비교 가능한 조작 중 첫 차이 · 최소 5m 및 샘플 해상도 기준', metric: '8.0', unit: 'm 늦음', tone: 'warn', stats: [['브레이크 해제', '+12.0m'], ['풀스로틀', '+6.0m']], ref: true },
  { id: 'goal', number: '04', name: '다음 코너 연습 목표', desc: '바꿀 한 가지를 크게 표시', group: '연습', sessions: ['practice', 'qualify', 'formation'], title: '브레이크 해제만 앞당기세요', detail: 'C4 · 제동 시작 위치는 그대로 유지하세요.', metric: '5', unit: 'm 앞에서', tone: 'good', stats: [['이번에 유지', '제동 시작'], ['이번에 변경', '해제 위치']] },
  { id: 'change', number: '05', name: '바꾼 조작과 결과', desc: '변화와 통과 기록 나란히', group: '연습', sessions: ['practice'], title: '해제 지점 5m 빨라짐', detail: '조작 변화와 결과를 함께 관측했습니다.', metric: '−0.070', unit: 's', tone: 'good', stats: [['탈출 속도', '+1.8 km/h'], ['재리프트', '관측 없음']], ref: true },
  { id: 'success', number: '06', name: '최근 시도 성공률', desc: '목표 달성을 반복하는지', group: '연습', sessions: ['practice'], title: '해제 위치 목표 3회 달성', detail: '조작 달성과 실제 페이스 개선은 따로 확인합니다.', metric: '3', unit: '/ 5회', tone: 'good', stats: [['연속 달성', '2회'], ['페이스 개선', '관측 2회']] },
  { id: 'consistency', number: '07', name: '코너별 일관성', desc: '제동·가속 위치의 편차', group: '진단', sessions: ['practice', 'race'], title: 'C4 · 제동 위치가 일정해졌습니다', detail: '최근 깨끗한 5회 통과에서 비교했습니다.', metric: '7', unit: 'm 범위', tone: 'good', stats: [['이전 5회', '14m 범위'], ['최근 5회', '7m 범위']] },
  { id: 'pattern', number: '08', name: '반복 조작 패턴', desc: '재리프트·재제동 관측', group: '진단', sessions: ['practice', 'race'], title: 'C7 · 재리프트 / 재제동', detail: '150ms 이상 유지한 입력 전환 · 깨끗한 통과 3회부터 표시', metric: '4 / 1', unit: '회 · 최근 5통과', tone: 'neutral', stats: [['이번 재리프트', '1회'], ['이번 재제동', '0회']] },
  { id: 'retention', number: '09', name: '개선 유지 여부', desc: '익힌 조작을 다음 랩에도', group: '연습', sessions: ['practice', 'qualify', 'race'], title: 'C2 · 개선한 탈출을 유지 중', detail: '풀스로틀 복귀 위치가 목표 범위 안에 있습니다.', metric: '3', unit: '회 연속', tone: 'good', stats: [['최근 통과', '목표 범위'], ['탈출 속도', '안정적']] },
  { id: 'phases', number: '10', name: '진입·회전·탈출 손익', desc: '코너 안의 손실 위치', group: '비교', sessions: ['practice', 'qualify'], title: 'C4 · 진입 / 회전 / 탈출', detail: '기준의 해제·가속 위치로 나눈 동일 거리의 시간 차이', metric: '+0.180', unit: 's', tone: 'warn', stats: [['진입', '−0.050s'], ['회전', '+0.030s'], ['탈출', '+0.200s']], ref: true },
  { id: 'personal', number: '11', name: '내 빠른 기록과 차이', desc: '비슷한 조건의 개인 기록', group: '비교', sessions: ['practice', 'qualify', 'race'], title: '내 빠른 기록보다 풀스로틀이 늦습니다', detail: '차량·레이아웃이 같은 개인 기록과 비교합니다.', metric: '9', unit: 'm 늦음', tone: 'warn', stats: [['비교 구간', 'C4 탈출'], ['기준 기록', '1:37.814']], ref: true },
  { id: 'common', number: '12', name: '빠른 통과의 공통 패턴', desc: '반복 관측된 특징 확인', group: '진단', sessions: ['practice'], title: 'C4 · 빠른 3회에서 관측', detail: '연료 10L·노면 5°C 이내 / 젖음 동일 · 인과관계 아님', metric: '3', unit: '/ 5통과', tone: 'neutral', stats: [['재제동', '3회 모두 없음'], ['풀스로틀 위치', '7.0m 범위']] },
  { id: 'stint', number: '13', name: '스틴트 페이스 변화', desc: '깨끗한 통과의 변화 추적', group: '진단', sessions: ['race'], title: '저속 코너에서 손실이 늘었습니다', detail: '최근 깨끗한 3랩 · 연료 조건 차이는 별도 확인이 필요합니다.', metric: '+0.240', unit: 's / lap', tone: 'warn', stats: [['저속 코너', '+0.24 s'], ['고속 구간', '비슷함']], ref: true },
  { id: 'impact', number: '14', name: '충돌 전후 관측 변화', desc: '최고속·직선 조향 비교', group: '진단', sessions: ['race'], title: '접촉·손상 신호 뒤 관측 변화', detail: '이전 2랩 중앙값 / 이후 1랩 · 조건 차이·손상 원인 미확정', metric: '−4.0', unit: 'km/h 최고속 차이', tone: 'warn', stats: [['랩타임 차이', '+0.20s'], ['직선 조향 차이', '+0.5°']] },
  { id: 'summary', number: '15', name: '이번 주행 개선 요약', desc: '개선·유지·재연습 정리', group: '진단', sessions: ['practice', 'qualify', 'race', 'formation'], title: 'C1·C4 개선 / C2 유지', detail: '다음 주행에서는 C7 가속 연결을 연습하세요.', metric: '2', unit: '구간 개선', tone: 'good', stats: [['개선 유지', 'C2'], ['재연습 후보', 'C7']] },
  { id: 'reference', number: '16', name: '비교 기준 준비 상태', desc: '출처와 확인한 조건 보기', group: '준비', sessions: ['formation', 'practice'], title: '비슷한 조건의 내 기록 준비됨', detail: '같은 차량·레이아웃 · 타이어 조건 일부 미확인', metric: '1:37.814', unit: '', tone: 'good', stats: [['노면', '건조'], ['연료 / 노면 온도', '차이 6L / 2°C']] },
];
export const byId = Object.fromEntries(modules.map(item => [item.id, item]));
export const slotNames = ['큰 영역', '보조 1', '보조 2', '잠깐 나타날 결과'];
export const phaseNames = { before: '코너 접근', after: '통과 후', lap: '랩 종료', formation: '출발 준비' };
export const phaseOrder = {
  before: ['goal', 'stint', 'retention', 'success', 'consistency', 'pattern', 'common', 'impact', 'live', 'reference'],
  after: ['score', 'divergence', 'phases', 'change', 'personal', 'pattern', 'common', 'impact', 'success', 'retention', 'consistency', 'live'],
  lap: ['summary', 'loss', 'phases', 'divergence', 'stint', 'pattern', 'common', 'impact', 'retention', 'consistency', 'success', 'live'],
  formation: ['reference', 'goal', 'live'],
};
export function selectedModules(config, session) {
  return [...new Set((config.selected?.[session] ?? config.slots?.[session] ?? sessions[session].slots).filter(id => byId[id]))];
}
export function moduleTiming(id, session) {
  return Object.entries(phaseOrder).filter(([phase, ids]) => ids.includes(id) && (session === 'formation' ? phase === 'formation' : phase !== 'formation')).map(([phase]) => phaseNames[phase]).join(' · ') || (byId[id]?.pending ? '준비 중' : '다른 세션에서 표시');
}
export function selectModules(config, session, scene = 'before', availability = 'personal') {
  const phase = session === 'formation' ? 'formation' : scene === 'lap' ? 'lap' : scene === 'after' && config.timing === 'pass' ? 'after' : 'before';
  const selected = selectedModules(config, session);
  const order = phase === 'before' && session === 'race' ? ['impact', 'stint', 'consistency', 'pattern', 'retention', 'common', 'goal', 'success', 'live', 'reference'] : phaseOrder[phase];
  const eligible = order.filter(id => selected.includes(id));
  const measured = eligible.filter(id => !byId[id].pending && (!byId[id].ref || availability !== 'none') && scene !== 'missing' && (scene !== 'start' || ['live', 'reference'].includes(id)));
  return {phase, ids: measured.length ? measured.slice(0, 3) : eligible.includes('live') ? ['live'] : eligible.slice(0, 1), selectedCount: selected.length,
    waiting: measured.length ? null : eligible.length ? 'data' : selected.some(id => !byId[id].pending) ? 'phase' : selected.length ? 'pending' : 'empty'};
}
export const games = { iracing: { name: 'iRacing', car: 'McLaren 720S GT3 EVO', track: 'Hockenheimring · Grand Prix', lap: '1:37.814' }, lmu: { name: 'LMU', car: 'BMW M4 GT3', track: 'Monza · Grand Prix', lap: '1:49.286' } };
export const moduleRequirements = {
  live: '과제·비교 기록·코너 분석 없이 작동합니다. 트랙에서 약 1초 이상 정상 주행하면 최근 10초 무입력·동시 입력 시간을 표시합니다. 제동 해제 후 가속을 관측하면 연결 시간도 표시합니다.',
  goal: '선택 기능입니다. 주행 파일 분석 → 주행 요약 → 다음 주행에서 할 일 → 이 과제로 연습 시작. 같은 차량·트랙·레이아웃의 연습 세션에서 연결됩니다.',
  success: '연습 목표를 연결하고 목표 구간을 통과하면 측정 가능한 시도의 목표 달성을 집계합니다. 페이스 개선과는 별도입니다.',
  change: '연습 목표를 연결한 뒤 목표 구간을 통과하면 측정한 직전 조작 결과를 표시합니다.',
  score: '같은 차량·트랙 파일을 한 번 분석해 코너 구간을 준비하고, 비교 기준을 연결한 뒤 코너를 깨끗하게 통과하세요.',
  loss: '기준과 비교한 코너 통과가 필요합니다. 이번·직전 랩에서 관측한 구간 중 최대 시간 차이를 표시합니다.',
  divergence: '코너 구간·비교 기준과 같은 위치의 연속 입력이 필요합니다. 관측 가능한 조작 중 첫 차이를 통과 후 표시합니다.',
  consistency: '같은 코너·같은 기준으로 제동 시작을 측정한 깨끗한 통과가 3회 필요합니다.',
  pattern: '비교 기록이나 과제는 필요하지 않습니다. 코너 구간을 준비하고 같은 코너의 연속 입력을 깨끗하게 3회 관측하면 표시합니다.',
  retention: '같은 코너를 같은 기준으로 깨끗하게 4회 통과하면 이전 통과의 중앙값과 이번 통과를 비교합니다.',
  phases: '코너 구간·비교 기준, 기준의 해제·가속 경계, 두 기록의 연속 구간 시간이 필요합니다.',
  personal: '비교할 기록을 내 빠른 기록으로 선택하세요. 같은 차량·레이아웃의 유효 기록과 코너 제동 시작을 비교합니다.',
  common: '과제·비교 기록 없이 가능합니다. 코너 구간을 준비하고 연료 10L·노면 5°C 이내, 젖음이 같은 깨끗한 통과 5회가 필요합니다.',
  stint: '같은 코너를 같은 기준으로 깨끗하게 3회 통과하면 최근 시간 차이의 중앙값을 표시합니다.',
  impact: '접촉·손상 신호 전 완전히 관측한 2랩과 신호 후 깨끗한 1랩이 필요합니다. 손상 부위나 원인은 판정하지 않습니다.',
  summary: '기준과 비교한 코너 통과 후 랩을 마치면 측정한 구간의 개선 수를 표시합니다.',
  reference: '실제 비교 출처와 준비 상태를 표시합니다. 기준이 없어도 자동 주행 코칭과 내 입력값 그래프는 사용할 수 있습니다.',
};
export const demoGoals = {
  abs: { name: 'ABS 개입', title: 'ABS가 길게 이어지는 구간을 줄여보세요', detail: 'C4 · 제동 시작은 유지하고 압력을 부드럽게 줄이세요.', metric: '한 가지', unit: '에 집중' },
  throttle: { name: '풀스로틀 위치', title: '풀스로틀 복귀를 앞당겨보세요', detail: 'C4 · 조향을 먼저 풀고 가속을 연결하세요.', metric: '5', unit: 'm 앞에서' },
  release: { name: '브레이크 해제', title: '브레이크 해제만 앞당기세요', detail: 'C4 · 제동 시작 위치는 그대로 유지하세요.', metric: '5', unit: 'm 앞에서' },
  coast: { name: '코스팅', title: '제동과 가속 사이의 공백을 줄여보세요', detail: 'C4 · 진입 속도는 유지하고 다음 조작을 연결하세요.', metric: '0.2', unit: 's 줄이기' },
  brake: { name: '제동 시작', title: '제동 시작 위치를 일정하게 맞추세요', detail: 'C4 · 같은 기준물에서 제동을 시작해보세요.', metric: '반복', unit: '해서 확인' },
  relift: { name: '스로틀 재리프트', title: '가속을 연결한 뒤 다시 놓는지 확인하세요', detail: 'C4 · 탈출 조향과 스로틀을 부드럽게 연결하세요.', metric: '한 가지', unit: '에 집중' },
  steering: { name: '조향', title: '탈출에서 조향을 먼저 풀어보세요', detail: 'C4 · 가속 복귀 위치와 함께 확인하세요.', metric: '연결', unit: '에 집중' },
};
export function initialConfig() { return {
  formation_items: [...formationDefaults],
  slots: Object.fromEntries(Object.entries(sessions).map(([id, s]) => [id, [...s.slots]])),
  selected: Object.fromEntries(Object.entries(sessions).map(([id, s]) => [id, s.slots.filter(Boolean)])),
  modes: Object.fromEntries(Object.keys(sessions).map(id => [id, 'coach'])),
  graph: { throttle: true, brake: true, steering: false, gear: true, future: true, feedback: true, window: 10 },
  source: 'auto', opacity: 90, contentOpacity: 100, width: 125, height: 105, x: 518, y: 387, fixed: true, autoShow: true,
  size: 'normal', auto: true, quiet: true, detail: 'standard', timing: 'pass', manual_session: 'practice',
}; }
