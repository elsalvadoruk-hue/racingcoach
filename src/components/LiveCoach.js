import { Card, CardContent } from './ui/card';
import { Badge } from './ui/badge';
import { ToggleGroup, ToggleGroupItem } from './ui/toggle-group';
import { LiveHeader, LiveMetric } from './LivePrimitives';
import { Button } from './ui/button';
import { NativeSelect } from './ui/native-select';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Radio, Route, Square, Volume2, VolumeX } from 'lucide-react';
import { HelpHint } from './HelpGuide';
import { encodeWav, VoiceRecorder, RESUMABLE, MAX_RESUMES, DEFAULT_RATE, RATE_MIN, RATE_MAX } from '../voiceAudio';
import useAudioOutput from '../useAudioOutput';
import { EFFECT_LEVELS, POP_MODES } from '../radioEffect';
import PitWall, { ENGINEERS, PIT_WALL_KEY, readPitPreferences } from './PitWall';
import useSpotter from '../useSpotter';
import SpotterPanel from './SpotterPanel';
import useRadioDiagnostics from '../useRadioDiagnostics';
import RadioDiagnostics from './RadioDiagnostics';
import RadioAudioPrepare from './RadioAudioPrepare';

const FEELINGS = { UNDERSTEER: '앞이 밀림', OVERSTEER: '뒤가 불안', BRAKING: '제동 불안', KERB: '연석 불안', DIFFICULT: '어려움', IMPROVED: '편해짐' };
const phaseText = (note) => {
  const snapshot = note.context?.snapshot;
  return snapshot?.lap_number != null ? `랩 ${snapshot.lap_number} · 위치 ${Math.round((snapshot.distance_pct || 0) * 100)}%` : '위치 미확인 · 주행 후 직접 연결';
};
const errorMessage = (error) => error.name === 'NotAllowedError' ? '마이크 권한이 필요합니다. 브라우저 주소창의 마이크 권한을 확인하세요.' : error.message;
const SPOTTER_ROLE = { label: '스포터', style: 'border-success/30 bg-success/10 text-success' };

function NoteText({ note }) {
  const engineer = note.context?.engineer === 'spotter' ? SPOTTER_ROLE : ENGINEERS[note.context?.engineer || 'coach'];
  return <><div className="coach-note-meta"><Badge variant="secondary">{engineer?.label || '엔지니어'}{note.source === 'AUTO_REPORT' ? note.context?.report_delivery === 'PREPARED' ? ' · 전달 대기' : ' · 자동 보고' : ''}</Badge><time className="text-xs text-muted-foreground">{new Date(note.created_at * 1000).toLocaleTimeString('ko-KR')}</time></div>
    <p className="mt-2 text-xs text-muted-foreground">{phaseText(note)}</p>
    {note.source !== 'AUTO_REPORT' && <p className="mt-3 text-sm font-medium">“{note.text}”</p>}
    {!!note.feelings?.length && <div className="mt-2 flex flex-wrap gap-2">{note.feelings.map(feeling => <Badge key={feeling} variant="outline">{FEELINGS[feeling] || feeling}</Badge>)}</div>}
    {note.reply && <div className="coach-note-reply"><p className="mb-1 text-xs text-muted-foreground">{note.reply_source === 'VERTEX_AI' ? 'AI 조언' : note.reply_source === 'MEASURED' ? '측정값 답변' : '안내'}</p><p>{note.reply}</p></div>}
    {note.source === 'AUTO_REPORT' && note.context?.spoken_text && <p className="mt-3 text-xs text-muted-foreground">자동 음성 문구: {note.context.spoken_text} · 정확한 수치는 위 기록 참조</p>}</>;
}

const cornerLine = (corner) => `${corner.number ?? '--'}번 구간 ${corner.delta_s > 0 ? '+' : ''}${corner.delta_s.toFixed(2)}초${corner.findings?.length ? ' · ' + corner.findings.join(' · ') : ''}`;

const CONTROL_LABELS = { brake_bias: '브레이크 바이어스', tc: 'TC', abs: 'ABS' };

