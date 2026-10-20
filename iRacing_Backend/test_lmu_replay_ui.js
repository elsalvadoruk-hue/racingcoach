// Run with: node --test iRacing_Backend/test_lmu_replay_ui.js
// jsdom is supplied by the existing frontend dependencies. No live API is used.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');

const html=fs.readFileSync(path.join(__dirname,'lmu_ui.py'),'utf8').split("PAGE = r'''")[1].split("'''")[0];
const duration=125.56;
const file={file_id:'a'.repeat(24),file_name:'replay.duckdb',metadata:{},
  laps:[1,2].map(lap_number=>({lap_number,status:'VALIDITY_UNKNOWN',distance_m:5700,
    lap_time_s:duration,recorded_lap_time_s:duration}))};
function replayFixture(){
  return {duration_s:duration,frame_count:1001,sources:[1,2].map(lap_number=>({file_name:file.file_name,lap_number})),
    paths:[1,2].map((lap_number,selection_index)=>({selection_index,lap_number,channels:{},
      points:[{x_m:0,y_m:0,segment:0},{x_m:100,y_m:100,segment:0}]})),
    frames:Array.from({length:1001},(_,index)=>({elapsed_s:index*duration/1000,
      laps:[0,1].map(selection_index=>{
        const status=index===1000?'FINISHED':index<=selection_index||index===10?'NO_POSITION':'SUPPORTED';
        return {selection_index,status,position:status==='SUPPORTED'?{x_m:index/10,y_m:index/10}:null,
          distance_m:status==='SUPPORTED'?index*5.7:null,speed_mps:50,throttle_ratio:1,brake_ratio:0};
      })}))};
}
async function waitFor(check){
  for(let n=0;n<100;n++){if(check())return;await new Promise(resolve=>setTimeout(resolve,1));}
  assert.fail('UI wait timeout');
}
async function setup(t){
  const errors=[],callbacks=new Map();let clock=1000,nextId=0;
  const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(String(error)));
  const dom=new JSDOM(html,{url:'http://localhost/',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
    w.performance.now=()=>clock;
    w.requestAnimationFrame=callback=>{callbacks.set(++nextId,callback);return nextId;};
    w.cancelAnimationFrame=id=>callbacks.delete(id);
    w.fetch=async url=>{
      let body;
      if(url==='/api/lmu/status')body={dependency:'READY',live:{state:'NOT_IMPLEMENTED'},file_count:1,telemetry_dirs:[]};
      else if(url==='/api/lmu/telemetry/files')body={files:[file]};
      else if(url.startsWith('/api/lmu/telemetry/file?'))body={file};
      else if(url==='/api/lmu/telemetry/replay')body={replay:replayFixture()};
      else throw Error('Unexpected API request: '+url);
      return {ok:true,json:async()=>body};
    };
  }});
  t.after(()=>dom.window.close());
  const w=dom.window,d=w.document,$=id=>d.getElementById(id);
  await waitFor(()=>!$('inspect').disabled);
  $('inspect').click();await waitFor(()=>!$('comparison').hidden);
  for(const input of d.querySelectorAll('#replay-choices input')){
    input.checked=true;input.dispatchEvent(new w.Event('change'));
  }
  $('load-replay').click();await waitFor(()=>!$('replay-result').hidden);
  return {w,d,$,errors,callbacks,
    tick(now){clock=now;const pending=[...callbacks.values()];callbacks.clear();for(const cb of pending)cb(now);},
    seek(index){$('replay-slider').value=index;$('replay-slider').dispatchEvent(new w.Event('input'));},
    states(){return [...d.querySelectorAll('#replay-values tr')].map(row=>row.children[1].textContent);},
    visible(){return d.querySelectorAll('#replay-map circle[visibility="visible"]').length;}};
}

test('시작·재개 첫 프레임의 이른 시각에도 재생이 중단되거나 뒤로 가지 않는다',async t=>{
  const ui=await setup(t),{$,tick,callbacks,errors}=ui;
  $('replay-play').click();
  // A RAF timestamp describes the frame start and can precede performance.now()
  // sampled during the click handler. This used to select frame -1 and throw.
  tick(995.7);
  assert.equal($('replay-slider').value,'0');
  assert.equal(callbacks.size,1);
  tick(1195.7);
  assert.ok(Number($('replay-slider').value)>0);
  assert.ok(ui.visible()>0);
  $('replay-play').click();assert.equal(callbacks.size,0);
  ui.seek(500);$('replay-play').click();tick(1190);
  assert.equal($('replay-slider').value,'500');
  tick(1390);assert.ok(Number($('replay-slider').value)>500);
  assert.deepEqual(errors,[]);
});

test('시작 전·위치 결측·종료를 구분하고 위치를 만들어 채우지 않는다',async t=>{
  const ui=await setup(t);
  assert.deepEqual(ui.states(),['위치 기록 시작 전','위치 기록 시작 전']);
  assert.equal(ui.visible(),0);
  assert.match(ui.$('replay-summary').textContent,/재생 버튼/);
  ui.seek(1);assert.deepEqual(ui.states(),['표시 중','위치 기록 시작 전']);assert.equal(ui.visible(),1);
  ui.seek(2);assert.equal(ui.visible(),2);
  ui.seek(10);assert.deepEqual(ui.states(),['위치 미확인','위치 미확인']);assert.equal(ui.visible(),0);
  ui.seek(11);assert.equal(ui.visible(),2);
  ui.seek(1000);assert.deepEqual(ui.states(),['랩 종료','랩 종료']);assert.equal(ui.visible(),0);
  ui.$('replay-reset').click();assert.equal(ui.$('replay-slider').value,'0');
  assert.deepEqual(ui.states(),['위치 기록 시작 전','위치 기록 시작 전']);
  assert.deepEqual(ui.errors,[]);
});

test('속도·정지·페이지 숨김·자동 종료와 종료 뒤 재생을 유지한다',async t=>{
  const ui=await setup(t),{$,tick,callbacks,w,d}=ui;
  $('replay-speed').value='2';$('replay-play').click();tick(1000);tick(2000);
  assert.equal($('replay-slider').value,'15');
  $('replay-play').click();const paused=$('replay-slider').value;tick(5000);
  assert.equal($('replay-slider').value,paused);assert.equal(callbacks.size,0);
  $('replay-play').click();Object.defineProperty(d,'hidden',{configurable:true,value:true});
  d.dispatchEvent(new w.Event('visibilitychange'));assert.equal(callbacks.size,0);
  assert.equal($('replay-play').textContent,'재생');
  Object.defineProperty(d,'hidden',{configurable:true,value:false});
  $('replay-play').click();tick(6000);tick(200000);
  assert.equal($('replay-slider').value,'1000');assert.equal(callbacks.size,0);
  $('replay-play').click();tick(200001);assert.equal($('replay-slider').value,'0');
  tick(200201);assert.ok(ui.visible()>0);
  assert.deepEqual(ui.errors,[]);
});

