import React, { useEffect, useState } from 'react';
import { Button } from './ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from './ui/field';
import { NativeSelect } from './ui/native-select';
import { Input } from './ui/input';
import { localSpeechRequest } from '../useLocalSpeech';

export {QUESTION_RESULTS} from './LocalSpeechHistory';

export default function LocalSpeechSettings({ apiBase, controller, armed, busy, onStatusChange }) {
  const [status, setStatus] = useState(null), [error, setError] = useState('');
  const [installing, setInstalling] = useState(false), [questions, setQuestions] = useState([]);
  const [questionSearch, setQuestionSearch] = useState('');
  const [commands,setCommands]=useState([]);
  const local = controller.mode === 'local';
  useEffect(() => {
    if (!local) return undefined;
    let timer, closed = false;
    const abort = new AbortController();
    async function poll() {
      try {
        const value = await localSpeechRequest(apiBase, '', undefined, abort.signal);
        if (closed) return;
        setStatus(value);
        onStatusChange?.(value);
        setError('');
        setInstalling(['packages', 'model'].includes(value.setup));
        timer = setTimeout(poll, ['packages', 'model'].includes(value.setup) ? 1500 : 5000);
      } catch (e) { if (!closed) { setError(e.message); timer = setTimeout(poll, 5000); } }
    }
    poll();
    localSpeechRequest(apiBase, '/questions', undefined, abort.signal).then(data => {
      if (!closed) {setQuestions(data.questions || []);setCommands(data.commands || []);}
    }).catch(() => {});
    return () => { closed = true; clearTimeout(timer); abort.abort(); };
  }, [apiBase, local, onStatusChange]);
  async function install(enhancementOnly = false) {
    setError(''); setInstalling(true);
    try {
      const value = await localSpeechRequest(apiBase, '/install', { owner: controller.owner, model: controller.model,
        ...(enhancementOnly === true ? { enhancement_only: true } : {}) });
      setStatus(value); onStatusChange?.(value);
    }
    catch (e) { setError(e.message); setInstalling(false); }
  }
  const installed = status?.installed?.[controller.model];
  const search = questionSearch.replace(/\s/g, '').toLowerCase();
  const visibleQuestions = questions.filter(q => [q.name, q.group, ...q.examples, ...(q.includes || [])].join('').replace(/\s/g, '').toLowerCase().includes(search));
  return <Card><CardHeader><CardTitle>한국어 질문 인식</CardTitle><CardDescription>말한 질문을 주행 정보와 연결합니다.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-4">
      <FieldGroup>{local && <Field><FieldLabel htmlFor="speech-model">인식 모델</FieldLabel>
          <NativeSelect id="speech-model" value={controller.model} disabled={armed || busy || installing} onChange={e => controller.setModel(e.target.value)}>
            <option value="sensevoice">SenseVoice · 권장</option><option value="base">Whisper base · 비교용</option>
          </NativeSelect><FieldDescription>SenseVoice는 한국어 레이스 질문의 속도와 연결 성공률을 기준으로 선택했습니다. Whisper base와 비교할 수 있습니다. 둘 다 CPU 두 스레드로 처리합니다.</FieldDescription>
        </Field>}
      </FieldGroup>
      {local && <>
        <p role="status">{installing ? status?.setup === 'model' ? '인식 모델을 받는 중…' : '인식 프로그램을 설치하는 중…' : !status ? '인식기 설치 상태를 확인하고 있습니다…' : installed ? '설치 완료 · 음성 인식을 켜면 모델을 불러옵니다.' : '처음 한 번 인식 프로그램과 모델을 받아 주세요.'}</p>
        <Button variant="outline" disabled={armed || busy || installing} onClick={install}>{installing ? '설치 중' : installed ? '인식기 설치 확인·복구' : '로컬 인식기 받기'}</Button>
        {controller.model==='sensevoice' && installed && <Field>
          <FieldLabel>소음이 섞인 질문 재인식</FieldLabel>
          <FieldDescription>{status?.enhancement_installed?'준비됨. 연결되지 않은 질문만 소음을 줄여 한 번 더 인식합니다.':'작은 잡음 제거 모델을 추가로 받으면 연결되지 않은 질문만 한 번 더 인식합니다.'} 피트 명령과 불명확한 대상·숫자는 재인식으로 실행하지 않습니다. 대기 중 추가 연산은 없습니다.</FieldDescription>
          {!status?.enhancement_installed && <Button variant="outline" disabled={armed||busy||installing} onClick={()=>install(true)}>소음 재인식 준비 · 약 0.6MB</Button>}
        </Field>}
        <p className="text-sm text-muted-foreground">설치할 때만 인터넷이 필요합니다. 이후 질문은 이 PC에서 처리하며 호출 비용이 없습니다. 한 번에 10초까지 질문할 수 있습니다.</p>
        <p className="text-sm text-muted-foreground">{controller.model==='sensevoice'?'SenseVoice 모델 약 240MB와 인식 프로그램을 앱에서 별도로 받습니다.':'Whisper base 모델 약 148MB와 인식 프로그램을 별도로 받습니다.'} 설치 공간은 환경에 따라 달라지며 다운로드 캐시는 별도입니다.</p>
        <a className="text-primary underline" href={controller.model==='sensevoice'?'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17':'https://huggingface.co/Systran/faster-whisper-base'} target="_blank" rel="noreferrer">모델 다운로드 출처</a>
        <p className="text-sm text-muted-foreground">뒷차·뒤 차량·후방 차량은 같은 질문으로 연결합니다. ‘기차’처럼 등록된 오인식은 간격·추격·피트 문맥이 맞을 때만 보정합니다. 인식 테스트에서 원문과 보정 문장을 비교하세요.</p>
        <details><summary>질문 예시 · {questions.length}가지</summary>
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">“지금 뭐에 집중해야 해?”로 현재 레이스의 우선순위를 물어보세요. 연료·피트 계획을 먼저 확인하고, 근거가 있으면 뒤차 방어 또는 앞차와의 구간 비교를 들려줍니다. 기록에서 판단 이유를 확인할 수 있습니다.</p>
            <p className="text-sm text-muted-foreground">“연료 상황 보고”, “경기 상황 보고”, “앞차 상황 보고”로 여러 정보를 묶어 들을 수 있습니다. 현재 측정값이 있는 항목만 답하며 빠진 항목은 로그에 표시합니다. 시간·인시던트 등 해당 수치의 녹음이 없는 답변은 텍스트로 표시합니다.</p>
            <Field><FieldLabel htmlFor="speech-question-search">질문 찾기</FieldLabel><Input id="speech-question-search" value={questionSearch} onChange={e=>setQuestionSearch(e.target.value)} placeholder="연료, 경쟁차, 피트, 보고…"/></Field>
            <p>{visibleQuestions.length}개 질문</p>
            <ul className="flex flex-col gap-2">{visibleQuestions.map(q => <li key={q.id}><strong>{q.name}</strong>{q.group && <p className="text-sm text-muted-foreground">{q.group}</p>}<p>{q.examples.join(' / ')}{q.game === 'lmu' ? ' · LMU 전용' : q.game === 'iracing' ? ' · iRacing 전용' : ''}</p>{q.includes?.length > 0 && <p>보고 항목: {q.includes.join(' → ')}</p>}</li>)}</ul>
            {!visibleQuestions.length && <p>일치하는 질문이 없습니다.</p>}
          </div>
        </details>
        <details><summary>iRacing 피트 명령 예시</summary>
          <p>“피트 레인 타이어 교체” 또는 “피트 웻 타이어 교체”는 웻 네 바퀴로, “피트 타이어 교체” 또는 “피트 슬릭 타이어 교체”는 슬릭 네 바퀴로 설정합니다.</p>
          <p>“피트 급유 15리터”, “피트 타이어 전부 바꿔줘”, “피트 앞유리 닦아줘”처럼 말해도 됩니다. 앞의 “피트”가 “히트”나 “키트”로 인식되어도 같은 명령으로 연결합니다. 시작말 뒤의 질문·부정 표현·서로 다른 명령을 섞은 문장은 전송하지 않습니다.</p>
          <p>명령은 “피트”로 시작하세요. 연료는 다음 정차에서 추가할 양이며 1~150리터의 정수를 지원합니다. “설정”은 생략해도 되지만 리터 단위는 말해 주세요. 공기압은 단위를 포함해야 하며 100~400kPa 범위에서 정수 kPa로 변환합니다. 완주 연료 설정은 한 번의 추가 급유로 완주 가능한 계획이 있을 때만 사용합니다. 게임 반영을 확인한 설정은 음성으로 복창하며 미확인은 따로 알립니다.</p>
          <ul>{commands.map(text=><li key={text}>{text}</li>)}</ul>
        </details>

      </>}
      {(error || status?.error) && <p role="alert">{error || status.error}</p>}
    </CardContent></Card>;
}
