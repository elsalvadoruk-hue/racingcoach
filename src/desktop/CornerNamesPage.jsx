import React, { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { Field, FieldGroup, FieldLabel } from '../components/ui/field';
import { TrackMap } from './Telemetry';
import { cornerLabel, data } from './sessionData';
import { requestJson } from '../analysis/analysisClient';
import { findOfficialTrackMap } from '../lib/officialTrackMaps';
import { NoteIcon } from './CornerNotes';

export default function CornerNamesPage({ corners, points, names, onSaved, onBack, initialSelected, game = 'iracing', cornerNotes = [] }) {
  // LMU recordings analysed by the shared workspace use the shared corner-number store;
  // their official map is the matching iRacing scan (session.official_track_id).
  const lmu = game === 'lmu' && data.session?.game !== 'lmu';
  const lmuShared = data.session?.game === 'lmu';
  const [referenceMap, setReferenceMap] = useState(null);
  const official = lmu ? referenceMap : lmuShared
    ? (data.session.official_track_id ? findOfficialTrackMap({ track_platform_id: data.session.official_track_id }) : null)
    : findOfficialTrackMap(data.session);
  const layers = official ? ['active', 'pitroad', 'start-finish', 'turns'].map(key => official.assets[key]).filter(Boolean) : [];
  const [selected, setSelected] = useState(initialSelected || corners[0]?.id);
  const [draft, setDraft] = useState(names);
  const [zoom, setZoom] = useState(1);
  const [message, setMessage] = useState('오른쪽 지도에서 자동 구간 번호를 선택하세요.');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [officialRows, setOfficialRows] = useState([]);
  const selection = { filename: data.filename, target_lap: data.defaultTarget,
    reference_lap: data.defaultReference, reference_filename: data.reference_filename || data.filename };
  const endpoint = lmu ? '/api/lmu/corner-names' : '/api/corner-names';
  useEffect(() => {
    const controller = new AbortController();
    if (!data.filename) return undefined;
    setBusy(true);
    const query = new URLSearchParams(lmu ? Object.entries(selection).filter(([, value]) => value != null) : { filename: data.filename });
    requestJson(`${endpoint}?${query}`, { signal: controller.signal }).then(result => {
      const rows = result.corners || [];
      if (lmu) setReferenceMap(result.official_map?.assets ? { ...result.official_map, assets: result.official_map.assets } : null);
      setOfficialRows(rows);
      const next = Object.fromEntries(rows.map(row => [row.id, { number: row.number || '', name: row.name || '', spoken: row.spoken || '' }]));
      setDraft(next); onSaved(next);
      setMessage(result.confirmed ? (lmu || lmuShared ? '확정된 번호입니다. 같은 LMU 트랙의 다른 주행과 실시간 음성에도 적용됩니다.' : '확정된 번호입니다. 수정 후 저장하면 다른 주행과 음성에도 적용됩니다.') : '지도 제안을 확인하거나 직접 번호를 입력한 뒤 확정하세요.');
    }).catch(e => { if (!controller.signal.aborted) setMessage(e.message); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const corner = corners.find(item => item.id === selected);
  const value = draft[selected] || {};
  const change = (key, text) => { setDraft(previous => ({ ...previous, [selected]: { ...previous[selected], [key]: text } })); setDirty(true); setMessage('수정 중 · 저장하면 비교 화면에도 표시됩니다.'); };
  const step = direction => setSelected(corners[(corners.findIndex(c => c.id === selected) + direction + corners.length) % corners.length].id);
  const save = async () => {
    setBusy(true);
    try {
      const result = await requestJson(endpoint, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...(lmu ? selection : { filename: data.filename }), corners: corners.map(c => ({ id: c.id, number: draft[c.id]?.number || '', name: draft[c.id]?.name || '', spoken: draft[c.id]?.spoken || '' })) }) });
      const confirmed = lmu && result.corners ? Object.fromEntries(result.corners.map(row => [row.id, { number: row.number || '', name: row.name || '', spoken: row.spoken || '' }])) : draft;
      setDraft(confirmed); onSaved(confirmed); setDirty(false); setMessage(lmu || lmuShared ? '확정했습니다. 같은 LMU 트랙의 다른 주행과 실시간 음성에서도 이 번호를 사용합니다.' : '확정했습니다. 이 트랙의 다른 주행과 실시간 음성에도 적용됩니다.');
    } catch (e) { setMessage(e.message); }
    finally { setBusy(false); }
  };
  if (!corner) return <main className="names-page"><Button variant="outline" onClick={onBack}>코너 비교로</Button><p>편집할 자동 구간이 없습니다.</p></main>;
  return <main className="names-page">
    <div className="names-heading"><Button variant="outline" onClick={onBack} disabled={dirty}><ArrowLeft data-icon="inline-start" />코너 비교로</Button><div><h1>코너 번호 확인</h1><p>{lmu ? '참조 지도와 기록 주행선을 대조해 번호 또는 이름을 연결하세요.' : '공식 지도를 보며 자동 구간에 번호 또는 이름을 연결하세요.'}</p></div><Badge variant="outline">{data.session.track_name} · {data.session.track_config}</Badge><span>{Object.values(draft).filter(v => v.number || v.name).length} / {corners.length}개 입력</span></div>
    <nav className="corner-note-index" aria-label="코너 목록">{corners.map(item =>
      <Button key={item.id} size="sm" variant={selected === item.id ? 'secondary' : 'ghost'} aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}>
        {item.id} · {cornerLabel(item, draft)}<NoteIcon notes={cornerNotes} pct={item.apex_pct} />
      </Button>)}</nav>
    <div className="names-maps">
      <section className="official-map-panel"><div className="panel-heading"><h2>{lmu ? 'iRacing 참조 지도' : 'iRacing 공식 지도'}</h2><div className="flex gap-1"><Button variant="ghost" size="icon" aria-label="공식 지도 축소" onClick={() => setZoom(z => Math.max(1, z - .5))}><ZoomOut /></Button><Button variant="ghost" size="icon" aria-label="공식 지도 확대" onClick={() => setZoom(z => Math.min(3, z + .5))}><ZoomIn /></Button><Button variant="ghost" size="sm" onClick={() => setZoom(1)}>맞춤</Button></div></div><div className="official-map-scroll">{!official && <p className="empty-data">이 레이아웃의 참조 지도가 없습니다. 오른쪽 주행선을 확인하세요.</p>}<div className="official-layers" style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}>{layers.map((src, i) => <img key={src} src={src} alt={i === 0 ? `iRacing ${data.session.track_name} 트랙 지도` : i === 3 ? '코너 번호 참조' : ''} draggable="false" />)}</div></div><p>iRacing 제공 · 코너 번호 참조용 · 확대 후 스크롤로 이동{lmu ? ' · LMU 구간과 대조 후 확정하세요.' : ''}</p></section>
      <section className="automatic-map-panel"><div className="panel-heading"><h2>{lmu ? '기록 주행선 · 자동 감지 구간' : 'GPS · 자동 감지 구간'}</h2><Badge variant="secondary">{selected} 선택됨</Badge></div><div className="automatic-map-scroll" ><TrackMap points={points} corners={corners} selected={corner} onSelect={setSelected} large names={draft} segmentMode rotatable schematic={lmu} /></div><p>숫자를 누르면 아래에서 편집합니다. 휠로 확대하고 좌클릭 드래그로 이동합니다. Ctrl+좌클릭을 누른 채 위로 움직이면 반시계, 아래로 움직이면 시계 방향으로 회전하며 숫자는 똑바로 유지됩니다.</p></section>
    </div>
    <section className="name-editor" aria-label="선택 구간 이름 편집"><div className="name-editor-heading"><div><strong>{selected}</strong><span>{cornerLabel(corner, draft)}</span><small>진입 {(corner.entry_pct * 100).toFixed(1)}% → 탈출 {(corner.exit_pct * 100).toFixed(1)}%</small></div><Button variant="outline" size="sm" onClick={() => step(-1)}><ArrowLeft data-icon="inline-start" />이전 구간</Button><Button variant="outline" size="sm" onClick={() => step(1)}>다음 구간<ArrowRight data-icon="inline-end" /></Button></div>
      <FieldGroup className="name-fields"><Field><FieldLabel htmlFor="corner-number">코너 번호</FieldLabel><Input id="corner-number" placeholder="예: 5 또는 5-7" maxLength={12} value={value.number || ''} onChange={e => change('number', e.target.value)} /></Field><Field><FieldLabel htmlFor="corner-name">코너 이름</FieldLabel><Input id="corner-name" placeholder="예: 레 콤브" maxLength={60} value={value.name || ''} onChange={e => change('name', e.target.value)} /></Field><Field><FieldLabel htmlFor="corner-spoken">음성으로 부를 말 · 선택</FieldLabel><Input id="corner-spoken" placeholder="예: 레 콤브 진입" maxLength={60} value={value.spoken || ''} onChange={e => change('spoken', e.target.value)} /></Field></FieldGroup>
      <div className="name-save"><Button variant="outline" disabled={busy || !officialRows.some(row => row.suggested_number || row.suggested_name)} onClick={() => {
        setDraft(Object.fromEntries(officialRows.map(row => [row.id, { number: row.suggested_number || '', name: row.suggested_name || '', spoken: '' }]))); setDirty(true);
      }}>공식 지도 제안 적용</Button><p role="status">{message}</p><Button variant="ghost" onClick={() => { setDraft(names); setDirty(false); setMessage('마지막 저장 상태로 되돌렸습니다.'); }} disabled={!dirty}><RotateCcw data-icon="inline-start" />수정 취소</Button><Button onClick={save} disabled={!dirty || busy}><Check data-icon="inline-start" />수정 내용 확정</Button></div>
      <p className="name-hint">자동 구간과 공식 코너는 일대일로 맞지 않을 수 있습니다. 여러 코너가 한 구간이면 “5-7”로 적고, 번호가 없는 곳은 이름만 입력하거나 비워 두세요.</p>
      {lmu && <p className="name-hint">확정한 번호는 LMU 분석 화면에 적용됩니다. 실시간 음성 안내 연결은 준비 중입니다.</p>}
    </section>
  </main>;
}