export function CornerCoach({ coach, connected, controls }) {
  const lap = coach?.last_lap, last = coach?.last_corner;
  return <Card asChild><section aria-label="실시간 코너 비교" className="coach-panel">
    <LiveHeader title="실시간 코너 비교" icon={Route} description="각 구간을 이번 실행에서 깨끗하게 통과한 내 최고 기록과 비교합니다. AI를 사용하지 않습니다." />
    <CardContent className="p-0">
      {!connected ? <p className="text-sm text-muted-foreground">차량 연결 대기</p>
        : !coach?.available ? <p className="text-sm text-muted-foreground">{coach?.reason || '구간 정의를 확인하고 있습니다.'}</p>
        : <>
          <div className="grid gap-3 sm:grid-cols-3">
            <LiveMetric label="비교 기준이 있는 구간" value={`${coach.reference_corners} / ${coach.corner_count}`} note="깨끗한 통과가 한 번 있어야 비교합니다." />
            <LiveMetric label="최근 완료 랩" value={lap ? `${lap.lap}랩 · ${lap.compared}개 구간 비교` : '비교 대기'} note={lap ? lap.losses.length ? `0.1초 이상 손실 합계 ${lap.total_loss_s.toFixed(2)}초` : '모든 구간이 최고와 0.1초 이내' : '다음 랩부터 비교합니다.'} />
            <LiveMetric label="방금 지난 구간" value={last ? last.delta_s == null ? `${last.number}번 구간 · 첫 기록` : cornerLine(last).split(' · ')[0] : '--'} note={last?.findings?.length ? last.findings.join(' · ') : '옆에 차량이 있었거나 옐로였던 통과는 제외합니다.'} />
          </div>
          {!!lap?.losses.length && <ul className="mt-4 flex flex-col gap-2 text-sm text-foreground">{lap.losses.map((corner) => <li key={corner.id} className="rounded-lg border border-border p-3">{cornerLine(corner)}</li>)}</ul>}
          {lap?.best_gain && <p className="mt-3 text-sm text-success">가장 줄인 구간: {cornerLine(lap.best_gain)}</p>}
          {coach.focus && <p role="status" className="mt-3 rounded-lg border border-primary/30 bg-primary/10 p-3 text-sm text-primary">다음 통과 전 안내 예정: {coach.focus.text}</p>}
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">구간 번호는 코너 비교 화면의 자동 구간 번호입니다. 음성은 스포터와 “일반 보고 포함”이 켜져 있을 때, 손실이 가장 컸던 구간 하나를 제동 지점 몇 초 전에 한 번만 알립니다. “방금 랩 어때?”, “이전 코너 뭐 잘못했어?”로 물어볼 수도 있습니다.</p>
        </>}
      {connected && <div className="mt-5 border-t border-border pt-4">
        <h3 className="text-sm font-semibold text-foreground">ABS · TC · 브레이크 바이어스</h3>
        {controls?.ready ? <ul className="mt-2 flex flex-col gap-2 text-sm text-foreground">{controls.advice.map((item) => <li key={item.control} className={`rounded-lg border p-3 ${item.call ? 'border-warning/40 bg-warning/10' : 'border-border'}`}><span className="font-semibold">{CONTROL_LABELS[item.control]}</span> · {item.text}</li>)}{!controls.advice.length && <li className="text-muted-foreground">이 차량에서 설정 값을 읽지 못했습니다.</li>}</ul>
          : <p className="mt-2 text-sm text-muted-foreground">같은 설정으로 옆 차량 없이 깨끗한 랩 3개를 달리면 추천합니다. 설정을 바꾸면 다시 세 랩을 봅니다.</p>}
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">제동·가속 중 조향을 반대로 준 횟수와 ABS 개입 시간으로 판단한 시험 방향입니다. 효과를 보장하지 않으며 한 번에 한 가지만 바꾸세요. “TC 어떻게 해?”처럼 물어볼 수도 있습니다.</p>
      </div>}
    </CardContent></section></Card>;
}