test('제동 심화 표는 평균 감속·속도별 시간을 표시하고 미지원과 0을 구분한다',async t=>{
  const ui=await setup(t),{w,$}=ui;
  const base={number:1,start_distance_m:100,end_distance_m:250,duration_s:3,
    entry_speed_kmh:250,exit_speed_kmh:140,peak_input_ratio:.9,status:'COMPLETE'};
  const shared={status:'AVAILABLE',distance_resolution_m:2,
    metrics:{avg_decel_g:1.04,brake_speed_time:[[245,.2],[240,.3]]}};
  const zones=[{...base,shared_measurement:shared},
    {...base,number:2,shared_measurement:{...shared,metrics:{avg_decel_g:0,brake_speed_time:[]}}},
    {...base,number:3,status:'PARTIAL',shared_measurement:{status:'UNAVAILABLE',reason:'속도 샘플 결측',metrics:{avg_decel_g:null}}}];
  w.zoneTable($('braking-zones'),{status:'PARTIAL',zones},true);
  const table=$('braking-zones').querySelector('table'),rows=[...table.tBodies[0].rows];
  assert.equal(table.tHead.rows[0].cells.length,9);
  assert.equal(rows[0].cells[7].textContent,'1.04');
  assert.equal(rows[1].cells[7].textContent,'0.00');
  assert.equal(rows[2].cells[7].textContent,'—');
  assert.equal(rows[2].cells[8].querySelector('summary').textContent,'측정 불가');
  assert.match(rows[2].cells[8].textContent,/속도 샘플 결측/);
  assert.deepEqual([...rows[0].querySelectorAll('.brake-speed-time tbody tr')].map(row=>row.textContent),['245.00.200','240.00.300']);
  assert.match(rows[1].cells[8].textContent,/속도 통과 기록이 없습니다/);
  w.zoneTable($('throttle-zones'),{status:'AVAILABLE',zones:[base]});
  assert.equal($('throttle-zones').querySelector('thead tr').cells.length,7);
  assert.deepEqual(ui.errors,[]);
});

async function setupLive(t,respond){
  const requests=[],errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));
  const stopped={state:'STOPPED',collecting:false,valid:null,validation_state:'GAME_SCREEN_UNVERIFIED'};
  const dom=new JSDOM(html,{url:'http://localhost/',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
    w.requestAnimationFrame=()=>1;w.cancelAnimationFrame=()=>{};
    w.fetch=async(url,options)=>{
      requests.push({url,method:options.method||'GET'});
      const override=await respond?.(url,options);
      const body=override??(url==='/api/lmu/status'?{dependency:'READY',live:stopped,file_count:0,telemetry_dirs:[]}:
        url==='/api/lmu/telemetry/files'?{files:[]}:stopped);
      return {ok:true,json:async()=>body};
    };
  }});
  t.after(()=>dom.window.close());
  const w=dom.window,$=id=>w.document.getElementById(id);
  await waitFor(()=>$('status').textContent.includes('READY'));
  return {w,$,requests,errors};
}

function cornerFixture(){
  const measurement=(extra={})=>({status:'AVAILABLE',distance_resolution_m:2,
    metrics:{duration_s:8,min_speed_kmh:90,exit_speed_kmh:130,brake_start_m:210,
      throttle_first_m:330,peak_brake:.8,...extra},
    metric_support:{brake_release_m:{status:'UNAVAILABLE',reason:'채널 샘플 결측'}}});
  return {status:'AVAILABLE',definition:{reference_lap:1},reference_valid:null,target_valid:null,
    corners:[{id:'corner_1',label:'구간 1',start_m:200,apex_m:300,end_m:400,
      reference:measurement(),target:measurement({duration_s:8.5,min_speed_kmh:95,brake_start_m:214,peak_brake:.9}),
      delta_time_s:.5,delta:{brake_start_m:4,min_speed_kmh:5}},
      {id:'corner_2',label:'구간 2',start_m:600,apex_m:700,end_m:800,
        reference:measurement(),target:measurement({duration_s:7}),delta_time_s:-1,delta:{}}]};
}

test('코너 후보의 시간차 부호·단위·결측 사유와 불확실성을 구분한다',async t=>{
  const {$,w,errors}=await setupLive(t);w.renderCorners(cornerFixture());
  assert.equal($('corners').rows.length,2);
  assert.equal($('corners').rows[0].cells[3].textContent,'+0.500');
  assert.equal($('corners').rows[1].cells[3].textContent,'-1.000');
  assert.match($('corner-summary').textContent,/기준 랩 1의 고정 경계/);
  assert.ok($('corner-summary').textContent.indexOf('구간 2:')<$('corner-summary').textContent.indexOf('구간 1:'));
  const metric=key=>$('corner-metrics').querySelector(`[data-metric="${key}"]`);
  assert.deepEqual([...metric('min_speed_kmh').cells].map(c=>c.textContent),['최저 속도','km/h','90.0','95.0','+5.0']);
  assert.equal(metric('peak_brake').cells[2].textContent,'80.0');
  assert.match(metric('peak_brake').cells[4].textContent,/차이 비교는 제공되지 않습니다/);
  assert.equal(metric('brake_start_m').cells[4].textContent,'+4.0');
  assert.match(metric('brake_release_m').cells[2].textContent,/측정 불가 \(채널 샘플 결측\)/);
  assert.match(metric('brake_release_m').cells[4].textContent,/측정 불가/);
  assert.match($('corner-detail').textContent,/실제 정점 미확인/);
  assert.match($('corner-comparison').textContent,/실제 재가속 시작을 보장하지 않습니다/);
  $('corners').rows[1].querySelector('button').click();
  assert.match($('corner-title').textContent,/구간 2/);
  assert.equal($('corners').rows[1].getAttribute('aria-selected'),'true');
  assert.equal(metric('duration_s').cells[4].textContent,'-1.000');
  assert.deepEqual(errors,[]);
});

test('입력 처리 단계가 다른 측정의 명시적 null 차이를 재계산하지 않는다',async t=>{
  const {$,w,errors}=await setupLive(t),report=cornerFixture(),corner=report.corners[0];
  corner.delta.brake_start_m=null;corner.delta_support={brake_start_m:{status:'INPUT_STAGE_MISMATCH',reason:'두 랩의 입력 처리 단계가 다릅니다.'}};
  w.renderCorners(report);
  const row=$('corner-metrics').querySelector('[data-metric="brake_start_m"]');
  assert.equal(row.cells[2].textContent,'210.0');assert.equal(row.cells[3].textContent,'214.0');
  assert.match(row.cells[4].textContent,/측정 불가.*입력 처리 단계가 다릅니다/);
  assert.doesNotMatch(row.cells[4].textContent,/4\.0/);assert.deepEqual(errors,[]);
});

