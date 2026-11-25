import React, { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronRight, Copy, Gamepad2, Plus, Trash2, Volume2, X } from 'lucide-react';
import { useGame } from '../lib/gameContext';
import { buttonRequest, buttonSocket } from '../useButtonRadio';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from './ui/dialog';
import { Field, FieldLabel } from './ui/field';
import { Input } from './ui/input';
import { NativeSelect } from './ui/native-select';
import { Switch } from './ui/switch';

const presets = [
  ['방금 랩', ['last']], ['앞뒤 간격', ['ahead', 'behind']], ['연료 상태', ['fuel']],
];
const empty = (name = '새 무전 버튼', items = []) => ({
  id: crypto.randomUUID(), name, items, keys: [], enabled: true, scope: 'both',
});
const games = { both: '두 게임 공통', iracing: 'iRacing', lmu: 'LMU' };

function keyboardBinding(e) {
  if (['Control', 'Alt', 'Shift', 'Meta', 'Process', 'Unidentified'].includes(e.key) || e.repeat) return null;
  const vk = e.keyCode;
  if (!vk || vk > 254) return null;
  const mods = Number(e.ctrlKey) + Number(e.altKey) * 2 + Number(e.shiftKey) * 4 + Number(e.metaKey) * 8;
  const name = e.code.startsWith('Numpad') ? e.code : e.key === ' ' ? 'Space' : e.key.length === 1 ? e.key.toUpperCase() : e.key;
  return { code: `key:${vk}:${mods}`, label: [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Win', name].filter(Boolean).join('+') };
}

function conflict(rows, draft) {
  if (!draft?.enabled || !draft.items.length) return null;
  return rows.find(r => r.id !== draft.id && r.enabled && r.items.length && r.scope === draft.scope
    && r.keys.some(k => draft.keys.some(b => k.code === b.code)));
}

const count = value => value.toLocaleString('ko-KR');

function useRadioPreview(apiBase, audio, setNotice, selectedGame) {
  const previewing = useRef(null), stopPreview = useRef(null);
  useEffect(() => () => { previewing.current?.abort(); stopPreview.current?.(); }, []);
  return async function listen(row) {
    if (!row?.items.length) return;
    previewing.current?.abort();
    stopPreview.current?.();
    const controller = new AbortController();
    previewing.current = controller;
    try {
      if (audio?.standby) throw new Error('음성을 재생하는 앱 창에서 미리듣기를 눌러 주세요.');
      if (audio?.applied?.muted || audio?.applied?.volume === 0) throw new Error('음소거 또는 볼륨 설정을 확인하세요.');
      setNotice('예시 음성을 준비하고 있습니다.');
      const game = row.scope && row.scope !== 'both' ? row.scope : selectedGame;
      const result = await buttonRequest(apiBase, '/preview', { items: row.items, game }, controller.signal);
      if (controller.signal.aborted) return;
      const details = result.missing.length ? '준비 음성 없음 · ' + result.missing.join(' / ') : '';
      if (!result.parts.length) throw new Error(details || '재생할 음성이 없습니다.');
      const played = await audio.play(result.parts, 'audio/mpeg', {
        priority: 1, channel: 'pitwall', signal: controller.signal,
        onStatus: status => {
          if (status === 'started') stopPreview.current = () => audio.stopNormal('radio_preview');
          else if (['completed', 'interrupted', 'cancelled', 'failed'].includes(status)) stopPreview.current = null;
        },
      });
      if (!controller.signal.aborted) setNotice(played ? ['예시 · ' + result.spoken.join(' '), details].filter(Boolean).join('  /  ') : '재생 중인 무전과 출력 장치를 확인하세요.');
    } catch (e) { if (!controller.signal.aborted) setNotice(e.message); }
  };
}

function InstalledRecordings({ apiBase, audio }) {
  const [notice, setNotice] = useState('');
  const listen = useRadioPreview(apiBase, audio, setNotice);
  const [group, setGroup] = useState(''), [kind, setKind] = useState('');
  const [corner, setCorner] = useState(''), [query, setQuery] = useState('');
  const [page, setPage] = useState(0), [result, setResult] = useState(null);
  const q = query.trim();
  const params = new URLSearchParams({view: 'library', group, kind, corner: corner || '0', q, offset: page * 40}).toString();
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const data = await buttonRequest(apiBase, '/catalog?' + params, undefined, controller.signal);
        if (!controller.signal.aborted) setResult({params, data});
      } catch (error) {
        if (!controller.signal.aborted) setResult({params, error: error.message});
      }
    }, q ? 150 : 0);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [apiBase, params, q]);
  function navigate(nextGroup, nextKind = '') {
    setGroup(nextGroup); setKind(nextKind); setCorner(''); setPage(0);
  }
  const current = result?.params === params ? result : null;
  const data = current?.data;
  const folders = !kind && !q && data ? Object.entries(group ? data.kinds : data.groups) : [];
  return <>
    {group && <nav aria-label="선택한 음성 분류" className="radio-library-path">
      <Button size="sm" variant="ghost" onClick={() => navigate('')}>전체 상황</Button><ChevronRight aria-hidden="true" />
      {kind ? <><Button size="sm" variant="ghost" onClick={() => navigate(group)}>{group}</Button><ChevronRight aria-hidden="true" /><strong>{kind}</strong></> : <strong>{group}</strong>}
    </nav>}
    <Input aria-label="설치 음성 검색" placeholder={group ? '이 분류에서 문장 검색' : '전체 설치 음성 검색'} value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} />
    {kind && (corner || data?.corners.length > 0) && <NativeSelect size="sm" aria-label="코너 번호" value={corner} onChange={e => { setCorner(e.target.value); setPage(0); }}>
      <option value="">모든 코너</option>{[...new Set([...(data?.corners || []), ...(corner ? [Number(corner)] : [])])].sort((a, b) => a - b).map(n => <option key={n} value={n}>{n}번 코너</option>)}
    </NativeSelect>}
    {!current ? <p role="status">설치 음성을 불러오는 중…</p> : current.error ? <p role="alert">{current.error}</p> : <>
      <h3>{kind || q ? '녹음' : group ? '안내 종류' : '주행 상황'} <span>{count(kind || q ? data.total : folders.length)}</span></h3>
      <div className="radio-button-options" key={params}>
        {folders.map(([label, total]) => <Button size="sm" variant="ghost" key={label} onClick={() => group ? navigate(group, label) : navigate(label)}>
          <span className="mr-auto">{label}</span><span>{count(total)}</span><ChevronRight data-icon="inline-end" />
        </Button>)}
        {data.clips.map(t => <Button key={t.id} size="sm" variant="ghost" aria-label={t.name + ' 듣기'} onClick={() => listen({items: [t.id]})}>
          <Volume2 data-icon="inline-start" />{t.name}</Button>)}
        {!data.total && <p>{q ? '이 문구에 맞는 녹음이 없습니다.' : '이 분류에서 사용할 수 있는 녹음이 없습니다.'}</p>}
      </div>
      {(page > 0 || data.more) && <div className="flex items-center justify-between"><Button size="sm" variant="ghost" disabled={!page} onClick={() => setPage(page - 1)}>이전</Button><span>{page + 1} / {Math.ceil(data.total / 40)}</span><Button size="sm" variant="ghost" disabled={!data.more} onClick={() => setPage(page + 1)}>다음</Button></div>}
    </>}
    {notice && <p role="status">{notice}</p>}
  </>;
}

