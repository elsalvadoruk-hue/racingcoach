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
</style><main>
<h1>LMU 텔레메트리 진단</h1>
<p>LMU 전용 파일 읽기와 연동 상태를 확인합니다.</p>
<section><strong>현재 개발 단계</strong>
<p>완료된 <code>.duckdb</code> 파일을 읽기 전용으로 검사합니다. 랩 유효성 판정·베스트 랩 비교·주행 개선 분석·실시간 수집은 후속 작업입니다.</p>
<small>이 화면은 별도 프로세스와 포트를 사용합니다. iRacing 설정·주행 기록·실시간 수집기를 공유하지 않습니다.</small></section>
<section><h2>연결 상태</h2><button id="refresh" type="button">새로고침</button>
<pre id="status" aria-live="polite">확인 중…</pre>
<p id="error" role="alert"></p></section>
<section><h2>기록 파일</h2><label for="files">검사할 파일</label><br>
<select id="files"><option value="">파일 목록을 불러오는 중…</option></select>
<button id="inspect" type="button" disabled>파일 검사</button>
<p><small>다른 Steam 라이브러리 경로는 실행 전 <code>LMU_TELEMETRY_DIRS</code>로 지정하세요. 여러 경로는 Windows에서 <code>;</code>로 구분합니다.</small></p>
<pre id="metadata"></pre><div class="scroll"><table id="laps" hidden>
<thead><tr><th>랩</th><th>경계 간 시간(초)</th><th>기록 시간(초)</th><th>판정 상태</th></tr></thead><tbody></tbody></table></div>
<details id="details" hidden><summary>채널·이벤트 상세</summary><pre id="raw"></pre></details></section>
<p><a href="/docs">LMU 전용 API 문서</a></p></main>
<script>
const $=id=>document.getElementById(id);
async function api(path){const response=await fetch(path,{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.detail||'조회에 실패했습니다.');return data;}
function clearFile(){$('metadata').textContent='';$('laps').hidden=true;$('details').hidden=true;}
async function refresh(){
  $('refresh').disabled=true;$('inspect').disabled=true;$('error').textContent='';clearFile();
  try{const [status,listing]=await Promise.all([api('/api/lmu/status'),api('/api/lmu/telemetry/files')]);
    $('status').textContent=`파일 읽기 패키지: ${status.dependency}\n실시간 수집: ${status.live.state}\n발견한 파일: ${status.file_count}\n`+status.telemetry_dirs.map(dir=>`${dir.exists?'폴더 확인':'폴더 없음'}: ${dir.path}`).join('\n');
    if(status.error)$('error').textContent=status.error;
    $('files').replaceChildren();if(!listing.files.length){const opt=new Option('발견한 LMU 파일이 없습니다.','');$('files').add(opt);}
    for(const file of listing.files)$('files').add(new Option(file.file_name+' ('+file.file_id.slice(0,6)+')',file.file_id));
    $('inspect').disabled=!listing.files.length||status.dependency!=='READY';
  }catch(error){$('error').textContent=error.message;$('status').textContent='상태를 확인하지 못했습니다.';}
  finally{$('refresh').disabled=false;}
}
$('refresh').addEventListener('click',refresh);$('files').addEventListener('change',clearFile);
$('inspect').addEventListener('click',async()=>{
  $('inspect').disabled=true;$('refresh').disabled=true;$('files').disabled=true;$('error').textContent='';clearFile();
  try{const {file}=await api('/api/lmu/telemetry/file?file_id='+encodeURIComponent($('files').value));
    $('metadata').textContent=JSON.stringify(file.metadata,null,2)+'\n\n게임의 랩 유효성은 확인되지 않았습니다. 베스트 랩 자동 선택은 비활성 상태입니다.';
    const body=$('laps').querySelector('tbody');body.replaceChildren();
    const labels={VALIDITY_UNKNOWN:'유효성 미확인',UNTIMED:'기록 시간 없음',TIMING_MISMATCH:'시간 불일치',PIT_LAP:'피트 포함',INCOMPLETE:'샘플 부족',DISTANCE_DISCONTINUITY:'거리 불연속'};
    for(const lap of file.laps){const row=document.createElement('tr');for(const value of [lap.lap_number,lap.lap_time_s.toFixed(3),lap.recorded_lap_time_s?.toFixed(3)??'—',labels[lap.status]||lap.status]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}body.append(row);}
    $('laps').hidden=!file.laps.length;if(!file.laps.length)$('metadata').textContent+='\n양쪽 경계가 기록된 랩이 없습니다.';
    $('raw').textContent=JSON.stringify({channels:file.channels,events:file.events,sample_counts:file.sample_counts},null,2);$('details').hidden=false;
  }catch(error){$('error').textContent=error.message;}
  finally{$('inspect').disabled=false;$('refresh').disabled=false;$('files').disabled=false;}
});refresh();
</script></html>'''
