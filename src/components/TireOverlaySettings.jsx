import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from './ui/card';
import { Field, FieldGroup, FieldLabel } from './ui/field';
import { Switch } from './ui/switch';
import { Button } from './ui/button';
import { Alert, AlertDescription } from './ui/alert';
import { Input } from './ui/input';

const APPEARANCE = [['background_opacity', '배경 불투명도', 85, 100, '%'],
  ['content_opacity', '표시 불투명도', 100, 100, '%'], ['radius', '모서리 둥글기 (R)', 12, 32, 'px']];
const TARGETS = [['pressure_low_bar', '압력 하한 (bar)', .01, .4, 4], ['pressure_high_bar', '압력 상한 (bar)', .01, .4, 4],
  ['temp_low_c', '온도 하한 (°C)', 1, -50, 250], ['temp_high_c', '온도 상한 (°C)', 1, -50, 250],
  ['pressure_min_cold_bar', '셋업 최저 냉간 압력 (bar)', .01, .4, 4]];

function TargetRanges({ view, disabled, save, open }) {
  const [values, setValues] = useState(() => ({ ...view.targets }));
  const [error, setError] = useState('');
  const submit = () => {
    const next = Object.fromEntries(TARGETS.map(([key]) => [key, values[key] === '' || values[key] == null ? null : Number(values[key])]));
    for (const [low, high] of [['pressure_low_bar','pressure_high_bar'],['temp_low_c','temp_high_c']]) {
      if ((next[low] == null) !== (next[high] == null) || (next[low] != null && next[low] >= next[high])) {
        setError('하한과 상한을 함께 입력하고 상한을 더 크게 지정해 주세요.'); return;
      }
    }
    setError(''); save({ car: view.car_path, ...next });
  };
  return <details className="text-sm" open={open}>
    <summary>차량별 음성 판단 범위{view.car_name ? ` · ${view.car_name}` : ''}</summary>
    <div className="flex flex-col gap-3 pt-3">
      <p className="text-muted-foreground">대시 색을 우선 사용합니다. 색 정보가 없는 항목은 아래 범위로 판단하며, 비워 두면 판단을 보류합니다. 온도는 현재 차량에서 측정하는 종류에 적용됩니다.</p>
      <FieldGroup className="tire-appearance-grid">
        {TARGETS.map(([key,label,step,min,max]) => <Field key={key} data-disabled={disabled || !view.car_path || (key.startsWith('temp_') && !view.target_temperature_kind)}>
          <FieldLabel htmlFor={`tire-target-${key}`}>{label}</FieldLabel>
          <Input id={`tire-target-${key}`} type="number" step={step} min={min} max={max} value={values[key] ?? ''}
            disabled={disabled || !view.car_path || (key.startsWith('temp_') && !view.target_temperature_kind)}
            onChange={event => setValues(current => ({ ...current, [key]: event.target.value }))}/>
        </Field>)}
      </FieldGroup>
      <Button variant="outline" size="sm" disabled={disabled || !view.car_path} onClick={submit}>현재 차량 범위 저장</Button>
      {error && <p role="alert">{error}</p>}
    </div>
  </details>;
}

const ADVICE_REASONS = {
  fixed_or_unknown_setup: '고정 셋업이거나 셋업 종류를 확인하지 못해 압력 추천을 하지 않습니다.',
  no_target_range: '목표 열간 압력 범위가 없습니다. 아래 판단 범위를 저장하거나, 대시 색이 바뀌는 차량은 주행 기록이 쌓이면 자동으로 정해집니다.',
  no_evidence: '근거가 되는 스틴트가 아직 없습니다. 장착 압력이 확인된 레이스·연습 스틴트(근거 랩 3개 이상)가 필요합니다.',
  advice_error: '추천을 계산하지 못했습니다. 측정과 표시는 계속됩니다.',
};
const ADVICE_MODELS = { fitted: '여러 스틴트 회귀', nearest_with_default_slope: '가장 비슷한 스틴트 + 노면 온도 추정 보정',
  median_without_temperature: '노면 온도 없이 중앙값' };
const bar = kpa => (kpa / 100).toFixed(2), psi = kpa => (kpa / 6.894757293).toFixed(1);