test('코너 분석 누락·실패·비교 초기화 시 이전 표를 숨기고 HTML을 실행하지 않는다',async t=>{
  const {$,w,errors}=await setupLive(t),report=cornerFixture();
  report.corners[0].label='<img src=x onerror="window.injected=true">';
  report.corners[0].reference.metric_support.brake_release_m.reason='<b>결측</b>';
  w.renderCorners(report);assert.equal($('corners').querySelector('img'),null);
  assert.equal($('corner-metrics').querySelector('b'),null);assert.equal(w.injected,undefined);
  w.renderCorners(undefined);assert.equal($('corner-results').hidden,true);
  assert.equal($('corners').rows.length,0);assert.match($('corner-summary').textContent,/분석이 없습니다/);
  w.renderCorners({status:'UNAVAILABLE',reason:'REFERENCE_GEOMETRY_UNAVAILABLE'});
  assert.match($('corner-summary').textContent,/기준 랩 좌표 부족/);
  w.renderCorners(cornerFixture());w.clearComparison();
  assert.equal($('corner-results').hidden,true);assert.equal($('corner-metrics').rows.length,0);
  assert.equal($('corner-summary').textContent,'');assert.deepEqual(errors,[]);
});

test('두 랩 비교 응답의 코너 분석을 연결하고 선택 변경 시 과거 결과를 지운다',async t=>{
  const {$,w,errors}=await setup(t);let requested;
  const comparison={reference_lap:1,target_lap:2,reference_time_s:125,target_time_s:124,delta_time_s:-1,
    distance_start_m:0,distance_end_m:1000,sample_count:2,sector_length_m:1000,common_delta_time_s:-1,
    points:[{distance_m:0,delta_time_s:0},{distance_m:1000,delta_time_s:-1}],channels:{},channel_support:{},track_overlay:{paths:[]},
    sectors:[{sector_number:1,distance_start_m:0,distance_end_m:1000,reference_duration_s:22,target_duration_s:21,
      delta_time_s:-1,cumulative_delta_start_s:0,cumulative_delta_end_s:-1,channels:{}}],corner_comparison:cornerFixture()};
  w.fetch=async url=>{requested=url;return {ok:true,json:async()=>({comparison,sources:{reference:file,target:file}})};};
  $('reference').value='1';$('reference').dispatchEvent(new w.Event('change'));
  $('target').value='2';$('target').dispatchEvent(new w.Event('change'));assert.equal($('compare').disabled,false);
  $('compare').click();await waitFor(()=>!$('comparison-result').hidden);
  assert.match(requested,/\/api\/lmu\/telemetry\/compare-files\?/);
  assert.equal($('corners').rows.length,2);assert.equal($('corner-results').hidden,false);
  $('sector-length').value='250';$('sector-length').dispatchEvent(new w.Event('change'));
  assert.equal($('comparison-result').hidden,true);assert.equal($('corners').rows.length,0);
  assert.equal($('corner-focus').hidden,true);assert.deepEqual(errors,[]);
});

test('일관성은 수동 선택 3~6개에서만 요청하고 표본 부족과 관측 과제를 구분한다',async t=>{
  let submitted;const response={status:'PARTIAL',lap_count:3,corners:[
    {id:'1',label:'구간 1',start_m:200,end_m:400,sample_count:3,median_duration_s:8,min_duration_s:7.8,max_duration_s:8.3,spread_s:.5,std_duration_s:.2},
    {id:'2',label:'구간 2',start_m:600,end_m:800,sample_count:2,median_duration_s:999,spread_s:999}],
    focus:[{id:'1',label:'구간 1',observation:'시간 범위 0.5초 관측',next_check:'제동 위치와 최저 속도를 함께 비교'}]};
  const ui=await setupLive(t,(url,options)=>{
    if(url==='/api/lmu/telemetry/corner-consistency'){submitted=JSON.parse(options.body);return {consistency:response};}
  }),{$,w,errors}=ui;
  w.addReplayChoices({...file,laps:Array.from({length:7},(_,i)=>({...file.laps[0],lap_number:i+1}))});
  const inputs=[...$('replay-choices').querySelectorAll('input')],select=(n)=>inputs.forEach((input,i)=>{input.checked=i<n;input.dispatchEvent(new w.Event('change'));});
  select(2);assert.equal($('load-replay').disabled,false);assert.equal($('load-consistency').disabled,true);
  select(3);assert.equal($('load-consistency').disabled,false);$('load-consistency').click();
  await waitFor(()=>!$('consistency-result').hidden);
  assert.deepEqual(submitted.selections,[1,2,3].map(lap_number=>({file_id:file.file_id,lap_number})));
  assert.match($('consistency-summary').textContent,/3개 랩.*미확인/);
  assert.match($('consistency-summary').textContent,/일부 구간.*3회 미만/);
  assert.equal($('consistency-corners').rows[0].cells[5].textContent,'0.500');
  assert.match($('consistency-corners').rows[1].cells[2].textContent,/3개 미만/);
  assert.doesNotMatch($('consistency-corners').textContent,/999/);
  assert.match($('consistency-focus').textContent,/다음 비교 항목: 제동 위치/);
  select(6);assert.equal($('load-consistency').disabled,false);assert.equal($('consistency-result').hidden,true);
  assert.equal($('consistency-corners').rows.length,0);
  select(7);assert.equal($('load-consistency').disabled,true);assert.equal($('load-replay').disabled,true);
  w.renderConsistency({status:'UNAVAILABLE',reason:'완전한 관측 부족'});
  assert.match($('consistency-summary').textContent,/완전한 관측 부족/);
  assert.equal($('consistency-result').hidden,true);assert.deepEqual(errors,[]);
});