export default function LiveCoach({ apiBase, visible, onSaved }) {
  const [status, setStatus] = useState(null);
  const [notes, setNotes] = useState([]);
  const [armed, setArmed] = useState(false);
  const [phase, setPhase] = useState('idle');
  const [mode, setMode] = useState('measured');
  const [speak, setSpeak] = useState(true);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [socketReady, setSocketReady] = useState(false);
  const [lastAudio, setLastAudio] = useState(null);
  const lastMime = useRef('audio/mpeg');
  const [wallEnabled, setWallEnabled] = useState(false);
  const [wallPreferences, setWallPreferences] = useState(readPitPreferences);
  const [wallView, setWallView] = useState(null);
  const audioOutput = useAudioOutput();
  const recordDelivery = useRadioDiagnostics(apiBase);
  const deliveryEvent = useCallback((note, spokenText) => ({ event_id: String(note.id), run_id: note.context?.snapshot?.run_id || '',
    key: note.context?.report_key || 'answer', engineer: note.context?.engineer || 'coach', text: spokenText || note.reply || '',
    session_time: note.context?.snapshot?.session_time, lap_number: note.context?.snapshot?.lap_number,
    distance_pct: note.context?.snapshot?.distance_pct }), []);
  const { outputId, devices: outputDevices, supported: outputSupported, message: outputMessage, refresh: refreshOutputs, changeOutput, play, volume, muted, changeVolume, toggleMute, rate, changeRate, effect, changeEffect, isPlaying, isPriorityBlocked } = audioOutput;
  const stopSpeaking = audioOutput.stopNormal || audioOutput.stop;
  const blocked = useRef(null), busyAudio = useRef(null), reportResumes = useRef(new Map());
  blocked.current = audioOutput.isPriorityBlocked; busyAudio.current = audioOutput.audioBusy;
  const recorder = useRef(null), state = useRef({ phase: 'idle', armed: false });
  const spotter = useSpotter(apiBase, audioOutput, { busy: phase !== 'idle', specialists: wallEnabled && wallPreferences.level !== 'off', level: wallPreferences.level, recordDelivery });
  useEffect(() => { if (spotter.enabled) setWallEnabled(true); }, [spotter.enabled]);
  const turn = useRef(null), stopRequested = useRef(false), timeout = useRef(null);
  const mounted = useRef(true), options = useRef({ mode, speak }), saved = useRef(onSaved);
  options.current = { mode, speak }; saved.current = onSaved;
  const reportEpoch = useRef(0), wallOptions = useRef(null), playing = useRef(false), reportPoll = useRef(null);
  wallOptions.current = { ...wallPreferences, level: wallEnabled ? wallPreferences.level : 'off', speak: speak && !muted && volume > 0, basic_spotter: spotter.enabled };
  playing.current = isPlaying;
  const driving = useRef(null);
  driving.current = spotter.enabled && spotter.view?.radio?.connected ? spotter.view.radio : status?.snapshot;
  const drivingConnected = useRef(true);
  drivingConnected.current = !!(spotter.enabled && spotter.view?.radio?.connected) || status?.connected !== false;
  const request = useCallback(async (path, body) => {
    const response = await fetch(`${apiBase}/api/live-coach${path}`, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '음성 코치 요청을 완료하지 못했습니다.');
    return data;
  }, [apiBase]);
  const changePhase = (value) => { state.current.phase = value; if (mounted.current) setPhase(value); };
  const testOutput = () => {
    const rate = 16000, samples = new Float32Array(rate * .6);
    for (let i = 0; i < samples.length; i += 1) {
      const fade = Math.min(1, i / 400, (samples.length - i - 1) / 400);
      samples[i] = .15 * fade * Math.sin(2 * Math.PI * 440 * i / rate);
    }
    stopSpeaking();   // the driver's own button replaces an answer that is still playing
    play(encodeWav([samples], rate), 'audio/wav');
  };
  // Same value as ANSWER_PRIORITY in useAudioOutput (kept here because tests replace that module).
  const ANSWER = 4;
  const answerTurn = useRef(0);
  const speakAnswer = useCallback((audio, note, attempt = 0) => {
    const ticket = ++answerTurn.current;
    return play(audio, 'audio/mpeg', { priority: ANSWER, onStatus: (status, reason) => {
      recordDelivery(deliveryEvent(note), status, reason);
      // A side-by-side call cut the answer off: say it again from the start once the radio is free.
      if (status !== 'interrupted' || !['preempted', 'nearby_car'].includes(reason) || attempt >= MAX_RESUMES) return;
      const since = Date.now();
      const timer = setInterval(() => {
        if (!mounted.current || ticket !== answerTurn.current || state.current.phase !== 'idle' || Date.now() - since > 20000) { clearInterval(timer); return; }
        if (blocked.current?.() || busyAudio.current?.()) return;
        clearInterval(timer); speakAnswer(audio, note, attempt + 1);
      }, 300);
    } });
  }, [play, recordDelivery, deliveryEvent]);
  const submit = useCallback(async (body) => {
    changePhase('sending');
    try {
      const response = await request('/utterances', { ...body, reply: options.current.mode !== 'memo',
        analysis_mode: options.current.mode === 'ai' ? 'ai' : 'measured', speak: options.current.speak });
      if (!mounted.current) return;
      setNotes((current) => [response.note, ...current.filter((note) => note.id !== response.note.id)].slice(0, 200));
      // An answer made of prepared sentences arrives as several clips and plays as one call.
      const answerAudio = response.audio_parts?.length > 1 ? response.audio_parts : response.audio_base64;
      setError(response.warning || ''); lastMime.current = 'audio/mpeg'; setLastAudio(answerAudio || null);
      saved.current?.(response.note);
      if (answerAudio && options.current.speak) await speakAnswer(answerAudio, response.note);
    } catch (failure) { if (mounted.current) setError(errorMessage(failure)); }
    finally { turn.current = null; changePhase('idle'); }
  }, [request, speakAnswer]);
  const finish = useCallback(async () => {
    if (state.current.phase === 'starting') { stopRequested.current = true; return; }
    if (state.current.phase !== 'recording') return;
    clearTimeout(timeout.current);
    try {
      const encoded = recorder.current.finish();
      await submit({ capture_id: turn.current?.capture_id, audio_base64: encoded });
    } catch (failure) { setError(errorMessage(failure)); changePhase('idle'); }
  }, [submit]);
  const start = useCallback(async (serial) => {
    if (!state.current.armed || state.current.phase !== 'idle') return;
    reportEpoch.current += 1;
    stopSpeaking(); setError(''); stopRequested.current = false; changePhase('starting');
    try {
      // Freeze the backend's position at the F8 edge, independent of network/STT delay.
      const [capture] = await Promise.all([
        request(`/captures${serial ? `?hotkey_serial=${serial}` : ''}`, {}),
        recorder.current.start(),
      ]);
      turn.current = capture;
      if (!mounted.current || !state.current.armed) { recorder.current?.finish(); changePhase('idle'); return; }
      changePhase('recording');
      timeout.current = setTimeout(finish, 44000);
      if (stopRequested.current) await finish();
    } catch (failure) { recorder.current && (recorder.current.recording = false); setError(errorMessage(failure)); changePhase('idle'); }
  }, [request, finish, stopSpeaking]);
  const startRef = useRef(start), finishRef = useRef(finish);
  startRef.current = start; finishRef.current = finish;

  useEffect(() => {
    mounted.current = true;
    const currentState = state.current;
    return () => { mounted.current = false; currentState.armed = false; clearTimeout(timeout.current); recorder.current?.close(); stopSpeaking(); };
  }, [stopSpeaking]);
  useEffect(() => { if (visible || armed) refreshOutputs(); }, [visible, armed, refreshOutputs]);
  useEffect(() => {
    if (!visible && !armed && !wallEnabled && !spotter.enabled) return undefined;
    let stopped = false, running = false;
    const load = async () => {
      if (running) return;
      running = true;
      try { const data = await request('/status'); if (!stopped) setStatus(data); }
      catch (failure) { if (!stopped) setError(errorMessage(failure)); }
      finally { running = false; }
    };
    load(); const id = setInterval(load, 1200);
    if (visible) request('/notes').then((data) => { if (!stopped) setNotes(data.notes || []); }).catch(() => {});
    return () => { stopped = true; clearInterval(id); };
  }, [visible, armed, wallEnabled, spotter.enabled, request]);
  useEffect(() => {
    if (!visible && !wallEnabled) return undefined;
    let stopped = false, running = false, lastPoll = 0;
    const poll = async () => {
      if (running || Date.now() - lastPoll < 1800 || state.current.phase !== 'idle' || playing.current || isPriorityBlocked?.()) return;
      running = true;
      lastPoll = Date.now();
      const epoch = reportEpoch.current;
      try {
        const response = await request('/reports', wallOptions.current);
        if (stopped || !mounted.current || epoch !== reportEpoch.current) return;
        setWallView(response.pit_wall);
        if (!response.report || state.current.phase !== 'idle' || playing.current || isPriorityBlocked?.()) return;
        const { note, warning } = response.report;
        const audio = response.report.audio_parts?.length > 1 ? response.report.audio_parts : response.report.audio_base64;
        setNotes((current) => [note, ...current.filter((n) => n.id !== note.id)].slice(0, 200));
        if (warning) setError(warning);
        saved.current?.(note);
        const mime = response.report.audio_mime || 'audio/mpeg';
        if (audio && wallOptions.current.speak) {
          lastMime.current = mime; setLastAudio(audio);
          const urgent = response.report.priority >= 3;
          const workloadValid = () => {
            if (!drivingConnected.current) return false;
            const current = driving.current;
            if (!current) return true;
            if (current.run_id && note.context?.snapshot?.run_id && current.run_id !== note.context.snapshot.run_id) return false;
            if (wallOptions.current.hold_enabled === false) return true;
            if (current.quiet != null) return response.report.brief ? (current.brief_quiet ?? current.quiet) : current.quiet;
            return current.on_pit_road === true || (current.speed_mps != null && current.speed_mps < 2)
              || (current.brake != null && current.brake < .05 && current.steering_rad != null
                && (Math.abs(current.steering_rad) < .12 || (response.report.brief && current.throttle >= .9 && Math.abs(current.steering_rad) < .30)));
          };
          let finish;
          const finished = new Promise((resolve) => { finish = resolve; });
          const started = await play(audio, mime, {
            valid: () => !stopped && mounted.current && epoch === reportEpoch.current
              && (urgent || workloadValid()),
            workloadValid: urgent ? undefined : workloadValid,
            onStatus: (status, reason) => {
              recordDelivery(deliveryEvent(note, response.report.spoken_text), status, reason);
              if (status !== 'started') finish({ status, reason });
            },
          });
          if (!started) return;
          // The report counts as delivered when it was heard to the end. One that was cut off stays
          // undelivered, so the next poll offers it again if it is still true.
          const outcome = await Promise.race([finished, new Promise((resolve) => setTimeout(() => resolve({ status: 'completed' }), 30000))]);
          const call = `${note.context?.report_key}:${response.report.spoken_text || note.reply}`;
          const resumes = reportResumes.current.get(call) || 0;
          if (outcome.status === 'interrupted' && RESUMABLE.includes(outcome.reason) && resumes < MAX_RESUMES) {
            reportResumes.current.set(call, resumes + 1);
            if (reportResumes.current.size > 32) reportResumes.current.delete(reportResumes.current.keys().next().value);
            return;
          }
          reportResumes.current.delete(call);
        } else recordDelivery(deliveryEvent(note), 'muted', 'text_only');
        const delivered = await request(`/reports/${note.id}/ack`, {});
        if (!stopped && mounted.current && delivered.note) setNotes((current) => current.map((n) => n.id === note.id ? delivered.note : n));
      } catch (failure) { if (!stopped && wallOptions.current.level !== 'off') setError(errorMessage(failure)); }
      finally { running = false; }
    };
    reportPoll.current = poll;
    poll(); const timer = setInterval(poll, 2000);
    return () => { stopped = true; reportPoll.current = null; clearInterval(timer); };
  }, [visible, wallEnabled, request, play, isPriorityBlocked, recordDelivery, deliveryEvent]);
  useEffect(() => {
    if (!armed && !wallEnabled) return undefined;
    let socket, retry, stopped = false;
    const connect = () => {
      const url = new URL(`${apiBase}/api/live-coach/events`); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url);
      socket.onmessage = ({ data }) => {
        const event = JSON.parse(data);
        if (event.type === 'ready') { setSocketReady(true); return; }
        if (event.type === 'heartbeat') { reportPoll.current?.(); return; }
        if (event.type === 'ptt') {
          if (event.down) startRef.current(event.serial); else finishRef.current();
        }
      };
      socket.onclose = () => { setSocketReady(false); if (!stopped) retry = setTimeout(connect, 2000); };
      socket.onerror = () => socket.close();
    };
    connect();
    const preventF8 = (event) => { if (event.code === 'F8') event.preventDefault(); };
    window.addEventListener('keydown', preventF8);
    return () => { stopped = true; clearTimeout(retry); socket?.close(); window.removeEventListener('keydown', preventF8); };
  }, [armed, wallEnabled, apiBase]);

  const prepare = async () => {
    setError('');
    if (armed) {
      state.current.armed = false; setArmed(false); clearTimeout(timeout.current);
      recorder.current?.close(); recorder.current = null;
      if (state.current.phase !== 'sending') changePhase('idle');
      stopSpeaking(); return;
    }
    changePhase('arming');
    try {
      const capture = new VoiceRecorder(); recorder.current = capture;
      await capture.open();
      if (!mounted.current) { capture.close(); return; }
      state.current.armed = true; setArmed(true); changePhase('idle');
    } catch (failure) { setError(errorMessage(failure)); recorder.current?.close(); recorder.current = null; changePhase('idle'); }
  };
  const submitText = async () => {
    if (!text.trim() || state.current.phase !== 'idle') return;
    reportEpoch.current += 1; stopSpeaking();
    setError('');
    const value = text;
    changePhase('starting');
    try { const capture = await request('/captures', {}); setText(''); await submit({ capture_id: capture.capture_id, text: value }); }
    catch (failure) { setError(errorMessage(failure)); changePhase('idle'); }
  };
  const changeWallPreferences = (patch) => {
    reportEpoch.current += 1;
    setWallPreferences((current) => {
      const next = { ...current, ...patch };
      try { localStorage.setItem(PIT_WALL_KEY, JSON.stringify(next)); } catch { /* Settings remain usable. */ }
      return next;
    });
  };
  const toggleWall = () => { reportEpoch.current += 1; setWallEnabled((current) => !current); stopSpeaking(); };
  const testVoice = async (role) => {
    if (state.current.phase !== 'idle') return;
    reportEpoch.current += 1; stopSpeaking(); setError(''); changePhase('sending');
    try {
      const response = await request(`/voices/${role}/test`, {});
      if (mounted.current) { lastMime.current = 'audio/mpeg'; setLastAudio(response.audio_base64); await play(response.audio_base64); }
    } catch (failure) { if (mounted.current) setError(errorMessage(failure)); }
    finally { changePhase('idle'); }
  };
  const snapshot = status?.snapshot || {}, speech = status?.speech || {};
  const active = phase === 'recording' || phase === 'starting';
  return <section id="live-coach" hidden={!visible} aria-label="실시간 음성 코치" className="flex flex-col gap-5">
    <SpotterPanel spotter={spotter} muted={muted} volume={volume} audioError={outputMessage} />
    <PitWall enabled={wallEnabled} onToggle={toggleWall} preferences={wallPreferences} onChange={changeWallPreferences} view={wallView} engineers={status?.engineers} onTestVoice={testVoice} busy={phase !== 'idle'} speaking={isPlaying} />
    {visible && <Card asChild><section aria-label="엔지니어 음성 준비" className="coach-panel"><CardContent className="p-0"><RadioAudioPrepare apiBase={apiBase} /></CardContent></section></Card>}
    <CornerCoach coach={status?.corner_coach} controls={status?.controls} connected={!!status?.connected} />
    <RadioDiagnostics apiBase={apiBase} />
    <Card asChild><section aria-label="주행 중 대화와 코너 메모" className="coach-panel">
      <LiveHeader title="주행 중 대화와 코너 메모" topic="voiceCoach" icon={Radio} description="질문과 코너 느낌을 말한 순간의 주행 위치와 함께 기록합니다." />
      <CardContent className="p-0">
      <div className="grid gap-3 sm:grid-cols-3">
        <LiveMetric label="실시간 차량 연결" value={status?.connected ? '차량 연결됨' : '차량 연결 대기'} note={status?.message || '연결 상태 확인 중'} />
        <LiveMetric label="한국어 음성 인식" value={speech.recognition_state === 'SUCCESS' ? '연결 확인됨' : speech.recognition_state === 'FAILED' ? '연결 확인 필요' : speech.enabled && speech.recognition_installed ? '패키지 준비됨' : '설치 또는 연결 필요'} note="Chirp 3 · 한국어 · 전문 용어 힌트" />
        <LiveMetric label="주행 위치" value={status?.connected ? `랩 ${snapshot.lap_number ?? '--'} · ${Math.round((snapshot.distance_pct || 0) * 100)}%` : '위치 미확인'} note={status?.connected ? `${status.identity?.car_name || ''} · ${snapshot.speed_mps != null ? Math.round(snapshot.speed_mps * 3.6) : '--'}km/h` : '주행 후 직접 연결할 수 있습니다.'} />
      </div>
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="default" type="button" onClick={prepare} disabled={phase === 'sending' || phase === 'arming'} className="coach-primary-action flex items-center gap-2 px-4 py-3">{armed ? <MicOff size={17} /> : <Mic size={17} />}{armed ? '마이크 끄기' : phase === 'arming' ? '마이크 연결 중' : '마이크 준비'}</Button>
        <Button type="button" data-help="voicePtt" disabled={!armed || phase === 'sending'} onClick={() => active ? finish() : start()} variant={active ? 'destructive' : 'outline'}>{active ? <Square size={16} /> : <Mic size={16} />}{active ? '녹음 완료 · 전송' : '녹음 시작'}</Button>
        <p role="status" className="text-sm text-foreground">{phase === 'sending' ? '인식·저장·답변 중…' : active ? '듣고 있습니다 · 최대 45초' : armed ? socketReady ? 'F8을 누른 채 말하고 놓으세요.' : 'F8 연결 중 · 화면 녹음 버튼 사용 가능' : '먼저 마이크를 준비하세요.'}</p>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">게임에 포커스가 있어도 F8을 사용할 수 있습니다. 휠 버튼을 F8로 매핑해 사용할 수 있습니다. 앱 페이지는 열어두세요. 음성 인식과 합성은 요청할 때 Google Cloud 사용료가 발생합니다.</p>
      <p className="mt-2 text-xs leading-relaxed text-primary">기본은 측정값 답변입니다. 원리나 가능한 이유를 더 알고 싶을 때 AI 원인 분석을 선택하세요. AI 분석 모드에서만 생성형 AI 토큰을 사용하며, 측정값으로 답할 수 있는 질문은 계산으로 처리합니다.</p>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        <ToggleGroup type="multiple" variant="outline" value={[mode]} onValueChange={values => setMode(values.find(key => key !== mode) || mode)} disabled={phase !== 'idle'} aria-label="질문 답변 방식" className="flex flex-wrap justify-start gap-2">{[['measured', '측정값 답변'], ['ai', 'AI 원인 분석'], ['memo', '메모만 기록']].map(([key, label]) => <ToggleGroupItem key={key} value={key} className="min-h-11 px-3">{label}</ToggleGroupItem>)}</ToggleGroup>
        <label className="flex items-center gap-2 text-sm text-foreground"><input type="checkbox" checked={speak} onChange={(event) => { setSpeak(event.target.checked); if (!event.target.checked) stopSpeaking(); }} />음성으로 답변</label>
        <Button variant="outline" type="button" onClick={stopSpeaking} className="flex items-center gap-1  border  px-3 py-2 text-sm "><VolumeX size={15} />답변 중단</Button>
        {lastAudio && <Button variant="outline" type="button" onClick={() => { stopSpeaking(); play(lastAudio, lastMime.current); }} className="flex items-center gap-1 text-sm "><Volume2 size={15} />답변 다시 듣기</Button>}
      </div>
      <div className="mt-5 rounded-lg border bg-muted/30 p-5">
        <label htmlFor="voice-output" className="flex items-center gap-2 text-sm font-semibold text-success"><Volume2 size={17} />오디오 출력장치<HelpHint topic="voiceOutput" /></label>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <NativeSelect id="voice-output" value={outputSupported ? outputId : ''} onChange={(event) => changeOutput(event.target.value)} disabled={!outputSupported} aria-describedby="voice-output-help" className="min-h-[44px] min-w-0 w-full sm:w-auto sm:max-w-md sm:flex-1">
            <option value="">Windows 기본 장치</option>
            {outputSupported && outputId && !outputDevices.some((device) => device.deviceId === outputId) && <option value={outputId}>저장된 장치 · 연결 및 권한 확인 필요</option>}
            {outputDevices.map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `출력장치 ${index + 1}`}</option>)}
          </NativeSelect>
          <Button variant="outline" type="button" onClick={refreshOutputs} disabled={!outputSupported} className="min-h-[44px]  border  px-3 py-2 text-sm  disabled:opacity-50">목록 갱신</Button>
          <Button variant="outline" type="button" onClick={testOutput} disabled={active || phase === 'arming' || phase === 'sending'} className="flex min-h-[44px] items-center gap-2  border  px-3 py-2 text-sm  disabled:opacity-50"><Volume2 size={15} />테스트 소리</Button>
        </div>
        <p id="voice-output-help" className="mt-2 text-xs leading-relaxed text-muted-foreground">{outputSupported ? '자동 답변과 다시 듣기에 적용하며 선택은 이 브라우저에 저장합니다. 장치 이름이 안 보이면 마이크 준비 후 목록을 갱신하세요. 장치를 바꾸면 재생 중인 답변이 중단됩니다.' : '이 브라우저는 출력장치 선택을 지원하지 않아 Windows 기본 장치로 재생합니다. 최신 Chrome 또는 Edge에서 선택할 수 있습니다.'}</p>
        <div className="mt-4 border-t border-success/20 pt-4">
          <div className="flex items-center justify-between gap-3"><label htmlFor="voice-volume" className="flex items-center gap-2 text-sm font-semibold text-success">답변 볼륨<HelpHint topic="voiceVolume" /></label><output htmlFor="voice-volume" className="min-w-[60px] text-right font-mono text-sm text-success">{muted ? '음소거' : `${Math.round(volume * 100)}%`}</output></div>
          <div className="mt-2 flex items-center gap-3">
            <input id="voice-volume" type="range" min="0" max="100" step="1" value={Math.round(volume * 100)} onChange={(event) => changeVolume(Number(event.target.value) / 100)} aria-valuetext={`${Math.round(volume * 100)}%${muted ? ' · 음소거' : ''}`} aria-describedby="voice-volume-help" className="h-11 min-w-0 flex-1 cursor-pointer accent-emerald-400" />
            <Button variant="outline" type="button" onClick={toggleMute} aria-pressed={muted} className={`flex min-h-[44px] shrink-0 items-center gap-2  border px-3 py-2 text-sm ${muted ? '  ' : ' '}`}>{muted ? <VolumeX size={15} /> : <Volume2 size={15} />}{muted ? '음소거 해제' : '음소거'}</Button>
          </div>
          <p id="voice-volume-help" className="mt-1 text-xs leading-relaxed text-muted-foreground">재생 중에도 바로 적용하며 볼륨과 음소거 상태를 저장합니다. 슬라이더를 움직이면 음소거가 해제됩니다. 마이크 입력과 게임 볼륨은 별도로 조절하세요.</p>
        </div>
        <div className="mt-4 border-t border-success/20 pt-4">
          <div className="flex items-center justify-between gap-3"><label htmlFor="voice-rate" className="text-sm font-semibold text-success">여러 문장 안내 속도</label><output htmlFor="voice-rate" className="min-w-[60px] text-right font-mono text-sm text-success">{(rate ?? DEFAULT_RATE).toFixed(2)}배</output></div>
          <input id="voice-rate" type="range" min={RATE_MIN} max={RATE_MAX} step="0.05" value={rate ?? DEFAULT_RATE} onChange={(event) => changeRate?.(Number(event.target.value))} aria-valuetext={`${(rate ?? DEFAULT_RATE).toFixed(2)}배`} aria-describedby="voice-rate-help" className="mt-2 h-11 w-full cursor-pointer accent-emerald-400" />
          <p id="voice-rate-help" className="mt-1 text-xs leading-relaxed text-muted-foreground">여러 문장을 이어서 말하는 안내(랩 종료 보고, 긴 직선 한 줄, 수리 판단, 질문 답변 등)의 속도입니다. 한 문장짜리 안내는 1.15배 그대로입니다. 목소리 높이는 그대로이고 다음 안내부터 적용됩니다. 1.15배부터 1.4배까지 고를 수 있고, 포메이션 랩 안내는 1.15배로 둡니다.</p>
        </div>
        {effect && <div className="mt-4 border-t border-success/20 pt-4" aria-label="무전 효과">
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="radio-effect" className="text-sm font-semibold text-success">무전 효과</label>
            <NativeSelect id="radio-effect" value={effect.level} onChange={(event) => changeEffect({ level: event.target.value })} className="min-h-[44px] w-auto">
              {EFFECT_LEVELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </NativeSelect>
            <label className="flex items-center gap-2 text-sm text-foreground"><input type="checkbox" checked={effect.squelch} disabled={effect.level === 'off'} onChange={(event) => changeEffect({ squelch: event.target.checked })} />시작·끝 무전 잡음(칙)</label>
            <label className="flex items-center gap-2 text-sm text-foreground"><input type="checkbox" checked={effect.hiss} disabled={effect.level === 'off'} onChange={(event) => changeEffect({ hiss: event.target.checked })} />말하는 동안 바탕 잡음</label>
            <div className="flex items-center gap-3 sm:ml-4 sm:border-l sm:border-border sm:pl-6">
              <label htmlFor="radio-pops" className="text-sm text-foreground">팝노이즈</label>
              <NativeSelect id="radio-pops" value={effect.pops} disabled={effect.level === 'off'} onChange={(event) => changeEffect({ pops: event.target.value })} className="min-h-[44px] w-auto">
                {POP_MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </NativeSelect>
            </div>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="flex items-center gap-3 text-sm text-foreground">음성 크기
              <input type="range" min="20" max="150" step="5" value={effect.voice} disabled={effect.level === 'off'} onChange={(event) => changeEffect({ voice: Number(event.target.value) })} aria-valuetext={`${effect.voice}%`} className="h-11 min-w-0 flex-1 cursor-pointer accent-emerald-400" />
              <output className="min-w-[48px] text-right font-mono text-sm">{effect.voice}%</output></label>
            <label className="flex items-center gap-3 text-sm text-foreground">잡음 크기
              <input type="range" min="0" max="400" step="10" value={effect.noise} disabled={effect.level === 'off'} onChange={(event) => changeEffect({ noise: Number(event.target.value) })} aria-valuetext={`${effect.noise}%`} className="h-11 min-w-0 flex-1 cursor-pointer accent-emerald-400" />
              <output className="min-w-[48px] text-right font-mono text-sm">{effect.noise}%</output></label>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">스포터와 엔지니어의 모든 음성에 무전기 느낌의 필터를 겁니다. 음성 파일은 그대로이고 다음 안내부터 적용됩니다. “테스트 소리”로 들어 보세요. 세기가 강하면 숫자가 덜 또렷할 수 있습니다.</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">팝노이즈: 마이크에 입을 바짝 대고 말할 때 ㅂ·ㅍ 소리에서 숨이 마이크에 부딪혀 “퍽” 하고 목소리가 잠깐 깨지는 소리입니다. 실제 무전처럼 들리게 넣었습니다. 켜 두면 무전이 저음을 조금 더 살립니다.</p>
        </div>}
        {outputMessage && <p role="status" className="mt-3 text-sm text-warning">{outputMessage}</p>}
      </div>
      <div className="mt-5 border-t border-border pt-4"><label htmlFor="voice-text" className="mb-2 block text-sm font-semibold text-foreground">텍스트로 질문하거나 느낌 남기기</label><div className="flex flex-wrap gap-2"><Input id="voice-text" value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) submitText(); }} maxLength={2000} placeholder="예: 방금 코너에서 앞이 밀려서 어려웠어" className="min-w-0 flex-1  border   px-3 py-3 text-sm" /><Button variant="default" type="button" onClick={submitText} disabled={!text.trim() || phase !== 'idle'} className="coach-primary-action px-4 py-3 text-sm">기록 보내기</Button></div></div>
      {error && <p role="alert" className="mt-4 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">{error}</p>}
      <details className="mt-4 rounded-lg border border-border p-3 text-sm"><summary className="text-foreground">처음 연결하기 · 인식이 잘 안 될 때</summary><div className="mt-3 flex flex-col gap-2 leading-relaxed text-muted-foreground"><p>패키지는 음성코치_설치.bat로 설치합니다. 기존 Vertex AI와 같은 GCP 프로젝트에서 Cloud Speech-to-Text API와 Cloud Text-to-Speech API를 활성화하고, 서비스 계정의 Speech 사용 권한을 확인하세요.</p><p>헤드셋 마이크로 한 문장씩 말하세요. 마이크 준비는 사용자가 켤 때만 이루어집니다. 녹음 원본은 DB에 저장하지 않으며, 녹음한 구간만 인식 서비스로 전송합니다.</p><p>인식된 문장이 틀리면 주행 후 대화 기록에서 수정할 수 있습니다. 차량 연결이 끊기면 위치를 추정하지 않고 직접 연결 대기로 남깁니다.</p></div></details>
      </CardContent></section></Card>
    <Card asChild><section aria-label="최근 대화 기록" className="coach-panel"><LiveHeader title="최근 대화 기록" description="최신 5개 대화를 표시합니다. 주행 후 같은 랩과 코너의 분석 결과에 연결됩니다." action={<Badge variant="secondary">{Math.min(notes.length, 5)}개</Badge>} /><CardContent className="p-0">
      {notes.length ? notes.slice(0, 5).map(note => <article key={note.id} className="coach-live-note"><NoteText note={note} /></article>) : <p className="py-4 text-sm text-muted-foreground">아직 대화 기록이 없습니다. 마이크 또는 텍스트로 첫 메모를 남겨보세요.</p>}
    </CardContent></section></Card>
  </section>;
}