function forecastText(forecast) {
  if (!forecast || !String(forecast.basis || '').startsWith('forecast') || typeof forecast.track_c !== 'number') return '';
  const offset = forecast.basis === 'forecast_with_learned_offset' ? '이 트랙 기록의 노면-기온 차이' : '지금의 노면-기온 차이';
  const misses = (forecast.recent_misses_c || []).filter(v => typeof v === 'number');
  const miss = misses.length ? ` · 최근 예보 오차 평균 ${(misses.reduce((a, b) => a + Math.abs(b), 0) / misses.length).toFixed(1)}°C` : '';
  const now = typeof forecast.track_now_c === 'number' ? ` (지금 ${forecast.track_now_c.toFixed(1)}°C)` : '';
  return `Weather 예보 기준 약 ${forecast.minutes_ahead}분 뒤 노면 ${forecast.track_c.toFixed(1)}°C${now} · ${offset}로 추정${miss}`;
}

const signedBar = kpa => `${kpa >= 0 ? '+' : '−'}${Math.abs(kpa / 100).toFixed(2)}`;
function ACTIONS(value) {
  if (value.at_minimum) return ' · 최저 유지';
  if (value.action === 'raise') return ` · ▲ ${signedBar(value.change_kpa)} 올림`;
  if (value.action === 'lower') return ` · ▼ ${signedBar(value.change_kpa)} 내림`;
  if (value.action === 'keep') return ' · 유지';
  return '';
}

function atMinimumText(advice) {
  const floored = Object.entries(advice.wheels || {}).filter(([, v]) => v.at_minimum);
  if (!floored.length) return null;
  const over = Math.max(...floored.map(([, v]) => v.hot_over_target_kpa || 0));
  const source = floored.some(([, v]) => v.floor_source === 'driver_minimum') ? '입력한 셋업 최저값'
    : '이 차량 기록상 가장 낮은 냉간 압력(차량 최저값을 아래에 입력하면 그 값을 씁니다)';
  return <p role="note">
    {floored.length === Object.keys(advice.wheels).length ? '모든 바퀴가' : `${floored.map(([w]) => w).join(', ')}가`} 이미 최저 압력이라 더 낮출 수 없습니다.
    {over > 0 ? ` 이 조건에서는 열간 압력이 목표보다 약 ${(over / 100).toFixed(2)} bar 높게 예상됩니다.` : ''} 최저값 기준: {source}.
  </p>;
}

function AdviceView({ advice, voice, audioReady }) {
  const title = (advice.kind === 'garage' ? '출발 냉간 압력 추천' : '다음 피트 냉간 압력 추천')
    + (advice.status === 'ready' && advice.action === 'keep' ? ' · 지금 압력 유지' : '');
  if (advice.status !== 'ready') return <div className="text-sm" aria-label="압력 추천">
    <p className="font-medium">{title}</p>
    <p className="text-muted-foreground">{ADVICE_REASONS[advice.reason] || '추천을 보류했습니다.'}</p>
  </div>;
  const wheels = Object.entries(advice.wheels || {});
  const stints = Math.max(0, ...wheels.map(([, v]) => v.stints || 0));
  const models = [...new Set(wheels.map(([, v]) => v.model).filter(Boolean))].map(m => ADVICE_MODELS[m] || m).join(', ');
  return <div className="flex flex-col gap-2 text-sm" aria-label="압력 추천">
    <p className="font-medium">{title}</p>
    <div className="tire-appearance-grid">
      {wheels.map(([wheel, value]) => <p key={wheel}>
        <span className="text-muted-foreground">{wheel} </span>
        {typeof value.cold_kpa === 'number' ? `${bar(value.cold_kpa)} bar · ${psi(value.cold_kpa)} psi${ACTIONS(value)}` : '근거 없음'}
      </p>)}
    </div>
    {atMinimumText(advice)}
    {forecastText(advice.forecast) && <p className="text-muted-foreground">{forecastText(advice.forecast)}</p>}
    <p className="text-muted-foreground">
      {typeof advice.track_temp_c === 'number' ? `노면 ${advice.track_temp_c.toFixed(1)}°C 기준 · ` : ''}
      목표 열간 {bar(advice.target_kpa)} bar ({advice.target_source === 'driver_range' ? '저장한 범위' : '대시 색 학습'})
      {` · 근거 스틴트 ${stints}개 · ${models}`}
    </p>
    <p className="text-muted-foreground">참고값입니다. 반올림 단위는 차량 셋업 단계를 확인하지 않았습니다. 셋업 변경은 직접 판단해 주세요.</p>
    {voice && audioReady === false && <p className="text-muted-foreground">추천 안내 음성은 아직 준비되지 않았습니다. 화면 숫자는 그대로 표시됩니다.</p>}
    {advice.double_stint && <DoubleStint data={advice.double_stint}/>}
  </div>;
}

