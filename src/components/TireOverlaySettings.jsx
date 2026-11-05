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
  ['temp_low_c', '온도 하한 (°C)', 1, -50, 250], ['temp_high_c', '온도 상한 (°C)', 1, -50, 250]];

function TargetRanges({ view, disabled, save }) {
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
  return <details className="text-sm">
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
        </div>
        </div>
        </div>
        {view && <TargetRanges key={`${game}:${view.car_path || ''}:${JSON.stringify(view.targets || {})}`}
          view={view} disabled={disabled} save={body => apply(body, false, '/targets')}/>}
        {(error || view?.error) && <Alert variant="destructive"><AlertDescription>{error || view.error}</AlertDescription></Alert>}
      </>
    </CardContent>
  </Card>;
}