test('일관성은 제동·탈출 입력 위치를 먼저 표시하고 처리 단계 불일치·표본 부족을 수치로 대체하지 않는다',async t=>{
  const {$,w,errors}=await setupLive(t);
  w.renderConsistency({status:'AVAILABLE',lap_count:3,input_focus_reason:'반복 관측 근거를 확인하세요.',corners:[{
    label:'구간 1',start_m:200,end_m:400,sample_count:3,median_duration_s:8,spread_s:.2,
    input_consistency:{
      brake_start_m:{status:'AVAILABLE',label:'최초 브레이크 8% 초과 관측 위치',sample_count:3,median_m:210,min_m:208,max_m:214,spread_m:6,approximate:true},
      throttle_first_m:{status:'INPUT_STAGE_MISMATCH',label:'스로틀 20% 초과 위치',sample_count:3,median_m:999,spread_m:999,reason:'두 랩의 입력 처리 단계가 다릅니다.',observations:[{active_at_center:true},{active_at_center:true},{active_at_center:false}]},
      full_throttle_m:{status:'INSUFFICIENT_PASSES',label:'스로틀 95% 초과 위치',sample_count:2,median_m:null,reason:'입력 위치를 측정한 주행이 3회 미만입니다.'}
    }}],focus:[]});
  const rows=[...$('consistency-inputs').rows];assert.equal(rows.length,3);
  assert.deepEqual([...rows[0].cells].slice(2).map(cell=>cell.textContent),['3','210.0','208.0','214.0','6.0']);
  assert.match(rows[1].cells[3].textContent,/측정 불가.*처리 단계가 다릅니다/);
  assert.match(rows[1].cells[1].textContent,/기하 중심에서 이미 활성 2회/);
  assert.match(rows[2].cells[3].textContent,/측정 불가.*3회 미만/);
  assert.doesNotMatch($('consistency-inputs').textContent,/999/);
  assert.match($('consistency-input-reason').textContent,/반복 관측 근거/);
  assert.equal($('consistency-times').open,false);
  assert.ok($('consistency-inputs').compareDocumentPosition($('consistency-corners'))&w.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.match($('consistency-result').textContent,/최대 약 2 m.*이미 스로틀이 켜져.*재가속 시작을 보장하지 않습니다/s);
  assert.match($('consistency-result').textContent,/위치 범위가 0 m여도 재가속이 일정했다는 뜻은 아닙니다/);
  w.clearConsistency();assert.equal($('consistency-inputs').rows.length,0);
  assert.equal($('consistency-input-reason').textContent,'');assert.deepEqual(errors,[]);
});

function choosePracticeLaps(ui,numbers){
  const {$,w}=ui;for(const input of $('replay-choices').querySelectorAll('input')){
    input.checked=numbers.includes(Number(input.value.split(':')[1]));input.dispatchEvent(new w.Event('change'));
  }
}
function practiceTaskFixture(){return {task_id:'task-opaque',created_at:'2026-10-20T11:00:00Z',baseline:{
  definition:{definition_id:'fixed-geometry'},sources:[{file_name:'baseline.duckdb',file_id:file.file_id,lap_number:1}],
  consistency:{status:'AVAILABLE',lap_count:3,focus:[{label:'구간 1',observation:'제동 위치 범위 6m',next_check:'같은 구간을 다시 비교'}]}}};}

test('과제 목록은 읽기만 수행하고 일관성 분석 후 사용자 클릭으로만 과제를 저장한다',async t=>{
  const task=practiceTaskFixture();let saved;
  const ui=await setupLive(t,(url,options)=>{
    if(url==='/api/lmu/practice/tasks'&&options.method==='POST'){saved=JSON.parse(options.body);return {task};}
    if(url==='/api/lmu/practice/tasks')return {tasks:[task]};
    if(url==='/api/lmu/practice/tasks/'+task.task_id)return {task,comparisons:[{comparison_id:'stored-comparison',created_at:'2026-10-20T12:00:00Z',status:'AVAILABLE',lap_count:3,corners:[]}]};
  }),{$,w,requests,errors}=ui;
  assert.equal(requests.filter(r=>r.url.startsWith('/api/lmu/practice')).length,0);
  $('load-practice-tasks').click();await waitFor(()=>$('practice-task').options.length===2);
  assert.equal(requests.filter(r=>r.method==='POST').length,0);
  $('practice-task').value=task.task_id;$('practice-task').dispatchEvent(new w.Event('change'));
  await waitFor(()=>!$('practice-detail').hidden);assert.match($('practice-baseline').textContent,/fixed-geometry/);
  assert.match($('practice-history-summary').textContent,/재비교 1개/);
  $('practice-history').querySelector('button').click();assert.equal($('practice-comparison').hidden,false);
  assert.match($('practice-comparison-summary').textContent,/2026-10-20T12:00:00Z/);
  assert.equal(requests.filter(r=>r.method==='POST').length,0);
  w.addReplayChoices({...file,laps:Array.from({length:6},(_,i)=>({...file.laps[0],lap_number:i+1}))});
  choosePracticeLaps(ui,[1,2,3]);assert.equal($('save-practice').disabled,true);
  assert.equal($('compare-practice').disabled,true);assert.match($('practice-selection-info').textContent,/기준 랩이 포함/);
  w.renderConsistency({status:'AVAILABLE',lap_count:3,corners:[],focus:[]});assert.equal($('save-practice').disabled,true);
  w.renderConsistency({status:'AVAILABLE',lap_count:3,corners:[],focus:[{label:'구간 1',observation:'제동 위치 범위 6m',next_check:'새 기록과 비교'}]});assert.equal($('save-practice').disabled,false);
  $('save-practice').click();await waitFor(()=>$('practice-message').textContent.includes('로컬에 저장했습니다'));
  assert.deepEqual(saved.selections,[1,2,3].map(lap_number=>({file_id:file.file_id,lap_number})));
  assert.deepEqual(requests.filter(r=>r.method==='POST').map(r=>r.url),['/api/lmu/practice/tasks']);
  choosePracticeLaps(ui,[4,5,6]);assert.equal($('save-practice').disabled,true);
  assert.equal($('practice-message').textContent,'');assert.equal($('practice-detail').hidden,false);
  assert.equal($('compare-practice').disabled,false);assert.deepEqual(errors,[]);
});

test('과제 재비교는 새 선택을 전송하고 선택 변경 뒤 늦은 응답과 이전 결과를 표시하지 않는다',async t=>{
  const task=practiceTaskFixture();let release,submitted;
  const ui=await setupLive(t,(url,options)=>{
    if(url.endsWith('/compare')){submitted=JSON.parse(options.body);return new Promise(resolve=>{release=()=>resolve({comparison:{status:'AVAILABLE',reason:'입력 위치 관측 변화'}});});}
  }),{$,w,errors}=ui;
  w.addReplayChoices({...file,laps:Array.from({length:6},(_,i)=>({...file.laps[0],lap_number:i+1}))});
  w.renderPracticeTask(task);choosePracticeLaps(ui,[4,5,6]);
  $('compare-practice').click();await waitFor(()=>typeof release==='function');
  assert.equal($('compare-practice').disabled,true);
  assert.deepEqual(submitted.selections,[4,5,6].map(lap_number=>({file_id:file.file_id,lap_number})));
  choosePracticeLaps(ui,[3,4,5]);release();await waitFor(()=>!$('compare-practice').disabled);
  assert.equal($('practice-comparison').hidden,true);assert.equal($('practice-message').textContent,'');
  w.renderPracticeComparison({status:'AVAILABLE',reason:'관측 결과'});assert.equal($('practice-comparison').hidden,false);
  choosePracticeLaps(ui,[4,5,6]);assert.equal($('practice-comparison').hidden,true);
  assert.equal($('practice-comparison-values').textContent,'');
  w.fetch=async()=>({ok:false,json:async()=>({detail:'저장한 기준과 입력 처리 단계가 달라 비교하지 않았습니다.'})});
  $('compare-practice').click();await waitFor(()=>$('practice-error').textContent.includes('입력 처리 단계'));
  assert.equal($('practice-comparison').hidden,true);assert.equal($('compare-practice').disabled,false);
  assert.deepEqual(errors,[]);
});

test('저장한 입력 분포와 새 관측 범위를 표시하고 null 차이·표본 부족·HTML을 보존한다',async t=>{
  const {$,w,errors}=await setupLive(t),info=(extra={})=>({status:'AVAILABLE',sample_count:3,median_m:210,min_m:208,max_m:214,spread_m:6,observations:[{active_at_center:true}],...extra});
  const label='<img src=x onerror="window.injected=true">',before={brake_start_m:info(),throttle_first_m:info(),full_throttle_m:info()};
  w.renderPracticeTask({task_id:'opaque',baseline:{lap_count:3,definition:{definition_id:'frozen'},corners:[{label,input_consistency:before}],focus:[]}});
  assert.equal($('practice-baseline-inputs').querySelectorAll('tbody tr').length,3);
  assert.match($('practice-baseline').textContent,/기준 랩 수: 3/);
  w.renderPracticeComparison({status:'PARTIAL',lap_count:3,corners:[{label,baseline:before,
    current:{brake_start_m:info({spread_m:4}),throttle_first_m:info({spread_m:5}),full_throttle_m:info({status:'INSUFFICIENT_PASSES',sample_count:2,median_m:999,reason:'측정 가능한 랩 2회'})},
    spread_delta_m:{brake_start_m:-2,throttle_first_m:null,full_throttle_m:null},baseline_median_duration_s:8,current_median_duration_s:7.5}]});
  const rows=[...$('practice-comparison-values').querySelector('table').tBodies[0].rows];
  assert.equal(rows[0].cells[8].textContent,'-2.0');
  assert.match(rows[1].cells[8].textContent,/측정 불가/);assert.doesNotMatch(rows[1].cells[8].textContent,/-1\.0/);
  assert.match(rows[2].cells[6].textContent,/측정 불가.*2회/);
  assert.equal(rows[1].cells[9].textContent,'1회 / 1회');
  assert.doesNotMatch($('practice-comparison-values').textContent,/999/);
  assert.equal($('practice-comparison-values').querySelector('img'),null);assert.equal(w.injected,undefined);
  assert.ok(rows[0].cells[0].textContent.includes(label));
  assert.match($('practice-comparison-summary').textContent,/음수는 관측 범위 감소이며 개선 성공을 뜻하지 않습니다/);
  assert.equal($('practice-comparison-values').querySelector('details').open,false);
  assert.deepEqual(errors,[]);
});

function vehicleFixture(){return {status:'PARTIAL',lap_number:1,recorded_lap_time_s:125.56,reference_status:'VALIDITY_UNVERIFIED',
  catalog:[{name:'TyresPressure',label:'타이어 압력',group:'타이어',status:'AVAILABLE',source_unit:'kPa',unit:'kPa'},
    {name:'FrontRideHeight',label:'전방 차고',group:'차량',status:'AVAILABLE',source_unit:'m',unit:'mm'}],
  channel:{name:'TyresPressure',label:'타이어 압력',source_unit:'kPa',unit:'kPa',frequency_hz:10,wheel_order:'UNVERIFIED',
    components:[1,2,3,4].map((n)=>({id:'value'+n,label:'value'+n,status:'PARTIAL',sample_count:100,valid_samples:92,invalid_samples:8,
      minimum:0,maximum:3,mean:1.5,points:[{elapsed_s:0,value:0,segment:0},{elapsed_s:1,value:1,segment:0},
        {elapsed_s:2,value:null,segment:0},{elapsed_s:3,value:2,segment:0},{elapsed_s:4,value:3,segment:0},
        {elapsed_s:6,value:2,segment:1},{elapsed_s:7,value:3,segment:1}]}))}};}

test('차량 채널은 수동 클릭으로 읽고 네 원본 성분의 결측·구간 간 선을 연결하지 않는다',async t=>{
  const {$,w,errors}=await setup(t);let request;
  assert.equal($('vehicle-analysis').hidden,false);assert.equal($('vehicle-result').hidden,true);
  assert.equal($('vehicle-channel').options.length,1);assert.equal($('load-vehicle').disabled,true);
  w.fetch=async url=>{request=url;return {ok:true,json:async()=>({vehicle:vehicleFixture()})};};
  $('vehicle-lap').value='1';$('vehicle-lap').dispatchEvent(new w.Event('change'));assert.equal($('load-vehicle').disabled,false);
  $('load-vehicle').click();await waitFor(()=>!$('vehicle-result').hidden);
  const url=new URL(request,'http://localhost/');assert.equal(url.pathname,'/api/lmu/telemetry/vehicle-channels');
  assert.equal(url.searchParams.get('channel_name'),'TyresPressure');assert.equal(url.searchParams.get('file_id'),file.file_id);assert.equal(url.searchParams.get('lap_number'),'1');
  assert.equal($('vehicle-stats').rows.length,4);assert.equal($('vehicle-channel').options.length,2);
  assert.match($('vehicle-unit').textContent,/원본 단위: kPa \/ 표시 단위: kPa · 10 Hz/);
  assert.match($('vehicle-unit').textContent,/휠 순서는 미확인/);assert.match($('vehicle-summary').textContent,/일부 결측/);
  const row=$('vehicle-stats').rows[0];assert.deepEqual([...row.cells].slice(2).map(cell=>cell.textContent),['100','92','8','0.000','3.000','1.500']);
  assert.equal($('vehicle-chart').querySelectorAll('polyline[data-component]').length,12);
  for(const line of $('vehicle-chart').querySelectorAll('polyline[data-component]'))assert.equal(line.getAttribute('points').trim().split(' ').length,2);
  assert.match($('vehicle-chart').textContent,/랩 경과 시간\(초\)/);assert.doesNotMatch($('vehicle-chart').textContent,/앞왼쪽|우전|FL|FR/);
  $('vehicle-channel').value='FrontRideHeight';$('vehicle-channel').dispatchEvent(new w.Event('change'));
  assert.equal($('vehicle-result').hidden,true);assert.equal($('vehicle-stats').rows.length,0);assert.deepEqual(errors,[]);
});

test('차고 단위 mm·실제 0과 전체 결측 null을 구분하고 채널 HTML을 실행하지 않는다',async t=>{
  const {$,w,errors}=await setupLive(t),vehicle=vehicleFixture(),injection='<img src=x onerror="window.injected=true">';
  vehicle.status='AVAILABLE';vehicle.channel={name:'FrontRideHeight',label:injection,source_unit:'m',unit:'mm',frequency_hz:50,wheel_order:'NOT_APPLICABLE',components:[
    {id:'value',label:'value',status:'AVAILABLE',sample_count:3,valid_samples:3,invalid_samples:0,minimum:0,maximum:0,mean:0,points:[{elapsed_s:0,value:0,segment:0},{elapsed_s:1,value:0,segment:0}]}]};
  w.renderVehicle(vehicle);assert.match($('vehicle-unit').textContent,/원본 단위: m \/ 표시 단위: mm · 50 Hz/);
  assert.deepEqual([...$('vehicle-stats').rows[0].cells].slice(5).map(cell=>cell.textContent),['0.000','0.000','0.000']);
  assert.equal($('vehicle-chart').querySelectorAll('polyline').length,1);
  assert.equal($('vehicle-chart').querySelector('img'),null);assert.equal(w.injected,undefined);assert.ok($('vehicle-unit').textContent.includes(injection));
  vehicle.status='UNAVAILABLE';vehicle.reason='이 랩의 채널 표본이 모두 결측입니다.';
  Object.assign(vehicle.channel.components[0],{status:'UNAVAILABLE',reason:'값 없음',valid_samples:0,invalid_samples:3,minimum:null,maximum:null,mean:null,points:[]});
  w.renderVehicle(vehicle);assert.match($('vehicle-summary').textContent,/관측 불가.*모두 결측/);
  assert.deepEqual([...$('vehicle-stats').rows[0].cells].slice(5).map(cell=>cell.textContent),['측정 불가','측정 불가','측정 불가']);
  assert.equal($('vehicle-chart').querySelector('svg'),null);assert.match($('vehicle-chart').textContent,/결측값을 0으로 채우지 않습니다/);
  assert.deepEqual(errors,[]);
});

test('차량 채널 선택 변경·파일 변경·오류·늦은 응답은 이전 관측을 지운다',async t=>{
  const {$,w,errors}=await setup(t);let release;
  w.fetch=async()=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({vehicle:vehicleFixture()})});});
  $('vehicle-lap').value='1';$('vehicle-lap').dispatchEvent(new w.Event('change'));$('load-vehicle').click();await waitFor(()=>typeof release==='function');
  $('vehicle-lap').value='2';$('vehicle-lap').dispatchEvent(new w.Event('change'));release();await waitFor(()=>!$('load-vehicle').disabled);
  assert.equal($('vehicle-result').hidden,true);assert.equal($('vehicle-summary').textContent,'');
  w.renderVehicle(vehicleFixture());w.fetch=async()=>({ok:false,json:async()=>({detail:'이 채널을 읽을 수 없습니다.'})});
  $('load-vehicle').click();await waitFor(()=>$('vehicle-error').textContent.includes('읽을 수 없습니다'));
  assert.equal($('vehicle-result').hidden,true);assert.equal($('vehicle-stats').rows.length,0);assert.match($('vehicle-summary').textContent,/이전 관측 결과는 숨겼습니다/);
  w.renderVehicle(vehicleFixture());$('files').dispatchEvent(new w.Event('change'));
  assert.equal($('vehicle-analysis').hidden,true);assert.equal($('vehicle-chart').textContent,'');assert.equal($('vehicle-channel').value,'TyresPressure');assert.equal($('load-vehicle').disabled,true);
  assert.deepEqual(errors,[]);
});