const pct = v => `${Math.round(v * 100)}%`;
function DoubleStint({ data }) {
  if (data.status !== 'ready') return <p className="text-muted-foreground">더블 스틴트 참고: 이 차량·트랙의 마모 기록이 아직 부족합니다.</p>;
  const rows = Object.entries(data.wheels).filter(([, v]) => typeof v.wear_after_next === 'number');
  const laps = Math.round(rows[0]?.[1].stint_laps || 0);
  return <div aria-label="더블 스틴트 참고">
    <p className="font-medium">더블 스틴트 참고 · 이 타이어로 같은 길이(약 {laps}랩)를 한 번 더 가면</p>
    <div className="tire-appearance-grid">
      {rows.map(([wheel, v]) => <p key={wheel} className={wheel === data.worst ? 'font-medium' : undefined}>
        <span className="text-muted-foreground">{wheel} </span>
        지금 {pct(v.wear_now)}{v.wear_now_basis === 'live' ? '' : '(추정)'} → 피트 {pct(v.wear_at_stop)} → 다음 스틴트 끝 {pct(v.wear_after_next)}
      </p>)}
    </div>
    <p className="text-muted-foreground">남은 마모 비율 예상입니다. 마모만으로 그립이나 랩 타임을 판단하지 않으니 더블 스틴트 여부는 직접 결정해 주세요.</p>
  </div>;
}

function digitText(reader) {
  if (!reader?.mode) return '';
  if (reader.mode === 'self') {
    const rate = typeof reader.agreement === 'number' ? ` · 학습 때 일치율 ${(reader.agreement * 100).toFixed(1)}%` : '';
    return `숫자 인식: 자체 인식 사용 중${rate} · Windows OCR은 가끔 검증과 보조에만 사용`;
  }
  return `숫자 인식: Windows OCR로 학습 중 · 비교 ${reader.compared ?? 0}/${reader.needed ?? 500}`
    + ` · 숫자 ${reader.digits_learned ?? 0}/10 · 서로 다른 값 ${reader.values_seen ?? 0}/${reader.values_needed ?? 8}`;
}

function statusText(view) {
  if (!view) return '상태 확인 중';
  if (!view.supported) return 'Windows에서 사용할 수 있습니다.';
  if (!view.enabled && !view.voice_enabled) return '꺼짐';
  if (!view.running) return '타이어 측정 종료됨';
  if (view.status === 'unsupported_car') return '지원 차량 탑승 대기';
  if (view.status === 'cockpit_warmup') return `계기판 준비 대기 · ${view.warmup_remaining_s ?? 10}초`;
  if (view.visible) return `${view.car_name || '타이어'} · ${view.display_status || '측정 중'}`;
  if (view.voice_enabled && !view.enabled && view.status === 'reading') return `${view.car_name || '타이어'} · 음성 안내만 사용 중`;
  if (view.status && !['sdk_waiting', 'cockpit_waiting'].includes(view.status)) return '계기판 측정 대기';
  return `${view.game === 'lmu' ? 'LMU' : 'iRacing'} 탑승 대기`;
}