function Editor({ apiBase, audio }) {
  const { selectedGame } = useGame();
  const [config, setConfig] = useState(null);
  const [selected, setSelected] = useState(null);
  const [edits, setEdits] = useState({});
  const draft = edits[selected] || config?.rows.find(r => r.id === selected);
  const dirty = Boolean(edits[selected]);
  const [topics, setTopics] = useState(null);
  const [topicError, setTopicError] = useState('');
  const [names, setNames] = useState({});
  const [query, setQuery] = useState('');
  const [capture, setCapture] = useState(null);
  const [inputChoices, setInputChoices] = useState([]);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(null);
  const mounted = useRef(true), saving = useRef(false);
  const listen = useRadioPreview(apiBase, audio, setNotice, selectedGame);
  const captured = useRef(null);
  const update = changes => {
    setEdits(old => ({ ...old, [draft.id]: { ...draft, ...changes } }));
    setRemoving(null); setNotice('');
  };
  const select = row => { setSelected(row?.id || null); setRemoving(null); setCapture(null); setNotice(''); };
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    buttonRequest(apiBase, '', undefined, controller.signal).then(data => {
      setConfig(data); setNames(old => ({ ...old, ...data.names })); setSelected(data.rows[0]?.id);
    }).catch(e => { if (!controller.signal.aborted) setNotice(e.message); });
    return () => { mounted.current = false; controller.abort(); };
  }, [apiBase]);
  useEffect(() => {
    const controller = new AbortController();
    setTopics(null); setTopicError('');
    buttonRequest(apiBase, '/catalog?view=topics', undefined, controller.signal)
        .then(data => { if (controller.signal.aborted) return;
          setTopics(data.topics.filter(t => t.ready));
          setNames(old => ({ ...old, ...Object.fromEntries(data.topics.map(t => [t.id, t.name])) })); })
        .catch(e => { if (!controller.signal.aborted) setTopicError(e.message); });
    return () => controller.abort();
  }, [apiBase]);

  async function persist(rows, next, {clear, quick, transferred} = {}) {
    if (saving.current || !config) return;
    saving.current = true; setBusy(true); setCapture(null);
    try {
      const result = await buttonRequest(apiBase, '', { rows, revision: config.revision });
      if (mounted.current) {
        setConfig(result);
        setEdits(old => {
          const remaining = { ...old };
          delete remaining[clear];
          if (transferred) {
            for (const r of Object.values(remaining)) {
              if (r.scope === transferred.scope) remaining[r.id] = { ...r, keys: r.keys.filter(k => !transferred.keys.some(b => k.code === b.code)) };
            }
          }
          return remaining;
        });
        select(next); setNotice('저장했습니다.');
        if (quick) setCapture('quick');
      }
    } catch (e) { if (mounted.current) setNotice(e.message); }
    finally { saving.current = false; if (mounted.current) setBusy(false); }
  }
  function commit(row, transfer = false) {
    const used = conflict(config.rows, row);
    if (used && !transfer) { setNotice(`‘${used.name}’에 연결된 키입니다.`); return; }
    const rows = config.rows.map(r => {
      if (r.id === row.id) return row;
      if (transfer && r.scope === row.scope) return { ...r, keys: r.keys.filter(k => !row.keys.some(b => b.code === k.code)) };
      return r;
    });
    persist(rows, row, {clear: row.id, transferred: transfer && row});
  }
  function start(name, items, quick = false) {
    const row = empty(name, items);
    persist([...config.rows, row], row, {quick});
  }
  function addEmpty() {
    const used = new Set(config.rows.map(r => (edits[r.id] || r).name));
    let n = 1;
    while (used.has('무전 버튼 ' + n)) n++;
    start('무전 버튼 ' + n, []);
  }
  function remove(row) {
    if (removing !== row.id) { setRemoving(row.id); return; }
    const rows = config.rows.filter(r => r.id !== row.id);
    const next = selected === row.id ? rows[Math.min(config.rows.indexOf(row), rows.length - 1)] : draft;
    persist(rows, next, {clear: row.id});
  }
  captured.current = binding => {
    if (capture === 'test') {
      const candidates = config.rows.filter(r => r.enabled
        && r.items.some(item => item !== 'energy_remaining' || selectedGame === 'lmu')
        && r.keys.some(k => k.code === binding.code));
      const row = candidates.find(r => r.scope === selectedGame) || candidates.find(r => r.scope === 'both');
      setCapture(null);
      if (row) { select(row); listen(row); } else setNotice(`${binding.label}에 연결된 무전이 없습니다.`);
      return;
    }
    const keys = capture === 'add' ? draft.keys.filter(k => k.code !== binding.code) : [];
    const next = { ...draft, keys: [...keys, binding] };
    update({keys: next.keys}); setCapture(null);
    if (capture === 'quick' && !conflict(config.rows, next)) commit(next);
  };
  useEffect(() => {
    if (!capture) return undefined;
    setInputChoices([]);
    let done = false, timer;
    const inputs = new Map();
    const accept = binding => {
      if (done) return;
      inputs.set(binding.code, binding);
      if (inputs.size === 1 && !timer) {
        // A wheel mapper can report both the HID button and a keyboard shortcut.
        timer = setTimeout(() => {
          done = true;
          if (inputs.size === 1) captured.current(binding);
          else setInputChoices([...inputs.values()]);
        }, 160);
      }
    };
    const keydown = e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); setCapture(null); return; }
      if (done) return;
      e.preventDefault(); e.stopImmediatePropagation();
      const binding = keyboardBinding(e);
      if (binding) accept(binding);
    };
    window.addEventListener('keydown', keydown, true);
    const ws = buttonSocket(apiBase, true);
    ws.onmessage = ({ data }) => {
      const event = JSON.parse(data);
      if (event.code) {
        const [, device, value] = event.code.split(':');
        const control = value[0] === 'b' ? '버튼 ' + value.slice(1) : '방향 ' + (Number(value.slice(1)) + 1);
        accept({ code: event.code, label: `휠 ${control} · ${device.slice(-4)}` });
      } else if (event.error) setNotice(event.error);
    };
    ws.onerror = () => { if (!done) setNotice('휠 입력 연결을 확인하세요. 키보드로도 등록할 수 있습니다.'); };
    return () => {
      done = true;
      clearTimeout(timer);
      window.removeEventListener('keydown', keydown, true);
      if (ws.readyState === WebSocket.CONNECTING) ws.onopen = () => ws.close();
      else ws.close();
    };
  }, [capture, apiBase]);

  const nameOf = key => names[key] || (key.startsWith('clip:') ? '설치 음성 · 팩 다시 선택 또는 교체' : key);
  const move = (index, shift) => {
    const items = [...draft.items]; [items[index], items[index + shift]] = [items[index + shift], items[index]];
    update({ items });
  };
  const clash = config && conflict(config.rows, draft);
  const editGame = draft && draft.scope !== 'both' ? draft.scope : selectedGame;
  const options = (topics || []).filter(t => (!t.game || t.game === editGame)
    && (t.name.includes(query) || t.group.includes(query)));
  const quickOptions = presets.filter(([, items]) => items.every(id => topics?.some(t => t.id === id)));
  const choose = opt => {
    if (!config || busy) return;
    setNames(old => ({...old, [opt.id]: opt.name}));
    if (!draft) { start(opt.name.slice(0, 80), [opt.id]); return; }
    update({ items: [...draft.items, opt.id] });
  };
  return <fieldset className="radio-button-settings" disabled={busy || !config}>
    <div className="flex flex-wrap items-center gap-2">
      {quickOptions.length > 0 && <span className="text-sm">빠른 등록</span>}
      {quickOptions.map(([name, items]) => <Button key={name} size="sm" variant="outline" onClick={() => start(name, items, true)}>{name}</Button>)}
      <Button size="sm" variant="ghost" className="ml-auto" disabled={!config?.rows.length || busy} onClick={() => setCapture('test')}>저장한 키 테스트</Button>
    </div>
    {capture && <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border p-2">
      <strong>{inputChoices.length ? '함께 감지된 입력 중 하나를 선택하세요.' : (capture === 'test' ? '테스트할 키' : '연결할 키 또는 휠 버튼') + '를 눌러 주세요.'}</strong>
      {inputChoices.map(binding => <Button key={binding.code} size="sm" onClick={() => captured.current(binding)}>{binding.label}</Button>)}
      <Button className="ml-auto" size="sm" variant="outline" onClick={() => setCapture(null)}>입력 취소</Button>
    </div>}
    <div className="radio-button-columns">
      <section aria-label="저장한 버튼" className="radio-button-list">
        <h3>내 버튼 {config?.rows.length ?? ''}</h3>
        <Button size="sm" variant="outline" onClick={addEmpty}><Plus data-icon="inline-start" />버튼 추가</Button>
        <div className="radio-button-rows">{config?.rows.map(saved => {
          const r = edits[saved.id] || saved;
          return <div key={r.id} className="flex items-center gap-1">
            <Button variant={selected === r.id ? 'secondary' : 'ghost'} aria-pressed={selected === r.id} className="radio-button-row min-w-0 flex-1" onClick={() => select(r)}>
              <strong>{r.name || saved.name}{!r.enabled && ' · 꺼짐'}</strong><span>{r.keys.map(k => k.label).join(' / ') || '키 미지정'} · {r.items.length}개</span>
              {edits[r.id] && <span>저장 전</span>}
            </Button>
            <Button size={removing === r.id ? 'sm' : 'icon'} variant="ghost" aria-label={r.name + (removing === r.id ? ' 삭제 확인' : ' 삭제')} onClick={() => remove(saved)}>{removing === r.id ? '삭제 확인' : <Trash2 />}</Button>
          </div>;
        })}</div>
      </section>
      <section aria-label="버튼 편집" className="radio-button-edit">
        {draft ? <>
          <div className="flex items-end gap-2"><Field><FieldLabel htmlFor="radio-button-name">버튼 이름</FieldLabel><Input id="radio-button-name" maxLength={80} value={draft.name} onChange={e => update({ name: e.target.value })} /></Field>
            <NativeSelect aria-label="적용 게임" value={draft.scope} onChange={e => update({ scope: e.target.value })}>{Object.entries(games).map(([id, label]) =>
              <option key={id} value={id} disabled={id === 'iracing' && draft.items.includes('energy_remaining')}>{label}</option>)}</NativeSelect>
            <Switch aria-label="버튼 사용" checked={draft.enabled} onCheckedChange={enabled => update({ enabled })} /></div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => setCapture('replace')}><Gamepad2 data-icon="inline-start" />{draft.keys.length ? '키 변경' : '키 연결'}</Button>
            {draft.keys.map(k => <Button size="sm" variant="ghost" key={k.code} aria-label={k.label + ' 연결 해제'} onClick={() => update({ keys: draft.keys.filter(v => v !== k) })}>{k.label}<X data-icon="inline-end" /></Button>)}
            {draft.keys.length > 0 && <Button size="sm" variant="ghost" onClick={() => setCapture('add')}>다른 키도 연결</Button>}
          </div>
          {draft.items.length > 0 && draft.keys.some(k => k.code.startsWith('key:119:')) && <p>이 버튼을 켜면 연결한 F8 조합으로 무전을 요청합니다. 연결을 해제하면 마이크 질문에 다시 쓸 수 있습니다.</p>}
          <h3>질문 순서</h3>
          {!draft.items.length && <p>버튼을 눌렀을 때 물어볼 질문을 선택하세요.</p>}
          {draft.items.map((key, index) => <div key={index} className="flex items-center gap-1"><span className="mr-auto">{index + 1}. {nameOf(key)}</span>
            <Button size="icon" variant="ghost" aria-label={nameOf(key) + ' 위로'} disabled={!index} onClick={() => move(index, -1)}><ArrowUp /></Button>
            <Button size="icon" variant="ghost" aria-label={nameOf(key) + ' 아래로'} disabled={index + 1 === draft.items.length} onClick={() => move(index, 1)}><ArrowDown /></Button>
            <Button size="icon" variant="ghost" aria-label={nameOf(key) + ' 빼기'} onClick={() => update({ items: draft.items.filter((_, i) => i !== index) })}><X /></Button>
          </div>)}
        </> : <p>무전을 추가하거나 저장한 버튼을 선택하세요.</p>}
      </section>
      <section aria-label="등록할 질문" className="radio-button-library">
          <h3>등록할 질문</h3>
          <Input aria-label="질문 검색" placeholder="코너, 연료, 간격 등으로 검색" value={query} onChange={e => setQuery(e.target.value)} />
          <div className="radio-button-options">{[...new Set(options.map(t => t.group))].map(group => <React.Fragment key={group}>
            <h4 className="text-sm font-semibold">{group}</h4>{options.filter(t => t.group === group).map(t => <Button key={t.id} size="sm" variant="ghost" onClick={() => choose(t)}>
              <Plus data-icon="inline-start" />{t.name}
            </Button>)}
          </React.Fragment>)}{!options.length && <p role="status">{topicError || (topics === null ? '질문에 필요한 음원을 확인하는 중…'
            : topics.length ? '검색 결과가 없습니다.' : '설치된 음성으로 답할 수 있는 질문이 없습니다. 설정 → 업데이트 → 빠진 음성 받기를 확인하세요.')}</p>}</div>
      </section>
    </div>
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" disabled={!draft?.items.length || busy} onClick={() => listen(draft)}><Volume2 data-icon="inline-start" />예시로 듣기</Button>
      <Button size="sm" variant="ghost" disabled={!draft || busy} onClick={() => {
        const copy = {...draft, id: crypto.randomUUID(), name: (draft.name + ' 복사').slice(0, 80), keys: []};
        persist([...config.rows, copy], copy);
      }}><Copy data-icon="inline-start" />복제</Button>
      {clash && <span className="text-sm">‘{clash.name}’에서 사용 중</span>}
      <Button className="ml-auto" disabled={busy || !dirty || !draft?.name.trim()} onClick={() => commit(draft, Boolean(clash))}>{clash ? '키 옮기고 저장' : '저장'}</Button>
    </div>
  </fieldset>;
}