function vehicleComparisonFixture(){
  const components=[1,2,3,4].map(n=>({id:'value'+n,label:'value'+n,status:'PARTIAL',start_m:100,end_m:500,
    requested_distance_m:400,supported_distance_m:200,support_ranges:[{start_m:100,end_m:200},{start_m:400,end_m:500}],
    reference:{minimum:0,maximum:0,mean:0},target:{minimum:2,maximum:2,mean:2},delta:{minimum:2,maximum:2,mean:2},
    points:[{distance_m:100,reference:0,target:2,delta:2,segment:0},{distance_m:200,reference:0,target:2,delta:2,segment:0},
      {distance_m:400,reference:0,target:2,delta:2,segment:1},{distance_m:500,reference:0,target:2,delta:2,segment:1}]}));
  return {status:'PARTIAL',reference_status:'VALIDITY_UNVERIFIED',reference:{lap_number:1,recorded_lap_time_s:125,source_unit:'m',frequency_hz:50},target:{lap_number:2,recorded_lap_time_s:124,source_unit:'mm',frequency_hz:100},
    channel:{name:'RideHeights',label:'차고 기록',unit:'mm',wheel_order:'UNVERIFIED',components},
    corner_comparison:{status:'AVAILABLE',definition:{reference_lap:1},corners:[{id:'geometry-1',label:'구간 1',start_m:100,apex_m:300,end_m:500,components:components.map(({points,...rest})=>rest)}]}};
}

