import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Input } from './ui/input';
import { Switch } from './ui/switch';
import { NativeSelect, NativeSelectOption } from './ui/native-select';
import { Field, FieldGroup, FieldLabel, FieldSet, FieldLegend, FieldDescription } from './ui/field';
import { Alert, AlertDescription } from './ui/alert';

const GOALS = [['abs', 'ABS 개입'], ['throttle', '풀스로틀 위치'], ['release', '브레이크 해제'],
  ['coast', '코스팅'], ['brake', '제동 시작'], ['relift', '스로틀 재리프트'], ['steering', '조향']];
const SCENES = [['before', '진입 전'], ['during', '주행 그래프'], ['result', '통과 결과'], ['missing', '미측정']];
const FIELDS = [['width', '가로 크기 (%)', 75, 200], ['height', '세로 크기 (%)', 75, 200],
  ['x', '가로 위치 (px)', -100000, 100000], ['y', '세로 위치 (px)', -100000, 100000],
  ['content_opacity', '표시 불투명도', 0, 100], ['background_opacity', '배경 불투명도', 0, 100]];

export default function OverlaySettings({ apiBase, visible = true }) {
  const [view, setView] = useState(null);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [notice, setNotice] = useState('');
  const dirty = useRef(false), mounted = useRef(true), mutating = useRef(false);
  const generation = useRef(0);
  const opacity = useRef({ pending: {}, timer: null, flight: null });
  const request = useCallback(async (path = '', method = 'GET', body) => {
    const response = await fetch(`${apiBase}/api/overlay${path}`, body === undefined ? { method }
      : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (response.status === 404 && path === '/visibility') {
      throw new Error('실행 중인 앱 서버에 자동 표시 기능이 없습니다. 앱을 완전히 종료한 뒤 다시 실행해 주세요.');
    }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '오버레이 설정을 적용하지 못했습니다.');
    return data;
  }, [apiBase]);
  useEffect(() => {
    const stream = opacity.current;
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearTimeout(stream.timer); stream.timer = null; stream.pending = {};
    };
  }, []);
  // Keep one native update in flight and merge newer drag values into the next update.
  const flushOpacity = useCallback(() => {
    const stream = opacity.current;
    clearTimeout(stream.timer); stream.timer = null;
    if (stream.flight) return stream.flight;
    if (!Object.keys(stream.pending).length) return Promise.resolve(true);
    const send = async () => {
      while (mounted.current && Object.keys(stream.pending).length) {
        const patch = stream.pending; stream.pending = {};
        try {
          const data = await request('/settings', 'PUT', patch);
          if (mounted.current) { setView(data); setError(''); }
        } catch (failure) {
          stream.pending = {};
          if (mounted.current) setError(failure.message);
          return false;
        }
      }
      return true;
    };
    stream.flight = send().finally(() => { stream.flight = null; });
    return stream.flight;
  }, [request]);
  const slideOpacity = (key, value) => {
    if (mutating.current) return;
    dirty.current = true; generation.current += 1;
    setDraft((current) => ({ ...current, [key]: value })); setNotice('');
    const stream = opacity.current;
    stream.pending[key] = value;
    if (!stream.timer && !stream.flight) stream.timer = setTimeout(flushOpacity, 60);
  };
  useEffect(() => {
    if (!visible) return undefined;
    let cancelled = false;
    const poll = async () => {
      if (mutating.current || opacity.current.timer || opacity.current.flight) return;
      const ticket = generation.current;
      try {
        const data = await request();
        if (cancelled || ticket !== generation.current || mutating.current) return;
        setView(data);
        if (!dirty.current) setDraft(data.settings);
        setConnectionError('');
      } catch (failure) { if (!cancelled) setConnectionError(failure.message); }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [visible, request]);

  const change = async (path, method, body, message = '') => {
    if (mutating.current) return false;
    generation.current += 1;
    mutating.current = true; setBusy(true); setNotice('');
    try {
      // Save, lock and close only after the final slider value reaches the HUD.
      if (!await flushOpacity() || !mounted.current) return false;
      const data = await request(path, method, body);
      if (mounted.current) {
        setView(data);
        if (path !== '/visibility' && path !== '/demo') { setDraft(data.settings); dirty.current = false; }
        setError(''); setNotice(message);
      }
      return true;
    } catch (failure) {
      if (mounted.current) setError(failure.message);
      return false;
    } finally {
      mutating.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const apply = async () => {
    const values = Object.fromEntries(FIELDS.map(([key]) => [key, Number(draft[key])]));
    if (FIELDS.some(([key, , low, high]) => draft[key] === '' || !Number.isInteger(values[key])
      || values[key] < low || values[key] > high)) {
      setError('크기는 75~200%, 불투명도는 0~100% 범위의 정수로 입력하세요.'); return false;
    }
    return change('/settings', 'PUT', values, '미리보기에 적용했습니다. 저장·고정을 누르면 다음 실행에도 유지합니다.');
  };
  const fix = async () => {
    if (view.settings.fixed) {
      await change('/fixed', 'POST', { fixed: false }, '고정을 해제했습니다. 오버레이를 이동하거나 설정을 조절하세요.');
    } else {
      if (dirty.current && !await apply()) return;
      await change('/fixed', 'POST', { fixed: true }, '배치를 저장하고 고정했습니다. 다음 실행에도 그대로 복원합니다.');
    }
  };

  if (!view) return connectionError ? <Alert variant="destructive"><AlertDescription>{connectionError}</AlertDescription></Alert>
    : <p className="text-sm text-muted-foreground">오버레이 설정을 불러오고 있습니다.</p>;
  const previewing = view.preview ?? view.running;
  const autoAvailable = typeof view.auto_show === 'boolean';
  const editable = previewing && view.ready && !view.settings.fixed && !busy;
  return <section aria-label="오버레이 설정" className="flex flex-col gap-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-col gap-2">
        <h3 className="font-semibold">주행 오버레이</h3>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{previewing ? '미리보기 켜짐' : view.visible ? '주행 중 표시' : '주행 대기 · 숨김'}</Badge>
          <Badge variant="outline">{view.settings.fixed ? '설정 고정됨' : '편집 가능'}</Badge>
        </div>
      </div>
      <Button type="button" variant="outline" disabled={busy || !view.supported}
        onClick={() => change('/preview', previewing ? 'DELETE' : 'POST', undefined)}>
        {previewing ? '미리보기 끄기' : '미리보기 켜기'}
      </Button>
    </div>
    <FieldGroup>
      <Field orientation="horizontal" data-disabled={busy || !view.supported || !autoAvailable}>
        <FieldLabel htmlFor="overlay-auto-show" className="flex-1">주행 중 자동 표시</FieldLabel>
        <Switch id="overlay-auto-show" checked={autoAvailable && view.auto_show}
          disabled={busy || !view.supported || !autoAvailable} aria-describedby="overlay-auto-show-help"
          onCheckedChange={(enabled) => change('/visibility', 'PUT', { enabled },
            enabled ? '주행 중 자동 표시를 켰습니다.' : '주행 중 자동 표시를 껐습니다.')} />
      </Field>
      <FieldDescription id="overlay-auto-show-help">{autoAvailable
        ? '차량에 탑승해 트랙에 있을 때 표시합니다. 차량 밖·리플레이·세션 종료·연결 끊김에는 숨깁니다. 잠깐 정차해도 유지합니다.'
        : '실행 중인 앱 서버에 자동 표시 기능이 없습니다. 앱을 완전히 종료한 뒤 다시 실행해 주세요.'}</FieldDescription>
    </FieldGroup>
    <p className="text-sm text-muted-foreground">미리보기는 가상 데이터입니다. 연습 주행에서는 실제 ‘오늘의 한 가지’ 목표·통과 결과·최근 5회와 달성 축하를 표시합니다. 과제에서 선택한 내 랩 또는 Garage61 종합 기준을 그대로 비교하며, 과제가 없으면 현재 조건과 비슷한 Garage61 랩을 찾습니다. 내 입력은 형광 실선, 기준은 옅은 면이며 기준의 다음 2초를 미리 보여줍니다. 맞는 기준이 없으면 내 입력만 표시합니다.</p>
    <p className="text-sm text-muted-foreground">연습·퀄리·레이스의 기본 화면은 Garage61 비교 그래프입니다. 상단 글자 없이 그래프를 넓게 표시하며, 하단에는 같은 기준 대비 직전 코너 시간 차이·제동 시작 위치 차이를 표시합니다. 깨끗한 통과와 저장된 코너 구간 정의가 없거나 측정이 부족하면 —로 표시합니다. 연습 입력 달성과 실제 페이스 개선 확인은 구분합니다.</p>
    <p className="text-sm text-muted-foreground">자동 표시를 켜 두면 세션 연결 후 차량·트랙·노면 조건이 확인되는 대로 기준을 미리 준비합니다. 로컬 Garage61 CSV 캐시를 먼저 확인하고, 맞는 자료가 없으면 다운로드합니다. 차량 밖에서도 기준만 준비하며 오버레이는 숨깁니다.</p>
    <p className="text-sm text-muted-foreground">기본 화면 오른쪽의 큰 숫자는 현재 기어입니다. 같은 트랙 위치의 Garage61 기어보다 낮으면 빨강·같으면 흰색·높으면 초록이며, 비교 자료가 없거나 중립·후진이면 회색으로 표시합니다.</p>
    {view.practice && <p className="text-sm text-muted-foreground" data-testid="overlay-active-practice">
      현재 과제: {view.practice.title} · {view.practice.corner} · {view.practice.recent_ok}/5
      {view.practice.phase === 'WARMUP' ? ' · 워밍업 중' : ''}
    </p>}
    {view.reference && <p role="status" className="text-sm text-muted-foreground">{view.reference.message}
      {view.reference.state === 'READY' && Number.isFinite(view.reference.lap_time_s)
        ? ` · 기준 ${Math.floor(view.reference.lap_time_s / 60)}:${(view.reference.lap_time_s % 60).toFixed(3).padStart(6, '0')}` : ''}
      {view.reference.condition_note ? ` · ${view.reference.condition_note}` : ''}</p>}
    {!view.supported && <p className="text-sm text-muted-foreground">오버레이는 Windows에서 사용할 수 있습니다.</p>}
    <FieldSet>
      <FieldLegend>배치와 표시</FieldLegend>
      <FieldDescription>가로와 세로를 각각 조절합니다. 고정을 해제하면 창의 상단을 끌어 위치를 바꿀 수도 있습니다.</FieldDescription>
      <FieldGroup className="grid md:grid-cols-2 xl:grid-cols-4">
        {FIELDS.slice(0, 4).map(([key, label, min, max]) => <Field key={key} data-disabled={!editable}>
          <FieldLabel htmlFor={`overlay-${key}`}>{label}</FieldLabel>
          <Input id={`overlay-${key}`} type="number" step="1" min={min} max={max} disabled={!editable}
            value={draft[key] ?? ''} onChange={(event) => {
              dirty.current = true; setDraft((current) => ({ ...current, [key]: event.target.value }));
            }} />
        </Field>)}
      </FieldGroup>
      <FieldGroup className="grid md:grid-cols-2">
        {FIELDS.slice(4).map(([key, label, min, max]) => <Field key={key} data-disabled={!editable}>
          <div className="flex items-center justify-between gap-3">
            <FieldLabel htmlFor={`overlay-${key}`}>{label}</FieldLabel>
            <span aria-hidden="true" className="text-sm font-medium tabular-nums">{draft[key] ?? 0}%</span>
          </div>
          <input id={`overlay-${key}`} type="range" step="1" min={min} max={max} disabled={!editable}
            value={draft[key] ?? 0} aria-valuetext={`${draft[key] ?? 0}%`} aria-describedby="overlay-opacity-help"
            className="h-11 w-full cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
            onChange={(event) => slideOpacity(key, Number(event.target.value))} />
        </Field>)}
      </FieldGroup>
      <FieldDescription id="overlay-opacity-help">슬라이더를 움직이면 퍼센트와 미리보기가 바로 바뀝니다. 저장·고정을 누르면 다음 실행에도 유지합니다.</FieldDescription>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={!editable} onClick={apply}>미리보기에 적용</Button>
        <Button type="button" disabled={busy || !previewing || !view.ready} onClick={fix}>
          {view.settings.fixed ? '고정 해제·편집' : '현재 설정 저장·고정'}
        </Button>
      </div>
      <FieldDescription>저장·고정하면 위치·크기·두 불투명도를 잠그고 게임으로 클릭을 통과시킵니다. 창을 닫거나 앱을 다시 실행해도 저장한 배치를 사용합니다.</FieldDescription>
    </FieldSet>
    <details>
      <summary className="cursor-pointer text-sm font-medium">샘플 장면과 축하 효과</summary>
      <FieldGroup className="mt-4 grid md:grid-cols-2">
        <Field data-disabled={!previewing || !view.ready || busy}>
          <FieldLabel htmlFor="overlay-goal">샘플 목표</FieldLabel>
          <NativeSelect id="overlay-goal" value={view.goal} disabled={!previewing || !view.ready || busy}
            onChange={(event) => change('/demo', 'POST', { goal: event.target.value })}>
            {GOALS.map(([value, label]) => <NativeSelectOption key={value} value={value}>{label}</NativeSelectOption>)}
          </NativeSelect>
        </Field>
        <Field data-disabled={!previewing || !view.ready || busy}>
          <FieldLabel htmlFor="overlay-scene">샘플 장면</FieldLabel>
          <NativeSelect id="overlay-scene" value={view.scene} disabled={!previewing || !view.ready || busy}
            onChange={(event) => change('/demo', 'POST', { scene: event.target.value })}>
            {SCENES.map(([value, label]) => <NativeSelectOption key={value} value={value}>{label}</NativeSelectOption>)}
          </NativeSelect>
        </Field>
        <Button type="button" variant="outline" disabled={!previewing || !view.ready || busy}
          onClick={() => change('/demo', 'POST', { celebrate: true })}>5/5 축하 보기</Button>
      </FieldGroup>
    </details>
    {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    {(error || connectionError || view.error) && <Alert variant="destructive"><AlertDescription>{error || connectionError || view.error}</AlertDescription></Alert>}
  </section>;
}