export default function TireOverlaySettings({ apiBase, fetcher = fetch, game = 'iracing' }) {
  const id = useId();
  const [view, setView] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({});
  const alive = useRef(false), mutation = useRef(false), revision = useRef(0), poll = useRef(null);
  const slides = useRef({ pending: {}, timer: null });
  const request = useCallback(async (path = '', method = 'GET', body, signal) => {
    const response = await fetcher(`${apiBase}/api/tire-overlay${path}${game === 'lmu' ? '?game=lmu' : ''}`, {
      method, signal, ...(body === undefined ? {} : {
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      }),
    });
    const data = await response.json();
    if (!response.ok || data.version !== 1) {
      throw new Error(response.status === 404 || (response.ok && data.version !== 1)
        ? '추가 오버레이를 사용하려면 앱과 실행창을 완전히 종료한 뒤 다시 실행해 주세요.'
        : typeof data.detail === 'string' ? data.detail : '타이어 오버레이에 연결하지 못했습니다.');
    }
    return data;
  }, [apiBase, fetcher, game]);
  useEffect(() => {
    alive.current = true;
    setView(null); setDraft({}); setBusy(false); setError(''); mutation.current = false;
    let timer;
    const refresh = async () => {
      if (!alive.current) return;
      if (!mutation.current && !slides.current.timer && !Object.keys(slides.current.pending).length) {
        const ticket = revision.current, controller = new AbortController();
        poll.current = controller;
        try {
          const data = await request('', 'GET', undefined, controller.signal);
          if (alive.current && ticket === revision.current && !mutation.current) {
            setView(data); setError('');
          }
        } catch (failure) {
          if (alive.current && ticket === revision.current && failure.name !== 'AbortError') setError(failure.message);
        } finally {
          if (poll.current === controller) poll.current = null;
        }
      }
      if (alive.current) timer = setTimeout(refresh, 1000);
    };
    refresh();
    return () => {
      alive.current = false; revision.current += 1;
      clearTimeout(timer); poll.current?.abort();
      clearTimeout(slides.current.timer); slides.current = { pending: {}, timer: null };
    };
  }, [game, request]);
  const apply = async (body, reset = false, path) => {
    if (mutation.current) return;
    const ticket = ++revision.current;
    mutation.current = ticket;
    poll.current?.abort(); setBusy(true); setError('');
    try {
      const data = await request(path || (reset ? '/reset-position' : '/settings'), reset ? 'POST' : 'PUT', body);
      if (alive.current && ticket === revision.current) setView(data);
    } catch (failure) {
      if (alive.current && ticket === revision.current) { setError(failure.message); setDraft({}); }
    } finally {
      if (mutation.current === ticket) mutation.current = false;
      if (alive.current && ticket === revision.current) setBusy(false);
    }
  };
  const flushSlides = async () => {
    const stream = slides.current;
    clearTimeout(stream.timer); stream.timer = null;
    if (!alive.current) return;
    if (mutation.current) { stream.timer = setTimeout(flushSlides, 60); return; }
    if (!Object.keys(stream.pending).length) return;
    const body = stream.pending; stream.pending = {};
    await apply(body);
    if (alive.current && slides.current === stream && Object.keys(stream.pending).length) stream.timer = setTimeout(flushSlides, 60);
  };
  const slide = (key, value) => {
    revision.current += 1; poll.current?.abort();
    setDraft(current => ({ ...current, [key]: value }));
    const stream = slides.current;
    stream.pending[key] = value;
    if (!stream.timer) stream.timer = setTimeout(flushSlides, 60);
  };
  const disabled = busy || !view || !view.supported;
  return <Card aria-label="추가 오버레이" className="tire-overlay-settings">
    <CardHeader className="px-4 py-3"><CardTitle asChild><h3>추가 오버레이 · 타이어 압력·온도</h3></CardTitle>
      <CardDescription>숫자 표시와 음성 안내를 따로 선택할 수 있습니다.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3 px-4 pb-4 pt-0">
      <>
        <div className="tire-settings-layout">
        <div className="tire-settings-controls">
        <FieldGroup className="tire-settings-toggles">
          <Field orientation="horizontal" data-disabled={disabled}>
            <FieldLabel htmlFor={`${id}-enabled`}>타이어 오버레이 표시</FieldLabel>
            <Switch id={`${id}-enabled`} checked={view?.enabled === true} disabled={disabled}
              onCheckedChange={enabled => apply({ enabled })}/>
          </Field>
          <Field orientation="horizontal" data-disabled={disabled}>
            <FieldLabel htmlFor={`${id}-voice`}>타이어 음성 안내</FieldLabel>
            <Switch id={`${id}-voice`} checked={view?.voice_enabled === true} disabled={disabled}
              onCheckedChange={voice_enabled => apply({ voice_enabled })}/>
          </Field>
          <Field orientation="horizontal" data-disabled={disabled}>
            <FieldLabel htmlFor={`${id}-fixed`}>타이어 창 위치 잠금</FieldLabel>
            <Switch id={`${id}-fixed`} checked={view?.fixed === true} disabled={disabled}
              onCheckedChange={fixed => apply({ fixed })}/>
          </Field>
        </FieldGroup>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" size="sm" disabled={disabled} onClick={() => apply(undefined, true)}>타이어 창 위치 초기화</Button>
          {(view?.enabled || view?.voice_enabled) && !view.running && <Button variant="outline" size="sm" disabled={disabled} onClick={() => apply({ enabled: view.enabled, voice_enabled: view.voice_enabled })}>타이어 창 다시 켜기</Button>}
        </div>
        <p className="text-sm text-muted-foreground">숫자만 표시 · 위치는 차량별 자동 저장 · 첫 측정 전에는 표시하지 않습니다.</p>
        </div>
        <div className="tire-settings-controls">
        <FieldGroup className="tire-appearance-grid">
          {APPEARANCE.map(([key, label, fallback, max, unit]) => <Field key={key}>
            <div className="flex items-center justify-between gap-2">
              <FieldLabel htmlFor={`${id}-${key}`}>{label}</FieldLabel>
              <output htmlFor={`${id}-${key}`}>{draft[key] ?? view?.[key] ?? fallback}{unit}</output>
            </div>
            <Input id={`${id}-${key}`} type="range" min="0" max={max} step="1"
              disabled={!view || !view.supported} value={draft[key] ?? view?.[key] ?? fallback}
              onChange={event => slide(key, Number(event.target.value))}/>
          </Field>)}
        </FieldGroup>
        <div className="tire-settings-info">
          <p role="status" className="text-sm text-muted-foreground">{statusText(view)}</p>
          <p className="text-sm text-muted-foreground">탑승·그리드 진입 10초 뒤부터 1초마다 측정 · bar / °C</p>
          <p className="text-sm text-muted-foreground">{game === 'lmu' ? '공유 메모리에서 압력과 표면 온도를 읽습니다.' : '읽기 실패 시 이전 값 유지. 타이어 페이지가 보여야 하며, 화면 밖 숫자와 없는 온도는 추정하지 않습니다.'}</p>
          <p className="text-sm text-muted-foreground">표시를 꺼도 음성 안내는 계속됩니다. 기존 피트월 목소리·음량을 사용하며, 근거가 부족하면 안내하지 않습니다.</p>
          {view?.voice_enabled && view.voice_audio_ready === false && <p className="text-sm text-muted-foreground">타이어 안내 음원이 아직 준비되지 않았습니다.</p>}
          {view?.history_error && <p className="text-sm text-muted-foreground">타이어 기록을 저장하지 못했습니다. 측정과 표시는 계속됩니다.</p>}
          {game !== 'lmu' && view?.digit_reader?.mode && <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-muted-foreground">{digitText(view.digit_reader)}</p>
            <Button variant="outline" size="sm" disabled={disabled || !view.car_path} onClick={() => apply(undefined, true, '/digits/reset')}>현재 차량 숫자 학습 초기화</Button>
          </div>}
        </div>
        </div>
        </div>
        {game === 'lmu' && view && <Field orientation="horizontal" data-disabled={disabled}>
          <FieldLabel htmlFor={`${id}-lmu-open`}>오픈 셋업 경기에서 압력 추천 사용</FieldLabel>
          <Switch id={`${id}-lmu-open`} checked={view.lmu_open_setup === true} disabled={disabled}
            onCheckedChange={lmu_open_setup => apply({ lmu_open_setup })}/>
        </Field>}
        {game === 'lmu' && view && <p className="text-sm text-muted-foreground">LMU는 셋업 고정 여부를 알려주지 않습니다. 셋업을 바꿀 수 있는 경기에서만 켜 주세요. 냉간 압력은 차고와 새 타이어 직후의 실제 측정값을 씁니다.</p>}
        {view?.advice && (game !== 'lmu' || view.lmu_open_setup) && <AdviceView advice={view.advice} voice={view.voice_enabled} audioReady={view.advice_audio_ready}/>}
        {game !== 'lmu' && view?.ocr_english === false && <Alert>
          <AlertDescription>
            <p>영어 Windows OCR이 없어 {view.ocr_language ? `현재 언어(${view.ocr_language})로 ` : ''}숫자를 읽고 있습니다. 일부 계기판 숫자를 글자로 잘못 읽어 측정이 보류될 수 있습니다.</p>
            <p>OCR 구성요소만 추가하며 Windows 표시 언어는 바뀌지 않습니다. 설치에는 관리자 승인과 인터넷이 필요합니다.</p>
            <div className="flex flex-wrap gap-3 pt-2">
              {view.ocr_install === 'requested'
                ? <><span>설치 창이 끝나면 다시 확인을 눌러 주세요.</span>
                  <Button variant="outline" size="sm" disabled={disabled} onClick={() => apply(undefined, true, '/ocr-language/recheck')}>다시 확인</Button></>
                : <Button variant="outline" size="sm" disabled={disabled} onClick={() => apply(undefined, true, '/ocr-language/install')}>영어 OCR 설치 (관리자 승인)</Button>}
            </div>
          </AlertDescription>
        </Alert>}
        {view?.voice_enabled && view.car_path && view.state_source === null && <Alert>
          <AlertDescription>{view.car_name || '이 차량'}은 대시 색 정보가 없어, 아래 판단 범위를 저장해야 타이어 음성 안내가 나옵니다.</AlertDescription>
        </Alert>}
        {view && <TargetRanges key={`${game}:${view.car_path || ''}:${JSON.stringify(view.targets || {})}`}
          open={Boolean(view.voice_enabled && view.car_path && view.state_source === null)}
          view={view} disabled={disabled} save={body => apply(body, false, '/targets')}/>}
        {(error || view?.error) && <Alert variant="destructive"><AlertDescription>{error || view.error}</AlertDescription></Alert>}
      </>
    </CardContent>
  </Card>;
}