test('차량 두 랩 비교는 기존 선택을 전송하고 공통 거리 평균·차이·성분별 지원 비율을 표시한다',async t=>{
  const {$,w,errors}=await setup(t);let request;
  $('reference').value='1';$('reference').dispatchEvent(new w.Event('change'));$('target').value='2';$('target').dispatchEvent(new w.Event('change'));
  $('vehicle-channel').add(new w.Option('차고 기록','RideHeights'));$('vehicle-channel').value='RideHeights';$('vehicle-channel').dispatchEvent(new w.Event('change'));
  assert.equal($('compare-vehicle').disabled,false);assert.equal($('vehicle-comparison-result').hidden,true);
  w.fetch=async url=>{request=url;return {ok:true,json:async()=>({comparison:vehicleComparisonFixture()})};};
  $('compare-vehicle').click();await waitFor(()=>!$('vehicle-comparison-result').hidden);
  const url=new URL(request,'http://localhost/');assert.equal(url.pathname,'/api/lmu/telemetry/vehicle-comparison');
  for(const [key,value] of Object.entries({reference_file_id:file.file_id,target_file_id:file.file_id,reference_lap_number:'1',target_lap_number:'2',channel_name:'RideHeights',points:'301'}))assert.equal(url.searchParams.get(key),value);
  const rows=[...$('vehicle-comparison-stats').rows];assert.equal(rows.length,4);
  assert.deepEqual([...rows[0].cells].slice(1).map(c=>c.textContent),['0.000','2.000','+2.000','0.000 → 0.000','2.000 → 2.000','200.0 / 400.0','50.0 %']);
  assert.equal($('vehicle-corner-stats').rows.length,4);assert.equal($('vehicle-corner-stats').rows[0].cells[4].textContent,'+2.000');
  assert.match($('vehicle-comparison-summary').textContent,/원본 단위 m · 50 Hz/);assert.match($('vehicle-comparison-summary').textContent,/원본 단위 mm · 100 Hz/);
  assert.match($('vehicle-comparison-summary').textContent,/표시 단위 mm/);assert.match($('vehicle-comparison-summary').textContent,/휠 순서는 미확인/);
  assert.match($('vehicle-corner-summary').textContent,/기준 랩 1의 고정 기하 경계/);
  const lines=$('vehicle-comparison-charts').querySelectorAll('polyline[data-component]');assert.equal(lines.length,16);
  for(const line of lines)assert.equal(line.getAttribute('points').trim().split(' ').length,2);
  assert.equal($('vehicle-comparison-charts').querySelectorAll('[data-role="reference"]').length,8);
  assert.equal($('vehicle-comparison-charts').querySelectorAll('[data-role="target"]').length,8);assert.deepEqual(errors,[]);
});