export default function RadioButtonSettings({ apiBase, audio }) {
  const [open, setOpen] = useState(false);
  const [listenOpen, setListenOpen] = useState(false);
  return <><Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><Button size="sm" variant="outline"><Gamepad2 data-icon="inline-start" />무전 버튼 설정</Button></DialogTrigger>
    <DialogContent className="radio-button-dialog">
      <DialogHeader><DialogTitle>무전 버튼 설정</DialogTitle><DialogDescription>버튼에 질문을 담으세요. 주행 중 누르면 그때의 기록으로 답합니다.</DialogDescription></DialogHeader>
      {open && <Editor apiBase={apiBase} audio={audio} />}
    </DialogContent>
  </Dialog>
    <Dialog open={listenOpen} onOpenChange={setListenOpen}>
      <DialogTrigger asChild><Button size="sm" variant="outline"><Volume2 data-icon="inline-start" />설치 음성 듣기</Button></DialogTrigger>
      <DialogContent className="radio-listen-dialog">
        <DialogHeader><DialogTitle>설치 음성 듣기</DialogTitle><DialogDescription>주행 상황과 안내 종류를 고르거나 녹음된 문장을 검색하세요.</DialogDescription></DialogHeader>
        {listenOpen && <InstalledRecordings apiBase={apiBase} audio={audio} />}
      </DialogContent>
    </Dialog></>;
}