export function VoiceReview({ review, filename, laps = [], corners = [], apiBase, onRefresh, onNavigate, onOpenCorner }) {
  const [editing, setEditing] = useState(null), [text, setText] = useState(''), [lap, setLap] = useState(''), [corner, setCorner] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const notes = review?.notes || [], pending = review?.pending || [];
  const open = (note) => { setEditing(note.id); setText(note.text); setLap(String(note.lap_number || laps[0]?.lap_number || '')); setCorner(''); setError(''); };
  const save = async () => {
    setBusy(true); setError('');
    try {
      const selected = corners.find((item) => item.id === corner);
      const payload = { text };
      if (selected) { payload.filename = filename; payload.lap_number = Number(lap); payload.distance_pct = selected.apex_pct; }
      const response = await fetch(`${apiBase}/api/live-coach/notes/${editing}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '기록 수정 실패');
      setEditing(null); await onRefresh();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  const editor = (note) => editing === note.id && <div className="mt-3 rounded-xl border border-accent/30 bg-accent/10 p-3">
    <label className="block text-xs text-foreground">인식된 문장 수정<Textarea aria-label="대화 내용 수정" value={text} onChange={(event) => setText(event.target.value)} maxLength={2000} className="mt-2 min-h-[72px] w-full border   p-3 text-sm" /></label>
    <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-xs text-foreground">메모를 남긴 랩<NativeSelect aria-label="대화 연결 랩" value={lap} onChange={(event) => setLap(event.target.value)} className="mt-1 w-full">{laps.map((item) => <option key={item.lap_number} value={item.lap_number}>랩 {item.lap_number}</option>)}</NativeSelect></label><label className="text-xs text-foreground">코너 위치 확인<NativeSelect aria-label="대화 연결 코너" value={corner} onChange={(event) => setCorner(event.target.value)} className="mt-1 w-full"><option value="">위치 유지 · 문장만 수정</option>{corners.map((item) => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}</NativeSelect></label></div>
    <div className="mt-3 flex gap-3"><Button variant="default" type="button" disabled={busy || !text.trim()} onClick={save} className="coach-primary-action px-3 py-2 text-sm">저장하고 분석 갱신</Button><Button variant="outline" type="button" onClick={() => setEditing(null)} className="text-sm ">닫기</Button></div>{error && <p role="alert" className="mt-2 text-sm text-warning">{error}</p>}
  </div>;
  return <section id="voice-review" aria-label="대화로 남긴 코너" className="coach-panel">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-xl font-bold"><Mic className="text-foreground" />대화로 남긴 코너<HelpHint topic="voiceReview" /></h2><Button variant="outline" type="button" onClick={onRefresh} className=" border  px-3 py-2 text-sm ">대화 기록 다시 연결</Button></div>
    <p className="mt-2 text-sm leading-relaxed text-muted-foreground">드라이버의 느낌 → 해당 랩의 측정 → 다음 주행 → 셋업 가설 순서로 확인하세요. 자동 구간 번호는 공식 턴 번호가 아닙니다.</p>
    {review?.error && <p role="alert" className="mt-3 text-sm text-warning">{review.error}</p>}
    {!notes.length && <p className="mt-4 rounded-lg bg-background p-4 text-sm text-muted-foreground">이 파일에 연결된 대화가 없습니다. 음성 코치에서 메모를 남기거나 아래 연결 대기 기록의 랩·코너를 지정하세요.</p>}
    <div className="mt-4 flex flex-col gap-4">{notes.map((note) => <article key={note.id} className="rounded-xl border border-accent/30 bg-accent/20 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-bold text-foreground">랩 {note.lap_number} · {note.corner_label}</span><Button variant="outline" type="button" onClick={() => open(note)} className="text-xs ">문장·위치 수정</Button></div>
      <p className="mt-1 text-xs text-muted-foreground">{note.anchor_source === 'RECENT_CORNER' ? '발언 직전 코너에 연결' : note.link_method === 'MANUAL' ? '직접 확인한 위치' : '세션·주행 위치로 연결'} · {note.comparison_note}</p>
      <div className="mt-3"><NoteText note={note} /></div>
      {note.is_engineer_report ? <div className="mt-3 rounded-lg border border-accent/25 bg-accent/10 p-3 text-sm text-foreground">당시 계산 근거: 클래스 {note.report_context?.class_position ?? '--'}위 · 연료 {note.report_context?.fuel_l?.toFixed(1) ?? '--'}L · 예상 남은 거리 {note.report_context?.remaining_laps?.toFixed(1) ?? '--'}랩 · 추가 급유 {note.report_context?.fuel_to_add_l?.toFixed(1) ?? '--'}L<p className="mt-2 text-xs text-muted-foreground">경기 중 보고와 당시 추정값입니다. 상대의 피트 계획이나 셋업 효과를 증명하지 않습니다.</p></div> : <div className="mt-4 grid gap-3 md:grid-cols-2"><div className="rounded-lg border border-primary/25 bg-primary/10 p-3"><h3 className="text-sm font-bold text-primary">해당 랩에서 관측한 내용</h3><div className="mt-2 flex flex-col gap-1 text-sm leading-relaxed text-foreground">{note.evidence?.length ? note.evidence.map((item, i) => <p key={i}>{item}</p>) : <p>코너 위치 또는 측정 데이터가 부족합니다.</p>}</div><p className="mt-3 text-sm text-primary">다음 주행: {note.driving_action}</p>{note.corner_id && <Button variant="outline" type="button" onClick={() => onOpenCorner ? onOpenCorner(note) : onNavigate('corner-inputs', { distance_pct: note.distance_pct })} className="mt-3  border  px-3 py-2 text-xs ">메모를 남긴 랩·위치 비교</Button>}</div>
      <div className="rounded-lg border border-warning/25 bg-warning/10 p-3"><h3 className="text-sm font-bold text-warning">시험할 셋업 가설 · 효과 미확정</h3>{note.setup_candidates?.map((candidate, index) => <div key={index} className="mt-3 text-sm"><p className="font-semibold text-foreground">{candidate.label || candidate.setting}</p><p className="mt-1 font-mono text-warning">{candidate.original_value ?? '--'} → {candidate.value ?? candidate.step_text ?? '차고에서 작은 변경 확인'} {candidate.unit || ''}</p>{candidate.reason && <p className="mt-1 text-foreground">{candidate.reason}</p>}</div>)}<p className="mt-3 text-sm leading-relaxed text-foreground">{note.setup_note}</p><Button variant="outline" type="button" onClick={() => onNavigate('briefing')} className="mt-3  border  px-3 py-2 text-xs ">셋업 실험에서 증상 구체화</Button></div></div>}
      {editor(note)}
    </article>)}</div>
    {!!pending.length && <details className="mt-4 rounded-xl border border-border p-4"><summary className="font-semibold text-foreground">파일·위치 연결 대기 {review.pending_count ?? pending.length}개</summary><p className="mt-2 text-xs leading-relaxed text-muted-foreground">자동 연결 근거가 부족한 기록입니다. 현재 파일의 랩·코너를 직접 선택할 수 있습니다.</p><div className="mt-3 flex flex-col gap-3">{pending.map((note) => <article key={note.id} className="rounded-lg bg-background p-3"><NoteText note={note} /><Button variant="outline" type="button" onClick={() => open(note)} className="mt-3 text-sm  ">현재 파일에 연결·내용 수정</Button>{editor(note)}</article>)}</div></details>}
  </section>;
}