test('차량 비교의 null 차이·지원 0·코너 기하 부재를 구분하고 HTML을 실행하지 않는다',async t=>{
  const {$,w,errors}=await setupLive(t),report=vehicleComparisonFixture(),injection='<img src=x onerror="window.injected=true">';
  report.channel.label=injection;report.channel.components[0].label=injection;
  report.channel.components[0].delta.mean=null;
  Object.assign(report.channel.components[1],{status:'UNAVAILABLE',supported_distance_m:0,reference:{minimum:null,maximum:null,mean:null},target:{minimum:null,maximum:null,mean:null},delta:{minimum:null,maximum:null,mean:null},points:[]});
  report.corner_comparison={status:'UNAVAILABLE',reason:'기준 좌표가 없어 코너 후보를 확인하지 못했습니다.'};
  w.renderVehicleComparison(report);const rows=$('vehicle-comparison-stats').rows;
  assert.equal(rows[0].cells[1].textContent,'0.000');assert.equal(rows[0].cells[3].textContent,'측정 불가');
  assert.equal(rows[1].cells[1].textContent,'측정 불가');assert.equal(rows[1].cells[6].textContent,'0.0 / 400.0');assert.equal(rows[1].cells[7].textContent,'0.0 %');
  assert.equal($('vehicle-comparison-result').hidden,false);assert.equal($('vehicle-corner-stats').rows.length,0);assert.match($('vehicle-corner-summary').textContent,/기준 좌표가 없어/);
  assert.equal($('vehicle-comparison-result').querySelector('img'),null);assert.equal(w.injected,undefined);assert.ok(rows[0].cells[0].textContent.includes(injection));assert.deepEqual(errors,[]);
});

test('차량 두 랩 비교는 선택 변경 뒤 늦은 응답·채널 변경·실패의 과거 결과를 숨긴다',async t=>{
  const {$,w,errors}=await setup(t);let release;
  $('reference').value='1';$('reference').dispatchEvent(new w.Event('change'));$('target').value='2';$('target').dispatchEvent(new w.Event('change'));
  w.fetch=async()=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({comparison:vehicleComparisonFixture()})});});
  $('compare-vehicle').click();await waitFor(()=>typeof release==='function');$('target').value='1';$('target').dispatchEvent(new w.Event('change'));release();
  await waitFor(()=>!$('target').disabled);assert.equal($('vehicle-comparison-result').hidden,true);assert.equal($('vehicle-comparison-summary').textContent,'');
  $('target').value='2';$('target').dispatchEvent(new w.Event('change'));w.renderVehicleComparison(vehicleComparisonFixture());
  $('vehicle-channel').dispatchEvent(new w.Event('change'));assert.equal($('vehicle-comparison-result').hidden,true);assert.equal($('vehicle-corner-stats').rows.length,0);
  w.renderVehicleComparison(vehicleComparisonFixture());w.fetch=async()=>({ok:false,json:async()=>({detail:'차량 채널 단위가 지원되지 않습니다.'})});
  $('compare-vehicle').click();await waitFor(()=>$('vehicle-comparison-error').textContent.includes('단위가 지원되지'));
  assert.equal($('vehicle-comparison-result').hidden,true);assert.equal($('vehicle-comparison-stats').rows.length,0);assert.equal($('vehicle-comparison-charts').textContent,'');assert.equal($('compare-vehicle').disabled,false);
  assert.match($('vehicle-comparison-summary').textContent,/이전 결과는 숨겼습니다/);assert.deepEqual(errors,[]);
});

test('실시간 수집은 버튼으로만 시작하며 중지·종료 대기 상태를 구분한다',async t=>{
  const observing={state:'OBSERVING',collecting:true,snapshot:{speed_mps:50,lap_invalidated:false},
    recording_id:'opaque',frames_recorded:3,lap_observations:{completed:[{total_laps_at_end:2,last_lap_time:121.1,diagnostic_verdict:'CONSISTENT_VALID_SIGNALS',valid:null}]}};
  const ui=await setupLive(t,url=>url.endsWith('/start')?observing:
    url.endsWith('/stop')?{state:'STOPPING',collecting:false}:undefined);
  const {$,w,requests}=ui;
  assert.equal(requests.filter(r=>r.method==='POST').length,0);
  $('live-start').click();await waitFor(()=>!$('live-stop').disabled);
  assert.equal($('live-start').disabled,true);assert.equal($('live-stop').disabled,false);
  assert.match($('live-values').textContent,/180.0 km\/h/);
  assert.match($('live-laps').textContent,/정상 신호 일치\(검증 전\).*미확인/);
  $('live-stop').click();await waitFor(()=>$('live-message').textContent.includes('종료 대기')&&!$('live-refresh').disabled);
  assert.equal($('live-start').disabled,true);assert.equal($('live-stop').disabled,true);
  w.dispatchEvent(new w.Event('pagehide'));
  assert.deepEqual(requests.filter(r=>r.method==='POST').map(r=>r.url),['/api/lmu/live/start','/api/lmu/live/stop']);
  w.renderLive({state:'GAME_EXITED',collecting:false});assert.match($('live-message').textContent,/게임 종료/);
  assert.equal($('live-start').disabled,false);assert.deepEqual(ui.errors,[]);
});

test('시간 집계 제한과 랩 무효 신호를 구분하고 유효성은 미확인으로 남긴다',async t=>{
  const ui=await setupLive(t),{$,w}=ui;
  w.renderLive({state:'STOPPED',collecting:false,lap_observations:{completed:[
    {last_lap_time:125.79815673828125,diagnostic_verdict:'COUNT_POLICY_SIGNAL_OBSERVED',valid:null},
    {last_lap_time:126,diagnostic_verdict:'INVALID_SIGNAL_OBSERVED',valid:null}
  ]}});
  const rows=[...$('live-laps').rows];
  assert.match(rows[0].cells[2].textContent,/랩 무효 신호 관측/);
  assert.match(rows[1].cells[2].textContent,/시간 집계 제한 신호/);
  assert.doesNotMatch(rows[1].cells[2].textContent,/무효/);
  assert.equal(rows[1].cells[1].textContent,'125.798');
  assert.ok(rows.every(row=>row.cells[3].textContent==='미확인'));
  assert.deepEqual(ui.errors,[]);
});

test('실시간 상태 확인 실패 시 과거 속도를 숨기고 파일 새로고침의 늦은 응답을 무시한다',async t=>{
  let rejectStatus=false,releaseOverview;
  const ui=await setupLive(t,async url=>{
    if(url==='/api/lmu/live/status'&&rejectStatus)throw Error('연결 실패');
    if(url==='/api/lmu/live/start')return {state:'OBSERVING',collecting:true,snapshot:{speed_mps:40},timing:{last_read_duration_s:12.345}};
    if(url==='/api/lmu/status'&&releaseOverview!==undefined)return new Promise(resolve=>{releaseOverview=()=>resolve({dependency:'READY',live:{state:'STOPPED'},file_count:0,telemetry_dirs:[]});});
  });
  const {$}=ui;
  releaseOverview=null;$('refresh').click();await waitFor(()=>typeof releaseOverview==='function');
  $('live-start').click();await waitFor(()=>!$('live-stop').disabled);
  releaseOverview();await waitFor(()=>!$('refresh').disabled);
  assert.match($('live-values').textContent,/144.0 km\/h/);
  assert.match($('live-timing').textContent,/12.345초/);
  assert.equal($('live-start').disabled,true);
  rejectStatus=true;$('live-refresh').click();await waitFor(()=>$('live-error').textContent==='연결 실패');
  assert.doesNotMatch($('live-values').textContent,/144.0/);
  assert.match($('live-values').textContent,/숨겼습니다/);
  assert.doesNotMatch($('live-timing').textContent,/12.345/);
  assert.match($('live-timing').textContent,/지연 진단 값을 숨겼습니다/);
  rejectStatus=false;$('live-refresh').click();await waitFor(()=>$('live-error').textContent==='');
  await waitFor(()=>$('live-timing').textContent.includes('이 수집 버전에는'));
  assert.deepEqual(ui.errors,[]);
});

