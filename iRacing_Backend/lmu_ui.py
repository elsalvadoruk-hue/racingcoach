"""Small standalone diagnostic screen, served only by the LMU application."""

PAGE = r'''<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LMU 텔레메트리 진단</title>
<style>
body{font:16px/1.6 system-ui,sans-serif;background:#101820;color:#e7eef5;margin:0}
main{max-width:960px;margin:auto;padding:32px 20px}h1{font-size:28px;margin-bottom:8px}
section{background:#192632;border:1px solid #344654;border-radius:12px;padding:20px;margin:20px 0}
button,select{font:inherit;padding:10px;border-radius:6px;background:#233b4c;color:#fff;border:1px solid #607d91}
select{max-width:100%;margin:8px 0}button{cursor:pointer}button:disabled{opacity:.5}
pre{white-space:pre-wrap;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}
td,th{text-align:left;padding:8px;border-bottom:1px solid #344654}small{color:#b4c7d6}
#error{color:#ffb2a8}a{color:#7dd3fc}.scroll{overflow-x:auto}
.controls{display:flex;flex-wrap:wrap;gap:16px;align-items:end}.controls label{display:block}.controls>div{min-width:0;max-width:100%}
figure{margin:24px 0}svg{display:block;width:100%;height:auto;background:#101820;border-radius:8px}
.legend{color:#9edfe2}.legend span{color:#ffca80}figcaption{margin:6px 0}#comparison-summary,#analysis-summary{white-space:pre-line}
.gain{color:#9edfe2}.loss{color:#ffb2a8}tr[aria-selected="true"]{background:#294251}
#sector-summary,#sector-detail,#corner-summary,#corner-detail,#practice-comparison-summary,#vehicle-summary,#vehicle-unit,#vehicle-comparison-summary{white-space:pre-line}.sector-button{padding:5px 8px;font-size:14px;white-space:nowrap}
#practice-history{display:flex;flex-wrap:wrap;gap:8px}#practice-task{display:block}
#replay-choices label{display:block;margin:10px 0;overflow-wrap:anywhere}#replay-slider{width:100%;margin:20px 0}#replay-summary{white-space:pre-line}
</style><main>
<h1>LMU 텔레메트리 진단</h1>
<p>LMU 전용 파일 읽기와 연동 상태를 확인합니다.</p>
<section><strong>현재 개발 단계</strong>
<p>완료된 <code>.duckdb</code> 파일 분석과 실시간 신호의 진단 수집을 제공합니다. 게임 화면과 대조하기 전까지 랩 유효성은 미확인입니다. 베스트 랩 자동 선택과 주행 개선 코칭은 후속 작업입니다.</p>
<small>이 화면은 별도 프로세스와 포트를 사용합니다. iRacing 설정·주행 기록·실시간 수집기를 공유하지 않습니다.</small></section>
<section><h2>연결 상태</h2><button id="refresh" type="button">새로고침</button>
<pre id="status" aria-live="polite">확인 중…</pre>
<p id="error" role="alert"></p></section>
<section><h2>실시간 진단 수집</h2>
<p>LMU를 실행하고 주행 세션에 들어간 뒤 수집을 시작하세요. 별도 로컬 기록에 주행·랩 판정 신호를 저장합니다. 게임의 기록 파일은 수정하지 않습니다. 이 화면을 닫아도 수집은 계속되며, 수집 중지 또는 서버 종료 시 끝납니다.</p>
<div class="controls"><button id="live-start" type="button">수집 시작</button><button id="live-stop" type="button" disabled>수집 중지</button><button id="live-refresh" type="button">수집 상태 확인</button></div>
<p id="live-message" role="status" aria-live="polite">수집 전입니다.</p><p id="live-error" role="alert"></p>
<p><strong>게임 화면 대조 전: 랩 유효성 미확인</strong>. 아래 결과는 관측한 신호의 일치 여부이며 게임의 유효 랩 판정을 확정하지 않습니다.</p>
<pre id="live-values"></pre><p id="live-recording"></p>
<details><summary>수집 지연 진단</summary><p>수신 간격은 이 컴퓨터에서 측정한 시간이고 게임 내 시간과 다릅니다. 이 값만으로 지연 원인을 확정하지 않습니다.</p><pre id="live-timing"></pre></details>
<div class="scroll"><table><caption>최근 랩 판정 신호 관측</caption><thead><tr><th>완료 랩 수</th><th>기록 시간(초)</th><th>관측 결과</th><th>유효성</th><th>관측 근거·중단 사유</th></tr></thead><tbody id="live-laps"></tbody></table></div>
</section>
<section><h2>기록 파일</h2><label for="files">검사할 파일</label><br>
<select id="files"><option value="">파일 목록을 불러오는 중…</option></select>
<button id="inspect" type="button" disabled>파일 검사</button>
<p><small>다른 Steam 라이브러리 경로는 실행 전 <code>LMU_TELEMETRY_DIRS</code>로 지정하세요. 여러 경로는 Windows에서 <code>;</code>로 구분합니다.</small></p>
<pre id="metadata"></pre><div class="scroll"><table id="laps" hidden>
<thead><tr><th>랩</th><th>경계 간 시간(초)</th><th>기록 시간(초)</th><th>판정 상태</th></tr></thead><tbody></tbody></table></div>
<details id="details" hidden><summary>채널·이벤트 상세</summary><pre id="raw"></pre></details></section>
<section id="lap-analysis" hidden><h2>단일 랩 경로·입력 분석</h2>
<div class="controls"><div><label for="analysis-lap">분석할 랩</label><select id="analysis-lap"></select></div><button id="analyze-lap" type="button" disabled>선택한 랩 분석</button></div>
<p id="analysis-summary" aria-live="polite"></p>
<div id="analysis-result" hidden><h3>기록 좌표의 주행 경로</h3>
<p>게임 기록 좌표를 상대 경로로 그린 도식입니다. 실제 지리 위치·공식 코너·트랙 경계는 확인하지 않았습니다. 경로 시작은 첫 좌표 샘플이며 결승선과 다를 수 있습니다.</p><div id="track-path"></div><p id="map-status"></p>
<h3>제동 사용 구간</h3><p>브레이크 8% 진입 → 5% 이하 해제, 0.15초 이상인 구간입니다. 입력 신호로 측정하며 브레이크 압력이나 이상적인 제동 지점을 뜻하지 않습니다.</p>
<p><small>평균 감속은 구간 시작·끝의 속도 변화 ÷ 경과 시간 ÷ 9.80665로 계산합니다. 「감속 시간 보기」는 최대 2 m 간격의 거리 지점에서 5 km/h 단위 속도에 처음 도달한 시간입니다. 샘플 사이의 정확한 통과 시각이나 최대 감속을 뜻하지 않습니다. 결측·경계 구간은 심화 측정에서 제외합니다.</small></p><div id="braking-zones" class="scroll"></div>
<h3>고스로틀 사용 구간</h3><p>스로틀 95% 진입 → 90% 이하 해제, 0.15초 이상인 구간입니다. 실제 차량의 가속 여부와는 다릅니다.</p><div id="throttle-zones" class="scroll"></div>
<details><summary>채널별 변환·지원 상태</summary><pre id="normalized-channels"></pre></details></div></section>
<section id="vehicle-analysis" hidden><h2>타이어·차량 상태 관측</h2>
<p>검사한 파일에서 랩 1개와 채널을 직접 선택하세요. 저장된 값의 관측이며 적정 온도·셋업 변경·주행 개선을 판정하지 않습니다.</p>
<div class="controls"><div><label for="vehicle-lap">관측할 랩</label><select id="vehicle-lap"></select></div><div><label for="vehicle-channel">관측 채널</label><select id="vehicle-channel"><option value="TyresPressure">타이어 압력 · TyresPressure</option></select></div><button id="load-vehicle" type="button" disabled>선택한 차량 채널 읽기</button></div>
<p><small>처음 타이어 압력을 읽으면 이 파일의 채널 목록이 표시됩니다. 원본 열 번호 value1~value4는 휠 순서 미확인입니다. 온도 채널의 left/right를 타이어 안쪽·바깥쪽으로 추정하지 않습니다.</small></p>
<p id="vehicle-summary" role="status" aria-live="polite"></p><p id="vehicle-error" role="alert"></p>
<div id="vehicle-result" hidden><p id="vehicle-unit"></p><p>통계는 원본의 유효 표본으로 계산한 최솟값·최댓값·단순 표본 평균입니다. 시간 가중 평균이 아닙니다. 그래프는 표시용 지점이며 결측·서로 다른 연속 구간 사이를 연결하지 않습니다.</p>
<div class="scroll"><table><caption>원본 성분별 표본 통계</caption><thead><tr><th>원본 성분</th><th>관측 상태·사유</th><th>전체 표본 수</th><th>유효 표본 수</th><th>무효 표본 수</th><th>최소</th><th>최대</th><th>단순 표본 평균</th></tr></thead><tbody id="vehicle-stats"></tbody></table></div><div id="vehicle-chart"></div></div></section>
<section id="comparison" hidden><h2>두 랩 수동 비교</h2>
<p>유효성 미확인 기록의 진단용 비교입니다. 피트 포함·시간 불일치·거리 샘플 부족 랩은 선택에서 제외합니다.</p>
<p>기준 파일은 위에서 검사한 기록입니다. 다른 세션 또는 같은 파일에서 비교 랩을 직접 선택하세요.</p>
<label for="target-file">비교 파일</label><br><select id="target-file"></select><pre id="comparison-file-info"></pre>
<div class="controls"><div><label for="reference">기준 랩</label><select id="reference"></select></div>
<div><label for="target">비교 랩</label><select id="target"></select></div>
<div><label for="sector-length">거리 구간 길이</label><select id="sector-length"><option value="250">250 m</option><option value="500" selected>500 m</option><option value="1000">1000 m</option></select></div>
<button id="compare" type="button" disabled>선택한 랩 비교</button></div>
<h3>선택 차량 채널의 두 랩 비교</h3><p id="vehicle-comparison-channel"></p>
<button id="compare-vehicle" type="button" disabled>위 차량 채널로 선택한 두 랩 비교</button>
<p><small>위의 타이어·차량 상태 관측에서 채널을 선택합니다. 두 랩에서 공통으로 지원되는 거리만 비교하며 통계는 거리 가중 평균입니다. 그래프는 결측·서로 다른 지원 구간 사이를 연결하지 않습니다. 휠 순서와 Left/Right 온도의 내·외측 대응은 미확인입니다. 차이는 비교 − 기준이며 셋업이나 개선 효과를 판정하지 않습니다.</small></p>
<p id="vehicle-comparison-summary" role="status" aria-live="polite"></p><p id="vehicle-comparison-error" role="alert"></p>
<div id="vehicle-comparison-result" hidden><div class="scroll"><table><caption>공통 지원 거리에서의 차량 상태 비교</caption><thead><tr><th>원본 성분</th><th>기준 거리 평균</th><th>비교 거리 평균</th><th>평균 차이</th><th>기준 최소→최대</th><th>비교 최소→최대</th><th>지원 거리 / 요청 거리(m)</th><th>지원 비율</th></tr></thead><tbody id="vehicle-comparison-stats"></tbody></table></div><div id="vehicle-comparison-charts"></div>
<h4>고정 코너 후보 경계의 차량 상태 관측</h4><p id="vehicle-corner-summary"></p><div class="scroll"><table><caption>코너 후보별 공통 지원 거리의 평균 비교</caption><thead><tr><th>코너 후보·거리(m)</th><th>원본 성분</th><th>기준 거리 평균</th><th>비교 거리 평균</th><th>평균 차이</th><th>지원 거리 / 요청 거리(m)</th><th>지원 비율</th></tr></thead><tbody id="vehicle-corner-stats"></tbody></table></div></div>
<p id="comparison-summary" aria-live="polite"></p>
<div id="comparison-result" hidden><p>시간 차이는 비교 랩 − 기준 랩입니다. 음수는 비교 랩이 더 빠른 지점입니다.</p>
<p>차량 표시명·클래스·트랙·레이아웃명이 일치하는 기록만 비교합니다. 실제 차량 모델 ID·주행 방향은 미확인이며, 연료·타이어·날씨·노면·셋업·게임 버전 조건은 대조하지 않았습니다.</p>
<h3>두 랩 경로 겹쳐 보기</h3><div id="overlay-path"></div><p id="overlay-status"></p>
<div id="corner-comparison"><h3>코너 후보별 비교</h3>
<p>기준 랩의 기록 좌표에서 찾은 기하 후보입니다. 공식 코너명·경계는 미확인이며, 기하 중심은 실제 정점이나 최저 속도 지점이 아닙니다. 같은 기준 랩에서 정한 경계를 비교 랩에도 적용합니다. 랩 유효성은 미확인입니다.</p>
<p id="corner-summary" aria-live="polite"></p>
<div id="corner-results" hidden><div class="scroll"><table><caption>코너 후보별 구간 시간 비교 · 차이는 비교 − 기준</caption><thead><tr><th>후보·거리(m)</th><th>기준(초)</th><th>비교(초)</th><th>차이(초)</th><th>상세</th></tr></thead><tbody id="corners"></tbody></table></div>
<div id="corner-focus" hidden><h4 id="corner-title"></h4><p id="corner-detail"></p>
<div class="scroll"><table><caption>선택한 코너 후보 측정 · 차이는 비교 − 기준</caption><thead><tr><th>측정 항목</th><th>단위</th><th>기준</th><th>비교</th><th>차이</th></tr></thead><tbody id="corner-metrics"></tbody></table></div>
<p><small>거리 값은 랩 거리축의 관측 위치이며 지점 간격은 최대 약 2 m입니다. 제동 시작은 구간 시작부터 기하 중심까지 브레이크 8% 초과를 처음 관측한 위치입니다. 브레이크 종료 위치는 구간 시작부터 기하 중심 이후 최대 75 m까지 마지막 5% 초과 관측입니다. 스로틀 위치는 기하 중심 이후 20% 초과, 고스로틀 위치는 95% 초과를 처음 관측한 위치입니다. 실제 재가속 시작을 보장하지 않습니다. 타력 시간은 기하 중심 이전에 속도 50 km/h 초과·두 페달 5% 미만인 지점의 시간 합계입니다. 시간 차이는 관측 결과이며 원인이나 운전 처방을 확정하지 않습니다.</small></p></div></div></div>
<h3>거리 구간별 시간 차이</h3><p>거리를 일정 간격으로 나눈 구간입니다. 게임의 공식 섹터나 코너 구분은 아닙니다. 처음과 마지막 구간은 공통 비교 범위로 잘립니다.</p>
<p id="sector-summary"></p><p><small>구간 차이 합계는 공통 비교 거리의 진입부터 이탈까지 계산합니다. 랩 경계에서 측정한 전체 기록 시간 차이와는 다를 수 있습니다.</small></p><div class="scroll"><table><caption>구간 소요 시간과 시간 차이</caption><thead><tr><th>거리</th><th>기준(초)</th><th>비교(초)</th><th>구간 차이(초)</th><th>상세</th></tr></thead><tbody id="sectors"></tbody></table></div>
<div id="sector-focus" hidden><h3 id="sector-title"></h3><p id="sector-detail"></p>
<div class="scroll"><table><caption>선택 구간 채널 비교: 기준 / 비교</caption><thead><tr><th>채널</th><th>단위</th><th>진입</th><th>최저</th><th>이탈</th><th>거리 가중 평균</th></tr></thead><tbody id="sector-metrics"></tbody></table></div>
<p><small>최저값과 평균은 원본 채널을 거리축에 선형 보간한 결과입니다. 평균은 거리 가중 값이며, 시간 평균이나 페달 사용 시간 비율이 아닙니다.</small></p></div>
<p class="legend">기준 랩: 청록색 <span> / 비교 랩: 주황색</span></p><div id="charts"></div>
<div class="scroll"><table><caption>공통 거리 지점에서의 채널 차이</caption><thead><tr><th>채널</th><th>원본 주파수(기준 / 비교)</th><th>단위</th><th>평균 절대 차이</th></tr></thead><tbody id="metrics"></tbody></table></div>
<details><summary>두 기록의 채널 지원·입력 처리 상태</summary><pre id="comparison-support"></pre></details>
<p><small>각 채널의 원본 주파수로 시간축을 맞춘 뒤 공통 거리 범위에 선형 보간합니다. 페달·조향은 Unfiltered 기록을 우선 사용하고 없으면 게임 처리 입력을 사용합니다. 서로 다른 처리 단계는 비교에서 제외합니다. 그래프 끝은 실제 결승선과 다를 수 있습니다. 조향은 %로 표시하며 각도로 변환하지 않습니다. 이 비교만으로 컷 유효성이나 개선 원인을 판정하지 않습니다.</small></p></div></section>
<section><h2>여러 랩 오프라인 고스트 재생</h2>
<p>위에서 검사한 기준 파일과 비교 파일의 랩이 아래 목록에 추가됩니다. 2~6개를 직접 선택하세요. 첫 번째 선택 항목의 좌표를 공유 기준으로 사용합니다.</p>
<div id="replay-choices"></div><button id="load-replay" type="button" disabled>선택한 랩 재생 준비</button>
<button id="load-consistency" type="button" disabled>구간별 일관성 분석</button>
<p><small>일관성 분석은 3~6개 랩을 직접 선택하세요. 첫 선택 랩의 기하 후보 경계를 고정합니다. 랩 유효성과 연료·타이어·날씨 등 조건의 동등성은 미확인입니다.</small></p>
<p id="consistency-summary" aria-live="polite"></p><div id="consistency-result" hidden>
<h3>제동·탈출 입력 위치의 반복성</h3>
<p><small>최대 약 2 m 격자에서 관측한 위치이며 실제 페달 전환 시각을 뜻하지 않습니다. 기하 중심에서 이미 스로틀이 켜져 있었다면 중심 위치가 첫 관측으로 기록될 수 있습니다. 위치 범위가 0 m여도 재가속이 일정했다는 뜻은 아닙니다. 스로틀 관측 위치는 재가속 시작을 보장하지 않습니다.</small></p>
<div class="scroll"><table><caption>코너 후보별 제동·탈출 입력 위치 분포</caption><thead><tr><th>후보·거리(m)</th><th>입력 관측 항목</th><th>관측 랩 수</th><th>중앙 위치(m)</th><th>최소 위치(m)</th><th>최대 위치(m)</th><th>위치 범위(m)</th></tr></thead><tbody id="consistency-inputs"></tbody></table></div>
<p id="consistency-input-reason"></p><div id="consistency-focus"></div>
<details id="consistency-times"><summary>구간 시간의 분포도 함께 보기</summary><div class="scroll"><table><caption>코너 후보별 관측 시간 분포</caption><thead><tr><th>후보·거리(m)</th><th>관측 랩 수</th><th>중앙값(초)</th><th>최단(초)</th><th>최장(초)</th><th>범위(초)</th><th>표준편차(초)</th></tr></thead><tbody id="consistency-corners"></tbody></table></div></details>
<p><small>소수 랩의 관측 분포이며 운전자 실력이나 개선 원인을 확정하지 않습니다. 구간별 최단 시간의 합을 가상의 유효 랩으로 취급하지 않습니다.</small></p></div>
<p id="replay-summary" aria-live="polite"></p>
<div id="replay-result" hidden><p>각 랩의 게임 Lap 이벤트를 0초로 맞춘 기록 재생입니다. 실제 동시 주행·실시간 데이터가 아니며, 랩 유효성과 세션 간 좌표계 일치는 미확인입니다. 경계·결측·좌표 급변에서는 마커를 숨기며 랩 경계 종료 뒤에는 종료 상태로 표시합니다.</p>
<div id="replay-map"></div><label for="replay-slider">랩 경과 시간</label><input id="replay-slider" type="range" min="0" max="500" value="0" step="1">
<div class="controls"><button id="replay-play" type="button">재생</button><button id="replay-reset" type="button">처음으로</button><label>재생 속도 <select id="replay-speed"><option value="0.5">0.5배</option><option value="1" selected>1배</option><option value="2">2배</option></select></label><output id="replay-time" for="replay-slider"></output></div>
<div class="scroll"><table><caption>선택 랩의 현재 기록 값</caption><thead><tr><th>선택</th><th>상태</th><th>거리(m)</th><th>속도(km/h)</th><th>스로틀(%)</th><th>브레이크(%)</th></tr></thead><tbody id="replay-values"></tbody></table></div><details><summary>재생 랩별 채널·입력 처리 상태</summary><pre id="replay-support"></pre></details></div></section>
<section><h2>제동·탈출 입력 연습 과제</h2>
<p>현재 일관성 분석을 기준 기록으로 저장한 뒤, 다른 3~6개 랩을 위 목록에서 선택해 같은 경계로 다시 비교합니다. 기준과 겹치는 랩은 재비교할 수 없습니다. 저장과 재비교는 버튼을 눌렀을 때만 실행합니다.</p>
<button id="save-practice" type="button" disabled>현재 일관성 분석을 연습 과제로 저장</button>
<button id="load-practice-tasks" type="button">저장된 과제 목록 읽기</button>
<label for="practice-task">저장된 연습 과제</label><select id="practice-task"><option value="">목록을 읽거나 과제를 저장하세요</option></select>
<button id="compare-practice" type="button" disabled>선택한 3~6개 랩으로 과제 재비교</button>
<p id="practice-selection-info"></p><p id="practice-message" role="status" aria-live="polite"></p><p id="practice-error" role="alert"></p>
<div id="practice-detail" hidden><h3>저장한 기준 기록</h3><pre id="practice-baseline"></pre><div id="practice-baseline-inputs" class="scroll"></div><h4>저장된 재비교 이력</h4><p id="practice-history-summary"></p><div id="practice-history"></div></div>
<div id="practice-comparison" hidden><h3>새 기록과의 입력 반복성 관측</h3><p id="practice-comparison-summary"></p><div id="practice-comparison-values"></div></div>
<p><small>최대 약 2 m 격자의 근사 위치를 비교합니다. 기하 중심에서 이미 스로틀이 활성이라면 위치 범위가 0 m여도 재가속 일관성을 뜻하지 않습니다. 랩 유효성·조건 동등성은 미확인이며, 범위 감소나 시간 단축을 연습 효과·개선 성공으로 확정하지 않습니다.</small></p></section>
<p><a href="/docs">LMU 전용 API 문서</a></p></main>
<script>
const $=id=>document.getElementById(id);
let busy=false,inspectedFileId=null,dependencyReady=false,availableFiles=[],inspectedSummary=null,targetSummary=null;
let liveBusy=false,liveEpoch=0,liveTimer=0,liveData=null;
let currentConsistency=null,practiceTask=null,practiceTasks=[],practiceComparisons=[],practiceSelectionEpoch=0;
let vehicleEpoch=0,vehicleComparisonEpoch=0;
async function api(path,options={}){const response=await fetch(path,{cache:'no-store',...options});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'입력 내용을 확인하세요.');return data;}
const lapTime=value=>{const ms=Math.round(value*1000);return `${Math.floor(ms/60000)}:${((ms%60000)/1000).toFixed(3).padStart(6,'0')}`;};
function livePending(){return liveData?.collecting===true||liveData?.state==='STOPPING';}
function liveControls(){const collecting=liveData?.collecting===true;$('live-start').disabled=liveBusy||livePending();$('live-stop').disabled=liveBusy||!collecting;$('live-refresh').disabled=liveBusy;}
function scheduleLive(){clearTimeout(liveTimer);if(livePending()&&!document.hidden)liveTimer=setTimeout(()=>loadLive(),1000);}
const liveReasonLabels={PARTIAL_LAP_AT_ATTACH:'랩 중간부터 관측 시작',PIT_OBSERVED:'피트 신호 관측',NOT_REALTIME_OR_UNKNOWN:'주행 상태 종료 또는 미확인',LAST_LAP_TIME_MISSING:'완주 후 기록 시간이 제공되지 않음',LAST_LAP_TIME_CONFLICT:'기록 시간과 랩 경계 불일치',STALE_TELEMETRY:'새 주행 신호 수신 정체',OBSERVATION_GAP:'관측 간격 누락',PAUSED_OR_UNKNOWN:'일시정지 또는 상태 미확인',SESSION_OR_VEHICLE_CHANGED:'세션 또는 차량 변경',SDK_LIFECYCLE_EVENT:'게임 세션·주행 상태 변경',STOP_REQUESTED:'사용자가 수집 중지 요청',STOPPED:'수집 중지',GAME_EXITED:'게임 종료',SCORING_CONFIRMATION_TIMEOUT:'랩 종료 판정 신호 대기 시간 초과',SCORING_CONFIRMATION_MISSING:'랩 종료 판정 신호 미관측',SCORING_CONFIRMATION_DELAYED:'랩 종료 판정 신호 지연',SCORING_MISSING:'판정 신호 미관측',SCORING_STALE:'판정 신호 갱신 지연',COUNT_LAP_FLAG_MISSING:'시간 집계 정책 미확인',PIT_SIGNAL_MISSING:'피트 신호 미확인',LAP_INVALIDATION_SIGNAL_MISSING:'랩 무효 신호 미확인',TRACK_LIMITS_SIGNAL_MISSING:'트랙 리밋 신호 미확인'};
function liveLapEvidence(lap){
  const details=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('div');
  summary.textContent=lap.event_type==='LAP_OBSERVATION_INTERRUPTED'?'관측 중단 사유':'관측 근거 보기';details.append(summary,body);
  const add=text=>{const p=document.createElement('p');p.textContent=text;body.append(p);},e=lap.evidence||{};
  for(const reason of lap.reasons||[])add(liveReasonLabels[reason]||reason);
  if(Number.isFinite(lap.lap_start_et)&&Number.isFinite(lap.lap_end_et)&&lap.lap_end_et>=lap.lap_start_et)add(`경계 간 시간: ${(lap.lap_end_et-lap.lap_start_et).toFixed(3)}초 (공식 기록 아님)`);
  const flags={0:'0(랩·시간 집계 안 함)',1:'1(랩만 집계)',2:'2(랩·시간 집계)'};
  add('시간 집계 정책: '+(e.count_lap_flags?.length?e.count_lap_flags.map(f=>flags[f]??f).join(', '):'미확인'));
  add(`트랙 리밋 누적 신호: ${e.track_limits_steps_min??'미확인'} → ${e.track_limits_steps_max??'미확인'} (경고 횟수 아님)`);
  add('랩 무효 신호 감지: '+(e.lap_invalidated_observed===true?'감지':e.lap_invalidated_observed===false?'관측 안 됨':'미확인'));
  add('종료 판정 신호: '+(e.scoring_confirmation===true?'관측':e.scoring_confirmation===false?'미확인':'정보 없음'));
  return details;
}
function renderLive(data){
  liveData=data||{};data=liveData;const labels={WAITING:'새 데이터 대기',OBSERVING:'수집 중',STALE:'데이터 갱신 대기',GAME_EXITED:'게임 종료',STOPPED:'수집 중지',STOPPING:'종료 대기',ERROR:'수집 오류',UNAVAILABLE:'연결 확인 필요',LIMIT_REACHED:'기록 제한 도달'};
  $('live-message').textContent=`${labels[data.state]||data.state} · ${data.reason||''}`;
  const s=data.snapshot,number=(v,scale=1)=>typeof v==='number'&&Number.isFinite(v)?(v*scale).toFixed(1):'미확인';
  $('live-values').textContent=s?`속도: ${number(s.speed_mps,3.6)} km/h\n스로틀: ${number(s.throttle_ratio,100)} % / 브레이크: ${number(s.brake_ratio,100)} %\n현재 랩 무효 신호: ${s.lap_invalidated===true?'감지':s.lap_invalidated===false?'미감지':'미확인'}\n피트 신호: ${s.in_pits===true?'피트':s.in_pits===false?'피트 아님':'미확인'}`:'표시할 최신 주행 데이터가 없습니다.';
  const journals={ACTIVE:'기록 중',COMPLETE:'기록 종료',INCOMPLETE:'일부 기록 저장 실패',NOT_STARTED:'기록 전'};
  $('live-recording').textContent=data.recording_id?`로컬 기록 ID: ${data.recording_id} · 저장 샘플 ${data.frames_recorded??0}개 · ${journals[data.journal_state]||'기록 상태 미확인'}`:'이번 연결의 로컬 기록은 없습니다.';
  const t=data.timing,seconds=v=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(3)+'초':'미확인';
  const timingState=data.collecting?'현재 수집 상태입니다.':data.state==='STOPPING'?'종료 처리 중인 수집 상태입니다.':data.journal_state==='NOT_STARTED'?'수집 전 상태입니다.':'수집 종료 시점의 값입니다.';
  $('live-timing').textContent=t?`${timingState}\n수집 경과: ${seconds(t.recording_elapsed_s)}\n마지막 주행 신호 이후: ${seconds(t.telemetry_age_s)}\n직전 주행 신호 수신 간격: ${seconds(t.last_telemetry_receive_gap_s)}\n가장 긴 주행 신호 수신 간격: ${seconds(t.max_telemetry_receive_gap_s)}\n직전 데이터 읽기 대기: ${seconds(t.last_read_duration_s)}\n현재 데이터 읽기 대기: ${seconds(t.read_in_progress_s)}\n직전 게임 시간 간격: ${seconds(t.last_game_time_gap_s)}\n수신 정체 감지: ${t.stale_count??0}회`:'이 수집 버전에는 지연 진단 정보가 없습니다.';
  if(t&&data.last_stale)$('live-timing').textContent+=`\n마지막 정체 감지 시점: 수집 시작 후 ${seconds(data.last_stale.recording_elapsed_s)}\n정체 감지 당시 주행 신호 경과: ${seconds(data.last_stale.telemetry_age_s)}`;
  $('live-laps').replaceChildren();const verdicts={CONSISTENT_VALID_SIGNALS:'정상 신호 일치(검증 전)',INVALID_SIGNAL_OBSERVED:'랩 무효 신호 관측(판정 미확인)',COUNT_POLICY_SIGNAL_OBSERVED:'시간 집계 제한 신호(판정 미확인)',UNKNOWN:'관측 불완전'};
  for(const lap of (data.lap_observations?.completed||[]).slice(-10).reverse()){const row=document.createElement('tr'),time=Number.isFinite(lap.last_lap_time)&&lap.last_lap_time>0?lap.last_lap_time.toFixed(3):lap.reasons?.includes('LAST_LAP_TIME_MISSING')?'기록 시간 없음':'—';for(const text of [lap.total_laps_at_end??'미확인',time,verdicts[lap.diagnostic_verdict]||'관측 불완전','미확인']){const td=document.createElement('td');td.textContent=text;row.append(td);}const evidence=document.createElement('td');evidence.append(liveLapEvidence(lap));row.append(evidence);$('live-laps').append(row);}
  liveControls();scheduleLive();
}
async function loadLive(action=null){
  if(liveBusy)return;const epoch=++liveEpoch;clearTimeout(liveTimer);liveBusy=true;liveControls();$('live-error').textContent='';
  try{const data=await api('/api/lmu/live/'+(action||'status'),action?{method:'POST'}:{});if(epoch===liveEpoch)renderLive(data);}
  catch(error){if(epoch===liveEpoch){$('live-error').textContent=error.message;$('live-message').textContent='수집 상태를 확인하지 못했습니다. 서버 연결을 확인하세요.';$('live-values').textContent='최신 상태를 확인하지 못해 주행 값을 숨겼습니다.';$('live-timing').textContent='최신 상태를 확인하지 못해 지연 진단 값을 숨겼습니다.';}}
  finally{if(epoch===liveEpoch){liveBusy=false;liveControls();scheduleLive();}}
}
$('live-start').addEventListener('click',()=>loadLive('start'));$('live-stop').addEventListener('click',()=>loadLive('stop'));$('live-refresh').addEventListener('click',()=>loadLive());
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(liveTimer);else if(livePending())loadLive();});window.addEventListener('pagehide',()=>clearTimeout(liveTimer));
function updateCompare(){$('compare').disabled=busy||!inspectedFileId||!targetSummary||!$('reference').value||!$('target').value||(inspectedFileId===targetSummary.file_id&&$('reference').value===$('target').value);updateVehicleComparison();}
function updateAnalysis(){$('analyze-lap').disabled=busy||!inspectedFileId||!$('analysis-lap').value;}
function updateVehicle(){$('load-vehicle').disabled=busy||!inspectedFileId||!$('vehicle-lap').value||!$('vehicle-channel').value;updateVehicleComparison();}
function setBusy(value){busy=value;for(const id of ['refresh','files','reference','target-file','target','sector-length','analysis-lap','vehicle-lap','vehicle-channel'])$(id).disabled=value;for(const input of $('replay-choices').querySelectorAll('input'))input.disabled=value;$('inspect').disabled=value||!dependencyReady||!$('files').value;updateCompare();updateAnalysis();updateReplay();updateVehicle();}
function clearComparison(){$('comparison-summary').textContent='';$('comparison-result').hidden=true;for(const id of ['charts','metrics','sectors','overlay-path'])$(id).replaceChildren();$('overlay-status').textContent='';$('comparison-support').textContent='';$('sector-summary').textContent='';$('sector-focus').hidden=true;$('sector-metrics').replaceChildren();clearCorners();clearVehicleComparison();}
function clearAnalysis(){$('analysis-summary').textContent='';$('analysis-result').hidden=true;for(const id of ['track-path','braking-zones','throttle-zones'])$(id).replaceChildren();$('map-status').textContent='';$('normalized-channels').textContent='';}
function clearFile(){inspectedFileId=null;inspectedSummary=null;targetSummary=null;$('target-file').replaceChildren();$('comparison-file-info').textContent='';$('metadata').textContent='';$('laps').hidden=true;$('details').hidden=true;$('comparison').hidden=true;$('lap-analysis').hidden=true;$('vehicle-analysis').hidden=true;$('vehicle-lap').replaceChildren();$('vehicle-channel').replaceChildren(new Option('타이어 압력 · TyresPressure','TyresPressure'));clearVehicle();clearComparison();clearAnalysis();updateCompare();updateAnalysis();updateVehicle();}
async function refresh(){
  const refreshLiveEpoch=liveEpoch;
  dependencyReady=false;setBusy(true);$('error').textContent='';clearFile();$('files').replaceChildren();clearReplay();clearConsistency();replayChoices.clear();$('replay-choices').replaceChildren();
  try{const [status,listing]=await Promise.all([api('/api/lmu/status'),api('/api/lmu/telemetry/files')]);
    $('status').textContent=`파일 읽기 패키지: ${status.dependency}\n실시간 수집: ${status.live.state}\n발견한 파일: ${status.file_count}\n`+status.telemetry_dirs.map(dir=>`${dir.exists?'폴더 확인':'폴더 없음'}: ${dir.path}`).join('\n');
    if(status.error)$('error').textContent=status.error;
    if(!liveBusy&&refreshLiveEpoch===liveEpoch)renderLive(status.live);
    dependencyReady=status.dependency==='READY';
    $('files').replaceChildren();if(!listing.files.length){const opt=new Option('발견한 LMU 파일이 없습니다.','');$('files').add(opt);}
    availableFiles=listing.files;for(const file of listing.files)$('files').add(new Option(file.file_name+' ('+file.file_id.slice(0,6)+')',file.file_id));
  }catch(error){$('error').textContent=error.message;$('status').textContent='상태를 확인하지 못했습니다.';}
  finally{setBusy(false);}
}
$('refresh').addEventListener('click',refresh);$('files').addEventListener('change',clearFile);
$('inspect').addEventListener('click',async()=>{
  setBusy(true);$('error').textContent='';clearFile();
  try{const {file}=await api('/api/lmu/telemetry/file?file_id='+encodeURIComponent($('files').value));
    $('metadata').textContent=JSON.stringify(file.metadata,null,2)+'\n\n게임의 랩 유효성은 확인되지 않았습니다. 베스트 랩 자동 선택은 비활성 상태입니다.';
    const body=$('laps').querySelector('tbody');body.replaceChildren();
    const labels={VALIDITY_UNKNOWN:'유효성 미확인',UNTIMED:'기록 시간 없음',TIMING_MISMATCH:'시간 불일치',PIT_LAP:'피트 포함',INCOMPLETE:'샘플 부족',DISTANCE_DISCONTINUITY:'거리 불연속'};
    for(const lap of file.laps){const row=document.createElement('tr');for(const value of [lap.lap_number,lap.lap_time_s.toFixed(3),lap.recorded_lap_time_s?.toFixed(3)??'—',labels[lap.status]||lap.status]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}body.append(row);}
    $('laps').hidden=!file.laps.length;if(!file.laps.length)$('metadata').textContent+='\n양쪽 경계가 기록된 랩이 없습니다.';
    $('raw').textContent=JSON.stringify({channels:file.channels,events:file.events,sample_counts:file.sample_counts},null,2);$('details').hidden=false;
    for(const id of ['reference','target','analysis-lap','vehicle-lap'])fillLaps(id,file);
    inspectedFileId=file.file_id;inspectedSummary=file;targetSummary=file;addReplayChoices(file);
    for(const item of availableFiles)$('target-file').add(new Option(item.file_name+' ('+item.file_id.slice(0,6)+')',item.file_id));
    $('target-file').value=file.file_id;comparisonFileInfo();$('comparison').hidden=false;$('lap-analysis').hidden=false;$('vehicle-analysis').hidden=false;
  }catch(error){$('error').textContent=error.message;}
  finally{setBusy(false);}
});
function fillLaps(id,file){$(id).replaceChildren(new Option('랩을 선택하세요',''));for(const lap of file.laps.filter(lap=>lap.status==='VALIDITY_UNKNOWN'&&lap.distance_m!==null))$(id).add(new Option(`랩 ${lap.lap_number} · ${lapTime(lap.recorded_lap_time_s)} · 유효성 미확인`,lap.lap_number));}
function comparisonFileInfo(){const m=targetSummary.metadata;$('comparison-file-info').textContent=`기준: ${inspectedSummary.file_name}\n비교: ${targetSummary.file_name}\n비교 차량: ${m.car_name||'미확인'} / ${m.car_class||'미확인'}\n비교 트랙: ${m.track_name||'미확인'} / ${m.track_layout||'미확인'}`;}
$('target-file').addEventListener('change',async()=>{
  clearComparison();targetSummary=null;$('target').replaceChildren();$('comparison-file-info').textContent='비교 파일을 검사하는 중…';$('error').textContent='';setBusy(true);
  try{const id=$('target-file').value;targetSummary=id===inspectedFileId?inspectedSummary:(await api('/api/lmu/telemetry/file?file_id='+encodeURIComponent(id))).file;fillLaps('target',targetSummary);comparisonFileInfo();addReplayChoices(targetSummary);if($('target').options.length===1)$('comparison-file-info').textContent+='\n이 파일에는 비교 가능한 기록 랩이 없습니다.';}
  catch(error){$('error').textContent=error.message;$('comparison-file-info').textContent='비교 파일을 검사하지 못했습니다.';}
  finally{setBusy(false);}
});
for(const id of ['reference','target','sector-length'])$(id).addEventListener('change',()=>{clearComparison();updateCompare();});
$('analysis-lap').addEventListener('change',()=>{clearAnalysis();updateAnalysis();});
function clearVehicle(){vehicleEpoch++;$('vehicle-summary').textContent='';$('vehicle-error').textContent='';$('vehicle-unit').textContent='';$('vehicle-result').hidden=true;$('vehicle-stats').replaceChildren();$('vehicle-chart').replaceChildren();}
for(const id of ['vehicle-lap','vehicle-channel'])$(id).addEventListener('change',()=>{clearVehicle();updateVehicle();});
const vehicleNumber=value=>Number.isFinite(value)?value.toFixed(3):'측정 불가';
const vehicleStatus=value=>({AVAILABLE:'관측 가능',PARTIAL:'일부 결측·지원 제한',UNAVAILABLE:'관측 불가'}[value]||value||'미확인');
function vehicleCatalog(catalog){
  if(!catalog?.length)return;const selected=$('vehicle-channel').value,previous=$('vehicle-channel').selectedOptions[0]?.textContent||selected;
  $('vehicle-channel').replaceChildren();for(const channel of catalog)$('vehicle-channel').add(new Option(`${channel.group?channel.group+' / ':''}${channel.label||channel.name} · ${channel.name}${channel.status==='AVAILABLE'?'':' · '+vehicleStatus(channel.status)}`,channel.name));
  if(![...$('vehicle-channel').options].some(option=>option.value===selected))$('vehicle-channel').add(new Option(previous,selected));$('vehicle-channel').value=selected;
}
function renderVehicle(vehicle){
  $('vehicle-stats').replaceChildren();$('vehicle-chart').replaceChildren();$('vehicle-result').hidden=true;vehicleCatalog(vehicle?.catalog);
  const c=vehicle?.channel,unit=value=>value===null||value===undefined?'미확인':value===''?'표기 없음':value;
  $('vehicle-summary').textContent=`랩 ${vehicle?.lap_number??'미확인'} · ${Number.isFinite(vehicle?.recorded_lap_time_s)?lapTime(vehicle.recorded_lap_time_s):'기록 시간 미확인'} · 게임 랩 유효성 미확인\n${vehicleStatus(vehicle?.status)}${vehicle?.reason?' · '+vehicle.reason:''}`;
  if(!c){$('vehicle-unit').textContent='';return;}
  $('vehicle-unit').textContent=`${c.label||c.name} · 원본 채널: ${c.name}\n원본 단위: ${unit(c.source_unit)} / 표시 단위: ${unit(c.unit)} · ${Number.isFinite(c.frequency_hz)?c.frequency_hz+' Hz':'주파수 미확인'}\n${c.wheel_order==='UNVERIFIED'?'원본 열 번호 그대로 표시합니다. 휠 순서는 미확인입니다.':c.wheel_order==='NOT_APPLICABLE'?'단일 값 채널이며 휠 위치를 뜻하지 않습니다.':'휠 위치 대응은 미확인입니다.'}`;
  for(const component of c.components||[]){const row=document.createElement('tr');row.dataset.component=component.id;
    for(const text of [component.label||component.id,vehicleStatus(component.status)+(component.reason?' · '+component.reason:''),component.sample_count??'미확인',component.valid_samples??'미확인',component.invalid_samples??'미확인',vehicleNumber(component.minimum),vehicleNumber(component.maximum),vehicleNumber(component.mean)]){const cell=document.createElement('td');cell.textContent=text;row.append(cell);}$('vehicle-stats').append(row);
  }
  vehiclePlot(c);$('vehicle-result').hidden=false;
}
function vehiclePlot(channel){
  const components=channel.components||[],valid=components.flatMap(component=>component.points||[]).filter(point=>Number.isFinite(point.elapsed_s)&&Number.isFinite(point.value));
  if(!valid.length){const note=document.createElement('p');note.textContent='표시할 유효 표본이 없습니다. 결측값을 0으로 채우지 않습니다.';$('vehicle-chart').append(note);return;}
  const colors=['#9edfe2','#ffca80','#e7a6ff','#b8e986'],times=valid.map(p=>p.elapsed_s),values=valid.map(p=>p.value),start=Math.min(...times),end=Math.max(...times);
  let low=Math.min(...values),high=Math.max(...values);if(low===high){low-=1;high+=1;}
  const x=t=>66+(t-start)/Math.max(.001,end-start)*754,y=value=>195-(value-low)/(high-low)*173,ns='http://www.w3.org/2000/svg';
  const figure=document.createElement('figure'),caption=document.createElement('figcaption'),svg=document.createElementNS(ns,'svg');caption.textContent=`${channel.label||channel.name} · 랩 경과 시간(초) · ${channel.unit??'단위 미확인'}`;figure.append(caption);svg.setAttribute('viewBox','0 0 840 240');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${channel.label||channel.name}, 랩 경과 시간별 관측값, 단위 ${channel.unit??'미확인'}`);
  const add=(tag,attrs,text)=>{const el=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;svg.append(el);return el;};
  for(let i=0;i<=4;i++){const value=low+(high-low)*i/4;add('line',{x1:66,x2:820,y1:y(value),y2:y(value),stroke:'#344654'});add('text',{x:59,y:y(value)+4,fill:'#b4c7d6','font-size':11,'text-anchor':'end'},value.toFixed(1));const t=start+(end-start)*i/4;add('text',{x:x(t),y:218,fill:'#b4c7d6','font-size':11,'text-anchor':i===0?'start':i===4?'end':'middle'},t.toFixed(2)+'초');}
  const legend=document.createElement('p');
  for(const [index,component] of components.entries()){
    const color=colors[index%colors.length],label=document.createElement('span');label.style.color=color;label.textContent=(index?' / ':'')+(component.label||component.id);legend.append(label);
    let run=[],previous=null;const flush=()=>{if(run.length>1)add('polyline',{'data-component':component.id,points:run.map(p=>`${x(p.elapsed_s)},${y(p.value)}`).join(' '),fill:'none',stroke:color,'stroke-width':2});else if(run.length===1)add('circle',{'data-component':component.id,cx:x(run[0].elapsed_s),cy:y(run[0].value),r:3,fill:color});run=[];};
    for(const point of component.points||[]){
      if(!Number.isFinite(point.elapsed_s)||!Number.isFinite(point.value)){flush();previous=null;continue;}
      if(previous&&(point.segment===null||point.segment===undefined||point.segment!==previous.segment||point.elapsed_s<=previous.elapsed_s))flush();
      run.push(point);previous=point;
    }flush();
  }figure.append(svg,legend);$('vehicle-chart').append(figure);
}
$('load-vehicle').addEventListener('click',async()=>{
  clearVehicle();const epoch=vehicleEpoch,fileId=inspectedFileId,lap=$('vehicle-lap').value,name=$('vehicle-channel').value;setBusy(true);$('vehicle-summary').textContent='저장된 차량 채널을 읽는 중…';
  try{const params=new URLSearchParams({file_id:fileId,lap_number:lap,channel_name:name,points:301});const {vehicle}=await api('/api/lmu/telemetry/vehicle-channels?'+params);if(epoch===vehicleEpoch&&fileId===inspectedFileId&&lap===$('vehicle-lap').value&&name===$('vehicle-channel').value)renderVehicle(vehicle);}
  catch(error){if(epoch===vehicleEpoch){$('vehicle-error').textContent=error.message;$('vehicle-summary').textContent='차량 채널을 읽지 못했습니다. 이전 관측 결과는 숨겼습니다.';}}
  finally{setBusy(false);}
});
function updateVehicleComparison(){
  $('compare-vehicle').disabled=busy||!inspectedFileId||!targetSummary||!$('reference').value||!$('target').value||!$('vehicle-channel').value||(inspectedFileId===targetSummary.file_id&&$('reference').value===$('target').value);
  $('vehicle-comparison-channel').textContent='선택 채널: '+($('vehicle-channel').selectedOptions[0]?.textContent||'미선택');
}
function clearVehicleComparison(){vehicleComparisonEpoch++;for(const id of ['vehicle-comparison-summary','vehicle-comparison-error','vehicle-corner-summary'])$(id).textContent='';for(const id of ['vehicle-comparison-stats','vehicle-comparison-charts','vehicle-corner-stats'])$(id).replaceChildren();$('vehicle-comparison-result').hidden=true;}
$('vehicle-channel').addEventListener('change',()=>{clearVehicleComparison();updateVehicleComparison();});
const vehicleSigned=value=>Number.isFinite(value)?`${value>=0?'+':''}${value.toFixed(3)}`:'측정 불가';
function vehicleCoverage(component){
  const supported=component.supported_distance_m,requested=component.requested_distance_m;
  return [`${Number.isFinite(supported)?supported.toFixed(1):'측정 불가'} / ${Number.isFinite(requested)?requested.toFixed(1):'측정 불가'}`,Number.isFinite(supported)&&Number.isFinite(requested)&&requested>0?(supported/requested*100).toFixed(1)+' %':'측정 불가'];
}
function vehicleMeanValues(component){return [vehicleNumber(component.reference?.mean),vehicleNumber(component.target?.mean),vehicleSigned(component.delta?.mean)];}
function renderVehicleComparison(report){
  clearVehicleComparison();const c=report?.channel,source=s=>`랩 ${s?.lap_number??'미확인'} · ${Number.isFinite(s?.recorded_lap_time_s)?lapTime(s.recorded_lap_time_s):'기록 시간 미확인'} · 원본 단위 ${s?.source_unit??'미확인'} · ${Number.isFinite(s?.frequency_hz)?s.frequency_hz+' Hz':'주파수 미확인'}`;
  $('vehicle-comparison-summary').textContent=`${vehicleStatus(report?.status)} · ${c?.label||c?.name||'채널 미확인'} · 표시 단위 ${c?.unit??'미확인'}\n기준: ${source(report?.reference)}\n비교: ${source(report?.target)}\n게임 랩 유효성·주행 조건의 동등성은 미확인입니다. ${c?.wheel_order==='UNVERIFIED'?'원본 성분의 휠 순서는 미확인입니다.':''}\n${report?.reason||report?.note||'양쪽 공통 지원 거리에서 관측한 차이이며 원인이나 셋업 처방을 확정하지 않습니다.'}`;
  if(!c)return;
  for(const component of c.components||[]){
    const range=side=>`${vehicleNumber(component[side]?.minimum)} → ${vehicleNumber(component[side]?.maximum)}`;
    const label=(component.label||component.id)+(component.status==='AVAILABLE'?'':' · '+vehicleStatus(component.status))+(component.reason?' · '+component.reason:'');
    practiceRow($('vehicle-comparison-stats'),[label,...vehicleMeanValues(component),range('reference'),range('target'),...vehicleCoverage(component)]);
    vehicleComparisonPlot(c,component);
  }
  const corners=report.corner_comparison;
  if(!['AVAILABLE','PARTIAL'].includes(corners?.status))$('vehicle-corner-summary').textContent='코너 후보 비교 불가: '+(corners?.reason||'고정 경계의 차량 상태 관측이 없습니다.');
  else{
    $('vehicle-corner-summary').textContent=`기준 랩 ${corners.definition?.reference_lap??'미확인'}의 고정 기하 경계 · 공식 코너·실제 정점 미확인. 각 성분의 공통 지원 거리만 평균에 사용합니다.`;
    for(const corner of corners.corners||[])for(const component of corner.components||[])practiceRow($('vehicle-corner-stats'),[`${corner.label} · ${Number.isFinite(corner.start_m)?corner.start_m.toFixed(1):'미확인'}–${Number.isFinite(corner.end_m)?corner.end_m.toFixed(1):'미확인'}`,(component.label||component.id)+(component.status==='AVAILABLE'?'':' · '+vehicleStatus(component.status))+(component.reason?' · '+component.reason:''),...vehicleMeanValues(component),...vehicleCoverage(component)]);
  }
  $('vehicle-comparison-result').hidden=false;
}
function vehicleComparisonPlot(channel,component){
  const points=component.points||[],valid=points.filter(point=>Number.isFinite(point.distance_m)&&Number.isFinite(point.reference)&&Number.isFinite(point.target));
  if(!valid.length){const note=document.createElement('p');note.textContent=`${component.label||component.id}: 그래프를 표시할 공통 지원 거리 지점이 없습니다.`;$('vehicle-comparison-charts').append(note);return;}
  const distances=valid.map(point=>point.distance_m),values=valid.flatMap(point=>[point.reference,point.target]),start=Math.min(...distances),end=Math.max(...distances);let low=Math.min(...values),high=Math.max(...values);if(low===high){low-=1;high+=1;}
  const x=d=>66+(d-start)/Math.max(.001,end-start)*754,y=value=>195-(value-low)/(high-low)*173,ns='http://www.w3.org/2000/svg',figure=document.createElement('figure'),caption=document.createElement('figcaption'),svg=document.createElementNS(ns,'svg');
  caption.textContent=`${component.label||component.id} · ${channel.unit??'단위 미확인'} · 기준: 청록색 / 비교: 주황색 점선`;figure.append(caption);svg.setAttribute('viewBox','0 0 840 240');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${component.label||component.id}, 동일 거리의 기준·비교 관측, 단위 ${channel.unit??'미확인'}`);
  const add=(tag,attrs,text)=>{const el=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;svg.append(el);return el;};
  for(let i=0;i<=4;i++){const value=low+(high-low)*i/4;add('line',{x1:66,x2:820,y1:y(value),y2:y(value),stroke:'#344654'});add('text',{x:59,y:y(value)+4,fill:'#b4c7d6','font-size':11,'text-anchor':'end'},value.toFixed(1));const d=start+(end-start)*i/4;add('text',{x:x(d),y:218,fill:'#b4c7d6','font-size':11,'text-anchor':i===0?'start':i===4?'end':'middle'},d.toFixed(1)+' m');}
  for(const [role,color] of [['reference','#9edfe2'],['target','#ffca80']]){
    let run=[],previous=null;const flush=()=>{const attrs={'data-component':component.id,'data-role':role,stroke:color,'stroke-width':2};if(run.length>1)add('polyline',{...attrs,points:run.map(point=>`${x(point.distance_m)},${y(point[role])}`).join(' '),fill:'none','stroke-dasharray':role==='target'?'6 3':'none'});else if(run.length===1)add('circle',{...attrs,cx:x(run[0].distance_m),cy:y(run[0][role]),r:3,fill:color});run=[];};
    for(const point of points){if(!Number.isFinite(point.distance_m)||!Number.isFinite(point.reference)||!Number.isFinite(point.target)){flush();previous=null;continue;}if(previous&&(point.segment===null||point.segment===undefined||point.segment!==previous.segment||point.distance_m<=previous.distance_m))flush();run.push(point);previous=point;}flush();
  }figure.append(svg);$('vehicle-comparison-charts').append(figure);
}
$('compare-vehicle').addEventListener('click',async()=>{
  clearVehicleComparison();const epoch=vehicleComparisonEpoch,referenceFile=inspectedFileId,targetFile=targetSummary?.file_id,referenceLap=$('reference').value,targetLap=$('target').value,name=$('vehicle-channel').value;setBusy(true);$('vehicle-comparison-summary').textContent='공통 거리에서 차량 상태를 비교하는 중…';
  try{const params=new URLSearchParams({reference_file_id:referenceFile,reference_lap_number:referenceLap,target_file_id:targetFile,target_lap_number:targetLap,channel_name:name,points:301});const {comparison}=await api('/api/lmu/telemetry/vehicle-comparison?'+params);if(epoch===vehicleComparisonEpoch&&referenceFile===inspectedFileId&&targetFile===targetSummary?.file_id&&referenceLap===$('reference').value&&targetLap===$('target').value&&name===$('vehicle-channel').value)renderVehicleComparison(comparison);}
  catch(error){if(epoch===vehicleComparisonEpoch){$('vehicle-comparison-error').textContent=error.message;$('vehicle-comparison-summary').textContent='차량 상태를 비교하지 못했습니다. 이전 결과는 숨겼습니다.';}}
  finally{setBusy(false);}
});
function pathMap(path,brakes){
  if(path.status!=='SCHEMATIC'){$('map-status').textContent=path.reason||'경로를 표시할 수 없습니다.';return;}
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 840 480');svg.setAttribute('role','img');svg.setAttribute('aria-label','기록된 랩의 상대 주행 경로와 제동 시작 지점');
  const rows=path.points,xs=rows.map(p=>p.x_m),ys=rows.map(p=>p.y_m),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
  const scale=Math.min(760/Math.max(xmax-xmin,1),400/Math.max(ymax-ymin,1)),cx=(xmin+xmax)/2,cy=(ymin+ymax)/2,x=p=>420+(p.x_m-cx)*scale,y=p=>240-(p.y_m-cy)*scale;
  const add=(tag,attrs,text)=>{const el=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;svg.append(el);return el;};
  for(const segment of new Set(rows.map(p=>p.segment))){const group=rows.filter(p=>p.segment===segment);add('polyline',{points:group.map(p=>`${x(p)},${y(p)}`).join(' '),fill:'none',stroke:'#9edfe2','stroke-width':3});}
  add('circle',{cx:x(rows[0]),cy:y(rows[0]),r:6,fill:'#f1df91'});add('text',{x:x(rows[0])+9,y:y(rows[0])-9,fill:'#f1df91','font-size':14},'기록 시작');
  for(const z of brakes){if(z.status!=='COMPLETE'||z.start_distance_m===null)continue;const p=rows.reduce((a,b)=>Math.abs(a.distance_m-z.start_distance_m)<Math.abs(b.distance_m-z.start_distance_m)?a:b);const label=`제동 ${z.number}: ${z.start_distance_m.toFixed(1)} m`,dot=add('circle',{cx:x(p),cy:y(p),r:6,fill:'#ffb2a8',tabindex:0,'aria-label':label});const title=document.createElementNS(ns,'title');title.textContent=label;dot.append(title);}
  $('track-path').append(svg);$('map-status').textContent=`청록색: 주행 경로 / 붉은 점: 제동 시작 부근 (표시 지점에 근사, 마우스를 올리면 구간 번호·거리 표시) · ${path.source_frequency_hz} Hz · ${path.segment_count}개 연속 경로`;
}
function brakeDetail(cell,measurement){
  const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent=measurement?.status==='AVAILABLE'?'감속 시간 보기':'측정 불가';details.append(summary);
  const note=document.createElement('p');
  if(measurement?.status!=='AVAILABLE'){note.textContent=measurement?.reason||'심화 측정 결과가 없습니다.';details.append(note);cell.append(details);return;}
  note.textContent=`거리 지점 간격 ${measurement.distance_resolution_m.toFixed(2)} m · 제동 시작부터의 시간 · 속도 변화 기준 평균 감속. 최대 감속은 미연결입니다.`;details.append(note);
  const readings=measurement.metrics.brake_speed_time;
  if(!readings?.length){const p=document.createElement('p');p.textContent='5 km/h 단위 속도 통과 기록이 없습니다.';details.append(p);}
  else{const table=document.createElement('table');table.className='brake-speed-time';const caption=document.createElement('caption');caption.textContent='감속 중 속도별 첫 도달 시간';table.append(caption);const head=document.createElement('thead'),tr=document.createElement('tr');for(const text of ['속도(km/h)','제동 시작 후(초)']){const th=document.createElement('th');th.textContent=text;tr.append(th);}head.append(tr);table.append(head);const body=document.createElement('tbody');for(const [speed,time] of readings){const row=document.createElement('tr');for(const value of [speed.toFixed(1),time.toFixed(3)]){const td=document.createElement('td');td.textContent=value;row.append(td);}body.append(row);}table.append(body);details.append(table);}
  cell.append(details);
}
function zoneTable(container,data,braking=false){
  if(data.status==='UNAVAILABLE'){container.textContent='지원되는 입력 채널이 없습니다.';return;}
  if(data.status==='PARTIAL'){const note=document.createElement('p');note.textContent='일부 채널 또는 구간에 경계·결측이 있습니다. 비어 있는 값과 측정 상태를 함께 확인하세요.';container.append(note);}
  if(!data.zones.length){container.textContent='설정된 기준을 만족하는 사용 구간이 없습니다.';return;}
  const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr'),body=document.createElement('tbody');
  const headers=['구간','시작 거리(m)','끝 거리(m)','지속(초)','진입→이탈(km/h)','최대 입력(%)','측정 상태'];if(braking)headers.push('평균 감속(g)','감속 시간');
  for(const text of headers){const th=document.createElement('th');th.textContent=text;tr.append(th);}head.append(tr);table.append(head);
  const n=(v,d=1)=>v===null?'—':v.toFixed(d);
  for(const z of data.zones){const row=document.createElement('tr');for(const text of [z.number,n(z.start_distance_m),n(z.end_distance_m),n(z.duration_s,3),`${n(z.entry_speed_kmh)} → ${n(z.exit_speed_kmh)}`,n(z.peak_input_ratio*100),z.status==='COMPLETE'?'구간 확인':'경계·결측 포함']){const td=document.createElement('td');td.textContent=text;row.append(td);}if(braking){const mean=document.createElement('td'),detail=document.createElement('td');mean.textContent=n(z.shared_measurement?.metrics.avg_decel_g??null,2);row.append(mean,detail);brakeDetail(detail,z.shared_measurement);}body.append(row);}table.append(body);container.append(table);
}
$('analyze-lap').addEventListener('click',async()=>{
  setBusy(true);clearAnalysis();$('error').textContent='';$('analysis-summary').textContent='랩을 분석하는 중…';
  try{const params=new URLSearchParams({file_id:inspectedFileId,lap_number:$('analysis-lap').value,points:501});const {analysis:a}=await api('/api/lmu/telemetry/lap-analysis?'+params);
    $('analysis-summary').textContent=`랩 ${a.lap_number} · ${lapTime(a.recorded_lap_time_s)} · 게임 유효성 미확인\n제동 ${a.braking.zones.length}개 / 고스로틀 ${a.full_throttle.zones.length}개 (경계·결측 포함 구간도 표시)`;
    pathMap(a.track_path,a.braking.zones);zoneTable($('braking-zones'),a.braking,true);zoneTable($('throttle-zones'),a.full_throttle);$('normalized-channels').textContent=JSON.stringify(a.channels,null,2);$('analysis-result').hidden=false;
  }catch(error){$('error').textContent=error.message;$('analysis-summary').textContent='랩을 분석하지 못했습니다.';}
  finally{setBusy(false);}
});
const channelLabels={'Ground Speed':'속도','GPS Speed':'GPS 속도','Throttle Pos':'스로틀','Brake Pos':'브레이크','Steering Pos':'조향',speed_kmh:'속도',throttle_pct:'스로틀',brake_pct:'브레이크',steering_pct:'조향'};
const signed=value=>(value>=0?'+':'')+value.toFixed(3);
const distanceRange=s=>`${s.distance_start_m.toFixed(1)}–${s.distance_end_m.toFixed(1)} m`;
const cornerMetricSpecs=[
  ['duration_s','구간 소요 시간','초',3],['min_speed_kmh','최저 속도','km/h',1],
  ['exit_speed_kmh','이탈 속도','km/h',1],['brake_start_m','구간 내 최초 브레이크 8% 초과 위치','m',1],
  ['brake_release_m','마지막 브레이크 5% 초과 관측 위치','m',1],['throttle_first_m','기하 중심 이후 최초 스로틀 20% 초과 위치','m',1],
  ['full_throttle_m','기하 중심 이후 고스로틀 관측 위치','m',1],['peak_brake','최대 브레이크 입력','%',1,100],
  ['coast_time_s','타력 주행 관측 시간','초',3],['avg_decel_g','평균 감속','g',2],['braking_distance_m','제동 관측 거리','m',1]
];
const cornerReasonLabels={CHANNEL_MISSING:'채널 없음',MISSING_CHANNEL:'채널 없음',NO_BRAKE_EVENT:'제동 입력 기준 미충족',NO_BRAKING:'제동 입력 기준 미충족',NO_THROTTLE_EVENT:'스로틀 입력 기준 미충족',INSUFFICIENT_DATA:'샘플 부족',NO_CORNER_CANDIDATES:'기하 코너 후보 없음',NO_CORNERS:'기하 코너 후보 없음',REFERENCE_GEOMETRY_UNAVAILABLE:'기준 랩 좌표 부족',UNSUPPORTED_INPUT:'지원되는 입력 없음'};
const cornerReason=value=>cornerReasonLabels[value]||value||'지원되는 측정 결과 없음';
const cornerNumber=(value,digits=1,scale=1)=>Number.isFinite(value)?(value*scale).toFixed(digits):'측정 불가';
function cornerReading(measurement,key,digits=1,scale=1){
  const value=measurement?.metrics?.[key];if(Number.isFinite(value))return cornerNumber(value,digits,scale);
  return `측정 불가 (${cornerReason(measurement?.metric_support?.[key]?.reason||measurement?.reason)})`;
}
function clearCorners(){
  for(const id of ['corners','corner-metrics'])$(id).replaceChildren();
  for(const id of ['corner-summary','corner-title','corner-detail'])$(id).textContent='';
  $('corner-results').hidden=true;$('corner-focus').hidden=true;
}
function showCorner(report,corner){
  $('corner-title').textContent=`${corner.label} · ${cornerNumber(corner.start_m)}–${cornerNumber(corner.end_m)} m`;
  const resolutions=[corner.reference,corner.target].map(m=>cornerNumber(m?.distance_resolution_m,2));
  $('corner-detail').textContent=`기하 중심: ${cornerNumber(corner.apex_m)} m (실제 정점 미확인)\n측정 지점 간격: 기준 ${resolutions[0]} m / 비교 ${resolutions[1]} m\n거리 차이의 양수는 더 뒤쪽에서 관측됐다는 뜻입니다. 입력 시점의 원인이나 우열은 확정하지 않습니다.`;
  $('corner-metrics').replaceChildren();
  for(const [key,label,unit,digits,scale=1] of cornerMetricSpecs){
    const ref=corner.reference?.metrics?.[key],target=corner.target?.metrics?.[key],delta=key==='duration_s'?corner.delta_time_s:corner.delta?.[key];
    const difference=Number.isFinite(ref)&&Number.isFinite(target)&&Number.isFinite(delta)?delta:null;
    const deltaReason=corner.delta_support?.[key]?.reason||(key==='duration_s'?'양쪽 랩의 구간 시간 측정이 필요합니다.':'이 항목의 차이 비교는 제공되지 않습니다.');
    const row=document.createElement('tr');row.dataset.metric=key;
    for(const value of [label,unit,cornerReading(corner.reference,key,digits,scale),cornerReading(corner.target,key,digits,scale),Number.isFinite(difference)?`${difference>=0?'+':''}${(difference*scale).toFixed(digits)}`:`측정 불가 (${deltaReason})`]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
    $('corner-metrics').append(row);
  }
  for(const row of $('corners').children){const selected=row.dataset.corner===String(corner.id);row.setAttribute('aria-selected',selected);row.querySelector('button').setAttribute('aria-pressed',selected);}
  $('corner-focus').hidden=false;
}
function renderCorners(report){
  clearCorners();
  if(report?.status!=='AVAILABLE'||!report.corners?.length){$('corner-summary').textContent=report?`코너 후보를 비교할 수 없습니다: ${cornerReason(report.reason)}`:'이 비교 결과에는 코너 후보 분석이 없습니다.';return;}
  const largest=report.corners.filter(c=>Number.isFinite(c.delta_time_s)).sort((a,b)=>Math.abs(b.delta_time_s)-Math.abs(a.delta_time_s)).slice(0,3);
  const lines=largest.map(c=>`${c.label}: ${signed(c.delta_time_s)}초 (${c.delta_time_s>0?'시간 증가':c.delta_time_s<0?'시간 단축':'시간 차이 없음'})`);
  $('corner-summary').textContent=`기준 랩 ${report.definition?.reference_lap??'미확인'}의 고정 경계 · ${report.corners.length}개 기하 후보 · 공식 코너 구분 미확인\n절대 시간 차이가 큰 관측 후보:\n${lines.length?lines.join('\n'):'비교 가능한 구간 시간 없음'}\n상세에서 최저 속도·제동 위치·스로틀 관측 위치를 함께 비교하세요. 원인 판정이나 운전 처방은 제공하지 않습니다.`;
  for(const corner of report.corners){
    const row=document.createElement('tr');row.dataset.corner=String(corner.id);
    const values=[`${corner.label} · ${cornerNumber(corner.start_m)}–${cornerNumber(corner.end_m)}`,cornerReading(corner.reference,'duration_s',3),cornerReading(corner.target,'duration_s',3),Number.isFinite(corner.delta_time_s)?signed(corner.delta_time_s):'측정 불가'];
    for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
    if(Number.isFinite(corner.delta_time_s))row.children[3].className=corner.delta_time_s<0?'gain':corner.delta_time_s>0?'loss':'';
    const cell=document.createElement('td'),button=document.createElement('button');button.type='button';button.className='sector-button';button.textContent='측정 보기';button.setAttribute('aria-label',`${corner.label} 측정 보기`);button.addEventListener('click',()=>showCorner(report,corner));cell.append(button);row.append(cell);$('corners').append(row);
  }
  $('corner-results').hidden=false;showCorner(report,report.corners[0]);
}
function showSector(c,s){
  $('sector-title').textContent=`선택 구간: ${distanceRange(s)}`;
  $('sector-detail').textContent=`구간 시간 차이: ${signed(s.delta_time_s)}초\n누적 시간 차이: 진입 ${signed(s.cumulative_delta_start_s)}초 → 이탈 ${signed(s.cumulative_delta_end_s)}초\n아래 그래프의 파란 띠가 선택한 거리 구간입니다.`;
  $('sector-metrics').replaceChildren();
  for(const [name,metric] of Object.entries(s.channels)){
    const row=document.createElement('tr');const pair=key=>`${metric.reference[key].toFixed(2)} / ${metric.target[key].toFixed(2)}`;
    for(const value of [channelLabels[name]||name,metric.unit,pair('entry'),pair('minimum'),pair('exit'),pair('distance_mean')]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}$('sector-metrics').append(row);
  }
  for(const row of $('sectors').children){const selected=Number(row.dataset.sector)===s.sector_number;row.setAttribute('aria-selected',selected);row.querySelector('button').setAttribute('aria-pressed',selected);}
  const span=c.distance_end_m-c.distance_start_m;
  for(const band of document.querySelectorAll('.sector-band')){band.setAttribute('x',66+(s.distance_start_m-c.distance_start_m)/span*754);band.setAttribute('width',(s.distance_end_m-s.distance_start_m)/span*754);band.setAttribute('visibility','visible');}
  $('sector-focus').hidden=false;
}
function renderSectors(c){
  const fastest=c.sectors.reduce((a,b)=>a.delta_time_s<b.delta_time_s?a:b),slowest=c.sectors.reduce((a,b)=>a.delta_time_s>b.delta_time_s?a:b);
  const gain=fastest.delta_time_s < -1e-6 ? `가장 많이 단축된 구간: ${distanceRange(fastest)} · ${signed(fastest.delta_time_s)}초`:'기준보다 빨라진 거리 구간이 없습니다.';
  const loss=slowest.delta_time_s > 1e-6 ? `가장 많이 지연된 구간: ${distanceRange(slowest)} · ${signed(slowest.delta_time_s)}초`:'기준보다 느려진 거리 구간이 없습니다.';
  $('sector-summary').textContent=`${c.sector_length_m} m 간격 · ${c.sectors.length}개 구간\n${gain}\n${loss}\n공통 범위 구간 차이 합계: ${signed(c.common_delta_time_s)}초`;
  for(const s of c.sectors){const row=document.createElement('tr');row.dataset.sector=s.sector_number;
    for(const value of [distanceRange(s),s.reference_duration_s.toFixed(3),s.target_duration_s.toFixed(3),signed(s.delta_time_s)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}row.children[3].className=s.delta_time_s<0?'gain':'loss';
    const cell=document.createElement('td'),button=document.createElement('button');button.type='button';button.className='sector-button';button.textContent='구간 보기';button.setAttribute('aria-label',`${distanceRange(s)} 구간 보기`);button.addEventListener('click',()=>{showSector(c,s);$('sector-focus').scrollIntoView({block:'start',behavior:'smooth'});});cell.append(button);row.append(cell);$('sectors').append(row);
  }
  showSector(c,c.sectors[0]);
}
function plot(title,unit,points,series){
  const figure=document.createElement('figure'),caption=document.createElement('figcaption');caption.textContent=`${title} (${unit})`;figure.append(caption);
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 840 240');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${title}, 거리별 비교, 단위 ${unit}`);
  const add=(tag,attrs,text)=>{const el=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;svg.append(el);return el;};
  const values=series.flatMap(item=>points.map(p=>p[item.key])),min=Math.min(...values),max=Math.max(...values),pad=Math.max((max-min)*.1,.01),low=min-pad,high=max+pad;
  const start=points[0].distance_m,end=points.at(-1).distance_m,x=d=>66+(d-start)/(end-start)*754,y=v=>195-(v-low)/(high-low)*173;
  add('rect',{class:'sector-band',x:66,y:22,width:0,height:173,fill:'#5689b8',opacity:.23,visibility:'hidden'});
  for(let i=0;i<=4;i++){const value=low+(high-low)*i/4,pos=y(value);add('line',{x1:66,x2:820,y1:pos,y2:pos,stroke:'#344654'});add('text',{x:58,y:pos+4,fill:'#b4c7d6','font-size':12,'text-anchor':'end'},value.toFixed(1));}
  for(let i=0;i<=4;i++){const d=start+(end-start)*i/4;add('text',{x:x(d),y:218,fill:'#b4c7d6','font-size':12,'text-anchor':i===0?'start':i===4?'end':'middle'},`${d.toFixed(0)} m`);}
  if(low<0&&high>0)add('line',{x1:66,x2:820,y1:y(0),y2:y(0),stroke:'#8b9daa','stroke-dasharray':'4 4'});
  for(const item of series)add('polyline',{points:points.map(p=>`${x(p.distance_m)},${y(p[item.key])}`).join(' '),fill:'none',stroke:item.color,'stroke-width':2,'stroke-dasharray':item.dash||'none'});
  figure.append(svg);$('charts').append(figure);
}
function overlayMap(overlay){
  const paths=overlay.paths.filter(path=>path.status==='SCHEMATIC'),rows=paths.flatMap(path=>path.points);
  if(!rows.length){$('overlay-status').textContent=overlay.reason||'공유 기준 경로를 표시할 수 없습니다.';return;}
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 840 480');svg.setAttribute('role','img');svg.setAttribute('aria-label','기준 랩과 비교 랩의 상대 경로');
  const xs=rows.map(p=>p.x_m),ys=rows.map(p=>p.y_m),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),cx=(xmin+xmax)/2,cy=(ymin+ymax)/2,scale=Math.min(760/Math.max(1,xmax-xmin),400/Math.max(1,ymax-ymin));
  for(const path of paths){for(const segment of new Set(path.points.map(p=>p.segment))){const line=document.createElementNS(ns,'polyline');line.setAttribute('points',path.points.filter(p=>p.segment===segment).map(p=>`${420+(p.x_m-cx)*scale},${240-(p.y_m-cy)*scale}`).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke',path.role==='reference'?'#9edfe2':'#ffca80');line.setAttribute('stroke-width','2');if(path.role==='target')line.setAttribute('stroke-dasharray','6 3');svg.append(line);}}
  $('overlay-path').append(svg);$('overlay-status').textContent='기준: 청록색 / 비교: 주황색 점선. 기록된 게임 좌표를 공유 기준에 그린 도식입니다. 실제 지리 위치·세션 간 좌표계 일치·코너·주행선의 정확도는 미확인입니다. 위치를 자동 회전하거나 평행 이동해 맞추지 않습니다.'+(paths.length<2?' 일부 랩의 좌표가 없어 경로 하나만 표시합니다.':'');
}
$('compare').addEventListener('click',async()=>{
  setBusy(true);clearComparison();$('error').textContent='';$('comparison-summary').textContent='두 랩을 비교하는 중…';
  try{
    const params=new URLSearchParams({reference_file_id:inspectedFileId,target_file_id:targetSummary.file_id,reference_lap:$('reference').value,target_lap:$('target').value,points:301,sector_length_m:$('sector-length').value});
    const {comparison:c,sources}=await api('/api/lmu/telemetry/compare-files?'+params);
    $('comparison-summary').textContent=`기준: ${sources.reference.file_name}\n랩 ${c.reference_lap}: ${lapTime(c.reference_time_s)}\n비교: ${sources.target.file_name}\n랩 ${c.target_lap}: ${lapTime(c.target_time_s)}\n전체 기록 시간 차이: ${c.delta_time_s>=0?'+':''}${c.delta_time_s.toFixed(3)}초\n공통 비교 거리: ${c.distance_start_m.toFixed(1)}–${c.distance_end_m.toFixed(1)} m · ${c.sample_count}개 지점\n게임의 랩 유효성: 미확인`;
    plot('누적 시간 차이','초',c.points,[{key:'delta_time_s',color:'#f1df91'}]);
    for(const [name,metric] of Object.entries(c.channels)){
      if(name!=='GPS Speed'||!c.channels['Ground Speed'])plot(channelLabels[name]||name,metric.unit,c.points,[{key:name+'_reference',color:'#9edfe2'},{key:name+'_target',color:'#ffca80',dash:'6 3'}]);
      const row=document.createElement('tr');for(const value of [channelLabels[name]||name,`${metric.reference_source.frequency_hz} / ${metric.target_source.frequency_hz} Hz`,metric.unit,metric.mean_absolute_delta.toFixed(3)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}$('metrics').append(row);
    }
    overlayMap(c.track_overlay);$('comparison-support').textContent=JSON.stringify(c.channel_support,null,2);renderSectors(c);renderCorners(c.corner_comparison);$('comparison-result').hidden=false;
  }catch(error){$('error').textContent=error.message;$('comparison-summary').textContent='두 랩을 비교하지 못했습니다.';}
  finally{setBusy(false);}
});
const replayChoices=new Map(),replayColors=['#9edfe2','#ffca80','#e7a6ff','#b8e986','#ff9baf','#9bbcff'];
let replayData=null,replayDots=[],replayPositionStarts=[],replayProject=null,replayPlaying=false,replayRaf=0,replayClock=null,replayElapsed=0;
function selectedReplay(){return [...$('replay-choices').querySelectorAll('input:checked')].map(input=>replayChoices.get(input.value));}
function updatePractice(){
  const count=selectedReplay().length,eligible=count>=3&&count<=6;
  const overlap=selectedReplay().some(lap=>(practiceTask?.baseline?.sources||[]).some(source=>source.file_id===lap.file_id&&source.lap_number===lap.lap_number));
  $('save-practice').disabled=busy||!eligible||!currentConsistency?.focus?.length;
  $('compare-practice').disabled=busy||!eligible||!practiceTask||overlap;
  $('load-practice-tasks').disabled=busy;$('practice-task').disabled=busy;
  $('practice-selection-info').textContent=overlap?'현재 선택에 저장한 기준 랩이 포함되어 있습니다. 기준과 겹치지 않는 새 기록 랩 3~6개를 선택하세요.':!eligible?'위 목록에서 기록 랩 3~6개를 선택하세요.':!currentConsistency?.focus?.length?'관측 과제가 있는 일관성 분석을 실행하면 기준 기록을 저장할 수 있습니다.':'';
  for(const button of $('practice-history').querySelectorAll('button'))button.disabled=busy;
}
function clearPracticeComparison(){$('practice-comparison').hidden=true;$('practice-comparison-summary').textContent='';$('practice-comparison-values').replaceChildren();}
function practiceOptions(){
  const selected=practiceTask?.task_id||'';$('practice-task').replaceChildren(new Option('저장된 과제를 선택하세요',''));
  for(const task of practiceTasks)$('practice-task').add(new Option(`${task.created_at||'시각 미확인'} · ${task.task_id}`,task.task_id));
  $('practice-task').value=selected;
}
function renderPracticeTask(task,comparisons=[]){
  practiceTask=task||null;practiceComparisons=comparisons||[];renderPracticeHistory();clearPracticeComparison();$('practice-detail').hidden=!task;$('practice-baseline').textContent='';$('practice-baseline-inputs').replaceChildren();
  if(task){const b=task.baseline||{},c=b.consistency||b,sources=b.sources||[],metadata=b.metadata||{};
    const sourceText=sources.map((s,i)=>`${i+1}: ${s.file_name||s.file_id||'파일 미확인'} · 랩 ${s.lap_number??'미확인'}`).join('\n');
    const focus=(c.focus||b.focus||[]).map(f=>`${f.label}: ${f.observation||''}\n다음 비교 항목: ${f.next_check||'미확인'}`).join('\n');
    $('practice-baseline').textContent=`과제 ID: ${task.task_id}\n저장 시각: ${task.created_at||'미확인'}\n기준 랩 수: ${c.lap_count??sources.length}\n차량: ${metadata.car_name||'미확인'} / ${metadata.car_class||'미확인'}\n트랙: ${metadata.track_name||'미확인'} / ${metadata.track_layout||'미확인'}\n고정 경계 ID: ${(b.definition||c.definition)?.definition_id||'미확인'}\n${sourceText}\n${focus}`;
    const body=practiceTable($('practice-baseline-inputs'),'저장한 기준의 입력 위치 분포',['후보','입력 관측 항목','관측 랩 수','중앙 위치(m)','최소 위치(m)','최대 위치(m)','위치 범위(m)','중심에서 이미 활성']);
    for(const corner of c.corners||[])for(const key of ['brake_start_m','throttle_first_m','full_throttle_m']){const info=corner.input_consistency?.[key];practiceRow(body,[corner.label,info?.label||cornerMetricSpecs.find(spec=>spec[0]===key)[1],info?.sample_count??0,...['median_m','min_m','max_m','spread_m'].map(name=>practiceReading(info,name)),practiceActive(info,key)]);}
  }updatePractice();
}
function renderPracticeHistory(){
  $('practice-history').replaceChildren();$('practice-history-summary').textContent=practiceComparisons.length?`저장된 재비교 ${practiceComparisons.length}개. 이력 보기는 새 비교를 실행하지 않습니다.`:'저장된 재비교 이력이 없습니다.';
  for(const comparison of [...practiceComparisons].reverse()){const button=document.createElement('button');button.type='button';button.textContent=`${comparison.created_at||'시각 미확인'} · ${comparison.comparison_id||'기록 ID 미확인'} · 이력 보기`;button.disabled=busy;button.addEventListener('click',()=>{renderPracticeComparison(comparison);$('practice-message').textContent='저장된 재비교 이력을 표시했습니다. 새 비교는 실행하지 않았습니다.';});$('practice-history').append(button);}
}
function renderPracticeComparison(comparison){
  clearPracticeComparison();
  const state=comparison?.status==='AVAILABLE'?'입력 위치 범위 비교 가능':comparison?.status==='PARTIAL'?'일부 입력 위치만 비교 가능':'입력 위치 범위 비교 불가';
  $('practice-comparison-summary').textContent=`${state} · 새 기록 ${comparison?.lap_count??'미확인'}개 랩 · 저장한 경계 고정\n${comparison?.created_at?'저장 시각: '+comparison.created_at+'\n':''}${comparison?.reason||comparison?.note||'관측 변화이며 개선 효과를 확정하지 않습니다.'}\n위치 범위 차이는 새 기록 − 기준입니다. 음수는 관측 범위 감소이며 개선 성공을 뜻하지 않습니다. 랩 유효성·조건 동등성은 미확인입니다.`;
  const sources=document.createElement('p');sources.textContent='새 기록: '+((comparison?.sources||[]).map(source=>`${source.file_name||source.file_id||'파일 미확인'} · 랩 ${source.lap_number??'미확인'}`).join(' / ')||'원본 목록 미확인');$('practice-comparison-values').append(sources);
  const body=practiceTable($('practice-comparison-values'),'기준과 새 기록의 입력 위치 비교',['후보','입력 관측 항목','기준 랩 수','기준 중앙(m)','기준 범위(m)','새 랩 수','새 중앙(m)','새 범위(m)','범위 차이(m)','중심에서 이미 활성 · 기준 / 새']);
  const timeDetails=document.createElement('details'),timeTitle=document.createElement('summary');timeTitle.textContent='구간 시간 중앙값도 함께 보기';timeDetails.append(timeTitle);
  const timeBody=practiceTable(timeDetails,'구간 시간 중앙값 · 시간 효과 미확인',['후보','기준(초)','새 기록(초)']);
  for(const corner of comparison?.corners||[]){
    for(const key of ['brake_start_m','throttle_first_m','full_throttle_m']){
      const before=corner.baseline?.[key],after=corner.current?.[key],delta=corner.spread_delta_m?.[key];
      const supported=[before,after].every(info=>info?.status==='AVAILABLE'&&info.sample_count>=3);
      const difference=supported&&Number.isFinite(delta)?`${delta>=0?'+':''}${delta.toFixed(1)}`:`측정 불가 (${before?.reason||after?.reason||'같은 처리 단계에서 위치를 측정한 주행이 양쪽 모두 3회 이상 필요합니다.'})`;
      practiceRow(body,[corner.label,after?.label||before?.label||cornerMetricSpecs.find(spec=>spec[0]===key)[1],before?.sample_count??0,practiceReading(before,'median_m'),practiceReading(before,'spread_m'),after?.sample_count??0,practiceReading(after,'median_m'),practiceReading(after,'spread_m'),difference,`${practiceActive(before,key)} / ${practiceActive(after,key)}`]);
    }
    practiceRow(timeBody,[corner.label,cornerNumber(corner.baseline_median_duration_s,3),cornerNumber(corner.current_median_duration_s,3)]);
  }
  $('practice-comparison-values').append(timeDetails);$('practice-comparison').hidden=false;
}
function practiceReading(info,key){return info?.status==='AVAILABLE'&&info.sample_count>=3&&Number.isFinite(info[key])?cornerNumber(info[key],1):`측정 불가 (${info?.reason||'입력 위치를 측정한 주행이 3회 미만이거나 지원되지 않습니다.'})`;}
function practiceActive(info,key){return key==='brake_start_m'?'해당 없음':Array.isArray(info?.observations)?info.observations.filter(item=>item.active_at_center===true).length+'회':'미확인';}
function practiceTable(container,caption,headers){const table=document.createElement('table'),title=document.createElement('caption'),head=document.createElement('thead'),row=document.createElement('tr'),body=document.createElement('tbody');title.textContent=caption;for(const text of headers){const cell=document.createElement('th');cell.textContent=text;row.append(cell);}head.append(row);table.append(title,head,body);container.append(table);return body;}
function practiceRow(body,values){const row=document.createElement('tr');for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}body.append(row);}
$('save-practice').addEventListener('click',async()=>{
  const selections=selectedReplay(),epoch=practiceSelectionEpoch;setBusy(true);$('practice-error').textContent='';$('practice-message').textContent='현재 선택한 랩의 연습 과제를 저장하는 중…';clearPracticeComparison();
  try{const {task}=await api('/api/lmu/practice/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selections})});
    if(epoch!==practiceSelectionEpoch)return;practiceTasks=[task,...practiceTasks.filter(item=>item.task_id!==task.task_id)];renderPracticeTask(task);practiceOptions();$('practice-message').textContent='연습 과제를 로컬에 저장했습니다. 다른 기록 랩을 선택해 재비교할 수 있습니다.';
  }catch(error){if(epoch===practiceSelectionEpoch){$('practice-error').textContent=error.message;$('practice-message').textContent='연습 과제를 저장하지 못했습니다.';}}
  finally{setBusy(false);}
});
$('load-practice-tasks').addEventListener('click',async()=>{
  setBusy(true);$('practice-error').textContent='';$('practice-message').textContent='저장된 과제 목록을 읽는 중…';
  try{const {tasks}=await api('/api/lmu/practice/tasks');practiceTasks=tasks||[];practiceOptions();$('practice-message').textContent=`저장된 과제 ${practiceTasks.length}개. 목록에서 과제를 선택하세요.`;}
  catch(error){$('practice-error').textContent=error.message;$('practice-message').textContent='과제 목록을 읽지 못했습니다.';}
  finally{setBusy(false);}
});
$('practice-task').addEventListener('change',async()=>{
  const id=$('practice-task').value;renderPracticeTask(null);$('practice-error').textContent='';$('practice-message').textContent='';if(!id)return;setBusy(true);
  try{const {task,comparisons}=await api('/api/lmu/practice/tasks/'+encodeURIComponent(id));if($('practice-task').value===id)renderPracticeTask(task,comparisons);}
  catch(error){$('practice-error').textContent=error.message;}
  finally{setBusy(false);}
});
$('compare-practice').addEventListener('click',async()=>{
  if(!practiceTask)return;const taskId=practiceTask.task_id,selections=selectedReplay(),epoch=practiceSelectionEpoch;
  setBusy(true);clearPracticeComparison();$('practice-error').textContent='';$('practice-message').textContent='저장한 기준 경계로 새 기록을 비교하는 중…';
  try{const {comparison}=await api('/api/lmu/practice/tasks/'+encodeURIComponent(taskId)+'/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selections})});
    if(epoch!==practiceSelectionEpoch||practiceTask?.task_id!==taskId)return;practiceComparisons=[...practiceComparisons.filter(item=>item.comparison_id!==comparison.comparison_id),comparison];renderPracticeHistory();renderPracticeComparison(comparison);$('practice-message').textContent='새 기록의 관측을 비교했습니다. 시간 효과나 개선 성공은 미확인입니다.';
  }catch(error){if(epoch===practiceSelectionEpoch){$('practice-error').textContent=error.message;$('practice-message').textContent='연습 과제를 재비교하지 못했습니다.';}}
  finally{setBusy(false);}
});
function updateReplay(){const count=selectedReplay().length;$('load-replay').disabled=busy||count<2||count>6;$('load-consistency').disabled=busy||count<3||count>6;updatePractice();}
function clearConsistency(){currentConsistency=null;practiceSelectionEpoch++;clearPracticeComparison();$('practice-message').textContent='';$('practice-error').textContent='';$('consistency-summary').textContent='';$('consistency-result').hidden=true;for(const id of ['consistency-corners','consistency-inputs','consistency-focus'])$(id).replaceChildren();$('consistency-input-reason').textContent='';$('consistency-times').open=false;updatePractice();}
function renderConsistency(report){
  clearConsistency();
  if(!['AVAILABLE','PARTIAL'].includes(report?.status)){$('consistency-summary').textContent=`일관성을 분석할 수 없습니다: ${cornerReason(report?.reason)}`;return;}
  currentConsistency=report;updatePractice();
  $('consistency-summary').textContent=`선택한 ${report.lap_count}개 랩 · 기준 랩 고정 경계 · 랩 유효성·조건 동등성 미확인`+(report.status==='PARTIAL'?'\n일부 구간은 완전한 관측이 3회 미만입니다.':'');
  $('consistency-input-reason').textContent=report.input_focus_reason||'';
  for(const corner of report.corners||[]){
    for(const key of ['brake_start_m','throttle_first_m','full_throttle_m']){
      const info=corner.input_consistency?.[key],inputRow=document.createElement('tr');inputRow.dataset.metric=key;
      const available=info?.status==='AVAILABLE'&&info.sample_count>=3;
      const fallback=info?.status==='INPUT_STAGE_MISMATCH'?'입력 처리 단계가 확인되지 않거나 달라 반복성을 비교하지 않았습니다.':info?'입력 위치를 측정한 주행이 3회 미만입니다.':'이 결과에는 입력 반복성 정보가 없습니다.';
      const reading=name=>available&&Number.isFinite(info[name])?cornerNumber(info[name],1):`측정 불가 (${info?.reason||fallback})`;
      const activeCount=(info?.observations||[]).filter(item=>item.active_at_center===true).length;
      const label=(info?.label||cornerMetricSpecs.find(spec=>spec[0]===key)[1])+(key!=='brake_start_m'&&activeCount?` · 기하 중심에서 이미 활성 ${activeCount}회`:'');
      for(const value of [`${corner.label} · ${cornerNumber(corner.start_m)}–${cornerNumber(corner.end_m)}`,label,info?.sample_count??0,...['median_m','min_m','max_m','spread_m'].map(reading)]){const cell=document.createElement('td');cell.textContent=value;inputRow.append(cell);}
      $('consistency-inputs').append(inputRow);
    }
    const enough=corner.sample_count>=3,row=document.createElement('tr');
    const metric=key=>enough?cornerNumber(corner[key],3):`측정 불가 (${cornerReason(corner.reason||'완전한 관측 랩이 3개 미만입니다.')})`;
    for(const value of [`${corner.label} · ${cornerNumber(corner.start_m)}–${cornerNumber(corner.end_m)}`,corner.sample_count??0,...['median_duration_s','min_duration_s','max_duration_s','spread_s','std_duration_s'].map(metric)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
    $('consistency-corners').append(row);
  }
  for(const focus of report.focus||[]){const detail=document.createElement('details'),summary=document.createElement('summary'),observation=document.createElement('p'),next=document.createElement('p');summary.textContent=focus.label;observation.textContent=focus.observation;next.textContent='다음 비교 항목: '+focus.next_check;detail.append(summary,observation,next);$('consistency-focus').append(detail);}
  $('consistency-result').hidden=false;
}
$('load-consistency').addEventListener('click',async()=>{
  const selections=selectedReplay();clearConsistency();const epoch=practiceSelectionEpoch;setBusy(true);$('error').textContent='';$('consistency-summary').textContent='선택한 랩의 구간별 일관성을 분석하는 중…';
  try{const result=await api('/api/lmu/telemetry/corner-consistency',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selections})});if(epoch===practiceSelectionEpoch)renderConsistency(result.consistency);}
  catch(error){if(epoch===practiceSelectionEpoch){$('error').textContent=error.message;$('consistency-summary').textContent='일관성을 분석하지 못했습니다.';}}
  finally{setBusy(false);}
});
function stopReplay(){replayPlaying=false;cancelAnimationFrame(replayRaf);replayRaf=0;replayClock=null;$('replay-play').textContent='재생';}
function clearReplay(){stopReplay();replayData=null;replayDots=[];replayPositionStarts=[];replayElapsed=0;$('replay-result').hidden=true;$('replay-map').replaceChildren();$('replay-values').replaceChildren();$('replay-summary').textContent='';$('replay-support').textContent='';$('replay-slider').value=0;$('replay-time').textContent='';}
function addReplayChoices(file){
  for(const lap of file.laps.filter(lap=>lap.status==='VALIDITY_UNKNOWN'&&lap.distance_m!==null)){
    const key=file.file_id+':'+lap.lap_number;if(replayChoices.has(key))continue;
    const selection={file_id:file.file_id,lap_number:lap.lap_number};replayChoices.set(key,selection);
    const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=key;
    label.append(input,document.createTextNode(` ${file.file_name} · 랩 ${lap.lap_number} · ${lapTime(lap.recorded_lap_time_s)} · 유효성 미확인`));
    input.addEventListener('change',()=>{clearReplay();clearConsistency();updateReplay();const count=selectedReplay().length;if(count>6)$('replay-summary').textContent='최대 6개 랩을 선택하세요.';});$('replay-choices').append(label);
  }updateReplay();
}
function drawReplay(){
  const rows=replayData.paths.flatMap(path=>path.points),xs=rows.map(p=>p.x_m),ys=rows.map(p=>p.y_m),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),cx=(xmin+xmax)/2,cy=(ymin+ymax)/2,scale=Math.min(760/Math.max(1,xmax-xmin),400/Math.max(1,ymax-ymin));
  replayProject=p=>({x:420+(p.x_m-cx)*scale,y:240-(p.y_m-cy)*scale});
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 840 480');svg.setAttribute('role','img');svg.setAttribute('aria-label','선택한 여러 랩의 오프라인 고스트 위치');
  const add=(tag,attrs)=>{const el=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);svg.append(el);return el;};
  for(const [i,path] of replayData.paths.entries()){for(const segment of new Set(path.points.map(p=>p.segment))){add('polyline',{points:path.points.filter(p=>p.segment===segment).map(p=>{const q=replayProject(p);return `${q.x},${q.y}`;}).join(' '),fill:'none',stroke:replayColors[i],opacity:.55,'stroke-width':2});}}
  for(const [i,path] of replayData.paths.entries()){const dot=add('circle',{r:7,fill:replayColors[i],stroke:'#101820','stroke-width':2,visibility:'hidden'});const title=document.createElementNS(ns,'title');title.textContent=`선택 ${i+1} · 랩 ${path.lap_number}`;dot.append(title);replayDots.push(dot);}
  $('replay-map').append(svg);
}
function replayFrame(index){
  if(!replayData)return;const frame=replayData.frames[index];$('replay-slider').value=index;$('replay-slider').setAttribute('aria-valuetext',`${frame.elapsed_s.toFixed(2)}초`);$('replay-time').textContent=`${frame.elapsed_s.toFixed(2)} / ${replayData.duration_s.toFixed(2)}초`;$('replay-values').replaceChildren();
  for(const state of frame.laps){const i=state.selection_index,dot=replayDots[i];if(state.status==='SUPPORTED'&&state.position){const p=replayProject(state.position);dot.setAttribute('cx',p.x);dot.setAttribute('cy',p.y);dot.setAttribute('visibility','visible');}else dot.setAttribute('visibility','hidden');
    const format=(value,scale=1)=>value===null?'—':(value*scale).toFixed(1),source=replayData.sources[i],row=document.createElement('tr');
    const label=`${i+1} · ${source.file_name} · 랩 ${source.lap_number}`;
    const status=state.status==='SUPPORTED'?'표시 중':state.status==='FINISHED'?'랩 종료':replayPositionStarts[i]!==null&&frame.elapsed_s<replayPositionStarts[i]?'위치 기록 시작 전':'위치 미확인';
    for(const value of [label,status,format(state.distance_m),format(state.speed_mps,3.6),format(state.throttle_ratio,100),format(state.brake_ratio,100)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}row.children[0].style.color=replayColors[i];row.children[0].style.overflowWrap='anywhere';$('replay-values').append(row);
  }
}
function replayTick(now){
  if(!replayPlaying||!replayData)return;
  // RAF reports the frame start, which can precede performance.now() in a click.
  // Establish and advance the clock only with RAF timestamps, including resume.
  if(replayClock===null)replayClock=now;
  const elapsedStep=Math.max(0,now-replayClock)/1000;replayClock=Math.max(replayClock,now);
  replayElapsed=Math.max(0,Math.min(replayData.duration_s,replayElapsed+elapsedStep*Number($('replay-speed').value)));
  const index=Math.max(0,Math.min(replayData.frames.length-1,Math.floor(replayElapsed/replayData.duration_s*(replayData.frames.length-1)+1e-9)));if(index!==Number($('replay-slider').value))replayFrame(index);
  if(replayElapsed>=replayData.duration_s){stopReplay();return;}replayRaf=requestAnimationFrame(replayTick);
}
$('load-replay').addEventListener('click',async()=>{
  const selections=selectedReplay();clearReplay();setBusy(true);$('error').textContent='';$('replay-summary').textContent='기록 재생을 준비하는 중…';
  try{const result=await api('/api/lmu/telemetry/replay',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selections,points:1001})});replayData=result.replay;replayPositionStarts=replayData.paths.map((_,i)=>replayData.frames.find(frame=>frame.laps[i].status==='SUPPORTED'&&frame.laps[i].position)?.elapsed_s??null);drawReplay();$('replay-slider').max=replayData.frames.length-1;$('replay-result').hidden=false;replayFrame(0);$('replay-support').textContent=JSON.stringify(replayData.paths.map(path=>({selection:path.selection_index+1,channels:path.channels})),null,2);$('replay-summary').textContent=`재생 준비 완료. 재생 버튼을 누르세요.\n${replayData.sources.length}개 랩 · 랩 경과 시간 기준 · 표시 간격 약 ${(replayData.duration_s/(replayData.frame_count-1)).toFixed(3)}초\n시작 경계에 위치가 없는 랩은 기록이 시작되면 표시됩니다. 첫 번째 선택이 좌표 기준입니다. 기록 재생이며 게임 내 고스트가 아닙니다.`;}
  catch(error){$('error').textContent=error.message;$('replay-summary').textContent='재생을 준비하지 못했습니다.';}
  finally{setBusy(false);}
});
$('replay-play').addEventListener('click',()=>{if(!replayData)return;if(replayPlaying){stopReplay();return;}if(replayElapsed>=replayData.duration_s){replayElapsed=0;replayFrame(0);}replayPlaying=true;$('replay-play').textContent='일시정지';replayClock=null;replayRaf=requestAnimationFrame(replayTick);});
$('replay-slider').addEventListener('input',()=>{stopReplay();const index=Number($('replay-slider').value);replayFrame(index);if(replayData)replayElapsed=replayData.frames[index].elapsed_s;});
$('replay-reset').addEventListener('click',()=>{stopReplay();replayElapsed=0;replayFrame(0);});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopReplay();});
window.addEventListener('pagehide',stopReplay);
refresh();
</script></html>'''