test('관측 중단과 완료 시간 부재를 구분하고 경계 시간을 공식 기록으로 표시하지 않는다',async t=>{
  const ui=await setupLive(t),{$,w}=ui;
  w.renderLive({state:'STOPPED',collecting:false,lap_observations:{completed:[
    {total_laps_at_end:2,last_lap_time:-1,lap_start_et:145,lap_end_et:267.053,
      event_type:'LAP_OBSERVATION_INTERRUPTED',diagnostic_verdict:'UNKNOWN',
      reasons:['STALE_TELEMETRY','LAST_LAP_TIME_MISSING'],
      evidence:{count_lap_flags:[1,2],track_limits_steps_min:7,track_limits_steps_max:12,
        lap_invalidated_observed:false,scoring_confirmation:true}},
    {last_lap_time:0,reasons:['LAST_LAP_TIME_MISSING']},
    {last_lap_time:0,reasons:[]}
  ]}});
  const rows=[...$('live-laps').rows],interrupted=rows[2];
  assert.equal(interrupted.cells.length,5);
  assert.equal(interrupted.cells[1].textContent,'기록 시간 없음');
  assert.equal(interrupted.cells[2].textContent,'관측 불완전');
  assert.equal(interrupted.cells[3].textContent,'미확인');
  assert.equal(interrupted.cells[4].querySelector('summary').textContent,'관측 중단 사유');
  const evidence=interrupted.cells[4].textContent;
  assert.match(evidence,/새 주행 신호 수신 정체/);
  assert.match(evidence,/완주 후 기록 시간이 제공되지 않음/);
  assert.match(evidence,/경계 간 시간: 122.053초 \(공식 기록 아님\)/);
  assert.match(evidence,/1\(랩만 집계\), 2\(랩·시간 집계\)/);
  assert.match(evidence,/트랙 리밋 누적 신호: 7 → 12 \(경고 횟수 아님\)/);
  assert.match(evidence,/랩 무효 신호 감지: 관측 안 됨/);
  assert.match(evidence,/종료 판정 신호: 관측/);
  assert.equal(rows[1].cells[1].textContent,'기록 시간 없음');
  assert.equal(rows[0].cells[1].textContent,'—');
  assert.deepEqual(ui.errors,[]);
});

test('새 관측 사유는 원문으로 남기고 근거의 HTML 문자열을 실행하지 않는다',async t=>{
  const ui=await setupLive(t),{$,w}=ui,unknown='<img src=x onerror="window.injected=true">';
  w.renderLive({state:'STOPPED',collecting:false,lap_observations:{completed:[{
    last_lap_time:Infinity,lap_start_et:0,lap_end_et:Infinity,valid:true,
    diagnostic_verdict:'FUTURE_VERDICT',reasons:['FUTURE_REASON',unknown],
    evidence:{count_lap_flags:[0,3],track_limits_steps_min:0,track_limits_steps_max:0,
      lap_invalidated_observed:true,scoring_confirmation:false}
  }]}});
  const row=$('live-laps').rows[0],evidence=row.cells[4];
  assert.equal(row.cells[1].textContent,'—');
  assert.equal(row.cells[2].textContent,'관측 불완전');
  assert.equal(row.cells[3].textContent,'미확인');
  assert.equal(evidence.querySelector('summary').textContent,'관측 근거 보기');
  assert.match(evidence.textContent,/FUTURE_REASON/);
  assert.ok(evidence.textContent.includes(unknown));
  assert.equal(evidence.querySelector('img'),null);assert.equal(w.injected,undefined);
  assert.doesNotMatch(evidence.textContent,/경계 간 시간/);
  assert.match(evidence.textContent,/0\(랩·시간 집계 안 함\), 3/);
  assert.match(evidence.textContent,/0 → 0/);
  assert.match(evidence.textContent,/랩 무효 신호 감지: 감지/);
  assert.match(evidence.textContent,/종료 판정 신호: 미확인/);
  assert.deepEqual(ui.errors,[]);
});

test('수신 지연과 게임 시간 간격 및 정체 감지 시점을 따로 표시하고 종료 값을 구분한다',async t=>{
  const ui=await setupLive(t),{$,w}=ui;
  assert.equal($('live-timing').textContent,'이 수집 버전에는 지연 진단 정보가 없습니다.');
  const timing={recording_elapsed_s:30,telemetry_age_s:0,last_telemetry_receive_gap_s:1.5,
    max_telemetry_receive_gap_s:2,last_read_duration_s:1.49,read_in_progress_s:null,
    last_game_time_gap_s:.01,stale_count:1};
  const data={state:'OBSERVING',collecting:true,timing,
    last_stale:{recording_elapsed_s:29,telemetry_age_s:1.5}};
  w.renderLive(data);
  let text=$('live-timing').textContent;
  assert.match(text,/현재 수집 상태입니다/);
  assert.match(text,/마지막 주행 신호 이후: 0.000초/);
  assert.match(text,/직전 주행 신호 수신 간격: 1.500초/);
  assert.match(text,/가장 긴 주행 신호 수신 간격: 2.000초/);
  assert.match(text,/직전 데이터 읽기 대기: 1.490초/);
  assert.match(text,/현재 데이터 읽기 대기: 미확인/);
  assert.match(text,/직전 게임 시간 간격: 0.010초/);
  assert.match(text,/수신 정체 감지: 1회/);
  assert.match(text,/마지막 정체 감지 시점: 수집 시작 후 29.000초/);
  assert.match(text,/정체 감지 당시 주행 신호 경과: 1.500초/);
  w.renderLive({...data,state:'STOPPING',collecting:false});
  assert.match($('live-timing').textContent,/종료 처리 중인 수집 상태입니다/);
  w.renderLive({...data,state:'STOPPED',collecting:false});
  assert.match($('live-timing').textContent,/수집 종료 시점의 값입니다/);
  w.renderLive({state:'STOPPED',collecting:false,journal_state:'NOT_STARTED',timing:{}});
  text=$('live-timing').textContent;
  assert.match(text,/수집 전 상태입니다/);
  assert.doesNotMatch(text,/마지막 정체 감지 시점/);
  assert.deepEqual(ui.errors,[]);
});
